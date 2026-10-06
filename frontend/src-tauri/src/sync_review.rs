//! Conflict text comes from verified local handles and immutable cached revisions.
use crate::{
    sync_inbox::RemoteRevision,
    workspace::{hash, Entry, HostError, Result, Workspace},
};
use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
use cap_std::fs::OpenOptions;
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    io::Read,
    path::{Path, PathBuf},
};

const PREVIEW_BYTES: usize = 64 * 1024;
const MAX_BYTES: u64 = 100 * 1024 * 1024;
pub(crate) const SCHEMA: &str = "CREATE TABLE IF NOT EXISTS sync_review_receipts (
    binding TEXT NOT NULL, sequence INTEGER NOT NULL, fingerprint TEXT NOT NULL,
    expected TEXT NOT NULL, choice TEXT NOT NULL, destination TEXT NOT NULL,
    guards TEXT NOT NULL,
    PRIMARY KEY(binding,sequence));";

#[derive(Clone, Debug, Serialize)]
pub struct Content {
    pub exists: bool,
    pub hash: String,
    pub byte_size: u64,
    pub text: Option<String>,
    pub preview_bytes: usize,
    pub truncated: bool,
}
impl Content {
    fn absent() -> Self {
        Self {
            exists: false,
            hash: String::new(),
            byte_size: 0,
            text: Some(String::new()),
            preview_bytes: 0,
            truncated: false,
        }
    }
}
#[derive(Debug, Serialize)]
pub struct RelatedContent {
    pub path: String,
    pub file_id: Option<String>,
    pub content: Content,
}
#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct ReviewGuard {
    pub path: String,
    pub file_id: Option<String>,
    pub hash: String,
}
#[derive(Debug, Serialize)]
pub struct ConflictReview {
    pub vault_id: String,
    pub binding_id: String,
    pub sequence: i64,
    pub file_id: String,
    pub local_path: String,
    pub local_file_id: Option<String>,
    pub remote: RemoteRevision,
    pub local: Content,
    pub incoming: Content,
    pub base: Option<Content>,
    pub related: Vec<RelatedContent>,
    pub fingerprint: String,
}

fn textual(path: &str) -> bool {
    Path::new(path)
        .extension()
        .and_then(|v| v.to_str())
        .is_some_and(|v| {
            matches!(
                v.to_ascii_lowercase().as_str(),
                "md" | "txt" | "json" | "csv" | "py" | "canvas"
            )
        })
}
fn unsafe_file() -> HostError {
    HostError::new("SYNC_REVIEW_UNSAFE_FILE")
}

fn read(root: &Path, relative: &str, text: bool, missing_ok: bool) -> Result<Content> {
    let mut parent = crate::experiment_input::open_root(root).map_err(|_| unsafe_file())?;
    let mut parts = relative.split('/').peekable();
    while let Some(part) = parts.next() {
        if part.is_empty() || part == "." || part == ".." {
            return Err(unsafe_file());
        }
        if parts.peek().is_some() {
            parent = match parent.open_dir_nofollow(part) {
                Ok(parent) => parent,
                Err(error) if missing_ok && error.kind() == std::io::ErrorKind::NotFound => {
                    return Ok(Content::absent())
                }
                Err(error) => return Err(error.into()),
            };
            continue;
        }
        let mut options = OpenOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .maybe_dir(true);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NONBLOCK);
        }
        #[cfg(windows)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.share_mode(windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ);
        }
        let mut file = match parent.open_with(part, &options) {
            Ok(file) => file.into_std(),
            Err(error) if missing_ok && error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(Content::absent())
            }
            Err(error) => return Err(error.into()),
        };
        let before = file.metadata()?;
        if !before.is_file() || before.len() > MAX_BYTES {
            return Err(unsafe_file());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if before.nlink() != 1 {
                return Err(unsafe_file());
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::Storage::FileSystem::*;
            let mut info = BY_HANDLE_FILE_INFORMATION::default();
            if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
                || info.nNumberOfLinks != 1
                || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            {
                return Err(unsafe_file());
            }
        }
        let mut buffer = [0u8; 64 * 1024];
        let mut prefix = Vec::new();
        let mut digest = Sha256::new();
        let mut size = 0u64;
        loop {
            let count = file.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            size += count as u64;
            if size > MAX_BYTES {
                return Err(unsafe_file());
            }
            digest.update(&buffer[..count]);
            if text && prefix.len() < PREVIEW_BYTES {
                let take = count.min(PREVIEW_BYTES - prefix.len());
                prefix.extend_from_slice(&buffer[..take]);
            }
        }
        let after = file.metadata()?;
        if size != before.len()
            || after.len() != before.len()
            || after.modified()? != before.modified()?
        {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        let text = if text {
            match std::str::from_utf8(&prefix) {
                Ok(value) => Some(value.to_owned()),
                Err(error) if error.error_len().is_none() && size > prefix.len() as u64 => Some(
                    std::str::from_utf8(&prefix[..error.valid_up_to()])
                        .map_err(|_| unsafe_file())?
                        .to_owned(),
                ),
                Err(_) => None,
            }
        } else {
            None
        };
        let preview_bytes = text.as_ref().map_or(0, |value| value.len());
        return Ok(Content {
            exists: true,
            hash: format!("{:x}", digest.finalize()),
            byte_size: size,
            truncated: text.is_some() && size > preview_bytes as u64,
            text,
            preview_bytes,
        });
    }
    Err(unsafe_file())
}

impl Workspace {
    fn sync_review_spool(&self, digest: &str) -> Result<PathBuf> {
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
        {
            return Err(HostError::new("SYNC_HASH_INVALID"));
        }
        // Reviewing never creates or repairs the cache. The handle reader
        // validates every existing directory and file without following links.
        Ok(self.root.join(".ainote/sync-spool").join(digest))
    }
    fn sync_review_file_id(&self, path: &str) -> Result<Option<String>> {
        Ok(self
            .entry(path)?
            .filter(|entry| !entry.deleted && !entry.is_folder)
            .map(|entry| entry.file_id))
    }
    fn sync_review_cached(&self, digest: &str, size: u64, path: &str) -> Result<Content> {
        self.sync_review_spool(digest)?;
        let value = read(
            &self.root,
            &format!(".ainote/sync-spool/{digest}"),
            textual(path),
            false,
        )?;
        if value.hash != digest || value.byte_size != size {
            return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
        }
        Ok(value)
    }
    fn sync_review_base(
        &self,
        binding: &str,
        revision: &RemoteRevision,
    ) -> Result<Option<Content>> {
        if revision.base_revision == 0 {
            return Ok(None);
        }
        let previous: Option<String> = self.db.query_row(
            "SELECT revision FROM sync_inbox WHERE binding=?1 AND sequence=?2 AND (state='applied' OR (state='conflict' AND EXISTS(SELECT 1 FROM sync_resolutions r WHERE r.binding=sync_inbox.binding AND r.sequence=sync_inbox.sequence AND r.state='completed' AND r.choice IN ('remote','copy'))))",
            params![binding,revision.base_revision],|row|row.get(0)).optional()?;
        let candidate = if let Some(encoded) = previous {
            let old: RemoteRevision = serde_json::from_str(&encoded)
                .map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
            if old.file_id != revision.file_id || old.sequence != revision.base_revision {
                return Ok(None);
            }
            old.validate(
                &self
                    .sync_binding()?
                    .ok_or_else(|| HostError::new("SYNC_NOT_BOUND"))?,
            )?;
            if old.operation == "delete" {
                return Ok(Some(Content::absent()));
            }
            Some((
                old.hash
                    .ok_or_else(|| HostError::new("SYNC_RESPONSE_INVALID"))?,
                old.size as u64,
                old.path,
            ))
        } else {
            self.db.query_row("SELECT hash,size,path FROM sync_jobs WHERE binding=?1 AND file_id=?2 AND remote_revision=?3 AND state='acked' AND operation='put' LIMIT 1",
                params![binding,revision.file_id,revision.base_revision],|row|Ok((row.get::<_,String>(0)?,row.get::<_,u64>(1)?,row.get::<_,String>(2)?))).optional()?
        };
        let Some((digest, size, path)) = candidate else {
            return Ok(None);
        };
        // A missing cache means two-way review, never an invented common base.
        if !self.sync_review_spool(&digest)?.try_exists()? {
            return Ok(None);
        }
        self.sync_review_cached(&digest, size, &path).map(Some)
    }
    pub fn sync_conflict_review(&self, binding: &str, sequence: i64) -> Result<ConflictReview> {
        self.check_binding(binding)?;
        let row: Option<(String,String)> = self.db.query_row(
            "SELECT local_path,remote FROM sync_conflicts WHERE binding=?1 AND sequence=?2 AND state='open'",
            params![binding,sequence],|row|Ok((row.get(0)?,row.get(1)?))).optional()?;
        let (local_path, encoded) = row.ok_or_else(|| HostError::new("SYNC_CONFLICT_CHANGED"))?;
        let remote: RemoteRevision =
            serde_json::from_str(&encoded).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
        let binding_record = self
            .sync_binding()?
            .ok_or_else(|| HostError::new("SYNC_NOT_BOUND"))?;
        remote.validate(&binding_record)?;
        let head: i64 = self.db.query_row(
            "SELECT revision FROM sync_heads WHERE binding=?1 AND file_id=?2",
            params![binding, remote.file_id],
            |row| row.get(0),
        )?;
        if remote.sequence != sequence
            || head != sequence
            || !self.sync_path_enabled(&local_path)?
        {
            return Err(HostError::new("SYNC_CONFLICT_CHANGED"));
        }
        self.resolve(&local_path)?;
        let local = read(&self.root, &local_path, textual(&local_path), true)?;
        let local_file_id = self.sync_review_file_id(&local_path)?;
        let mut related = Vec::new();
        for path in [
            self.path_for_id(&remote.file_id).ok(),
            Some(remote.path.clone()),
        ]
        .into_iter()
        .flatten()
        {
            if path == local_path
                || related
                    .iter()
                    .any(|item: &RelatedContent| item.path == path)
            {
                continue;
            }
            self.resolve(&path)?;
            if !self.sync_path_enabled(&path)? {
                return Err(HostError::new("SYNC_SCOPE_DISABLED"));
            }
            related.push(RelatedContent {
                file_id: self.sync_review_file_id(&path)?,
                content: read(&self.root, &path, textual(&path), true)?,
                path,
            });
        }
        let incoming = if let Some(digest) = &remote.hash {
            self.sync_review_cached(digest, remote.size as u64, &remote.path)?
        } else {
            Content::absent()
        };
        let same_identity = self
            .entry(&local_path)?
            .is_some_and(|entry| entry.file_id == remote.file_id && !entry.is_folder);
        let base = if same_identity {
            self.sync_review_base(binding, &remote)?
        } else {
            None
        };
        let mut review = ConflictReview {
            vault_id: self.vault_id.clone(),
            binding_id: binding.to_owned(),
            sequence,
            file_id: remote.file_id.clone(),
            local_path,
            local_file_id,
            remote,
            local,
            incoming,
            base,
            related,
            fingerprint: String::new(),
        };
        review.fingerprint = hash(
            &serde_json::to_vec(&review).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?,
        );
        Ok(review)
    }
    pub fn sync_resolve_reviewed(
        &mut self,
        binding: &str,
        sequence: i64,
        choice: &str,
        destination: &str,
        expected: &str,
        fingerprint: &str,
    ) -> Result<()> {
        self.check_binding(binding)?;
        let frozen: bool = self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM sync_resolutions WHERE binding=?1 AND sequence=?2)",
            params![binding, sequence],
            |row| row.get(0),
        )?;
        if frozen {
            let saved: Option<(String,String,String,String)> = self.db.query_row(
                "SELECT fingerprint,expected,choice,destination FROM sync_review_receipts WHERE binding=?1 AND sequence=?2",
                params![binding,sequence],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?))).optional()?;
            if saved.as_ref()
                != Some(&(
                    fingerprint.to_owned(),
                    expected.to_owned(),
                    choice.to_owned(),
                    destination.to_owned(),
                ))
            {
                return Err(HostError::new("SYNC_RESOLUTION_CHANGED"));
            }
        } else {
            let review = self.sync_conflict_review(binding, sequence)?;
            if review.fingerprint != fingerprint || review.local.hash != expected {
                return Err(HostError::new("SYNC_CONFLICT_CHANGED"));
            }
            // Before any file changes, persist the exact reviewed decision.
            // If publication fails no resolution is created. A lost reply can
            // resume the original operation; it cannot approve another choice.
            let guards: Vec<ReviewGuard> = std::iter::once(ReviewGuard {
                path: review.local_path,
                file_id: review.local_file_id,
                hash: review.local.hash,
            })
            .chain(review.related.into_iter().map(|item| ReviewGuard {
                path: item.path,
                file_id: item.file_id,
                hash: item.content.hash,
            }))
            .collect();
            let guards = serde_json::to_string(&guards)
                .map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
            self.db.execute("INSERT INTO sync_review_receipts VALUES (?1,?2,?3,?4,?5,?6,?7) ON CONFLICT(binding,sequence) DO UPDATE SET fingerprint=excluded.fingerprint,expected=excluded.expected,choice=excluded.choice,destination=excluded.destination,guards=excluded.guards",
                params![binding,sequence,fingerprint,expected,choice,destination,guards])?;
        }
        self.sync_resolve(binding, sequence, choice, destination, expected)
    }

    /// Only this decision's committed journal phases may change the reviewed
    /// paths. A partial retry must still protect every later human edit.
    pub(crate) fn sync_check_review_paths(
        &self,
        binding: &str,
        sequence: i64,
        retire: &str,
        rename: &str,
    ) -> Result<Option<Vec<ReviewGuard>>> {
        let encoded: Option<String> = self
            .db
            .query_row(
                "SELECT guards FROM sync_review_receipts WHERE binding=?1 AND sequence=?2",
                params![binding, sequence],
                |row| row.get(0),
            )
            .optional()?;
        let Some(encoded) = encoded else {
            return Ok(None);
        }; // Existing legacy decisions.
        let mut guards: Vec<ReviewGuard> = serde_json::from_str(&encoded)
            .map_err(|_| HostError::new("SYNC_RESOLUTION_CHANGED"))?;
        for id in [retire, rename] {
            let Some(value) = self
                .operation(id)?
                .filter(|value| value["state"] == "committed")
            else {
                continue;
            };
            let entry: Entry = serde_json::from_value(value["result"].clone())
                .map_err(|_| HostError::new("SYNC_RESOLUTION_CHANGED"))?;
            let expected = value["result"]["expected"]
                .as_str()
                .ok_or_else(|| HostError::new("SYNC_RESOLUTION_CHANGED"))?;
            for guard in &mut guards {
                if guard.file_id.as_deref() == Some(&entry.file_id) {
                    guard.file_id = None;
                    guard.hash.clear();
                }
            }
            if !entry.deleted {
                for guard in &mut guards {
                    if guard.path == entry.path || self.paths_alias(&guard.path, &entry.path)? {
                        guard.file_id = Some(entry.file_id.clone());
                        guard.hash = expected.to_owned();
                    }
                }
            }
        }
        for guard in &guards {
            self.resolve(&guard.path)?;
            let current = read(&self.root, &guard.path, false, true)?;
            let actual_id = self.sync_review_file_id(&guard.path)?;
            let same_id = actual_id == guard.file_id
                || (actual_id.is_none()
                    && guard.file_id.as_deref().is_some_and(|id| {
                        self.path_for_id(id)
                            .and_then(|path| self.paths_alias(&guard.path, &path))
                            .unwrap_or(false)
                    }));
            if current.hash != guard.hash || !same_id {
                return Err(HostError::new("SYNC_CONFLICT_CHANGED"));
            }
        }
        Ok(Some(guards))
    }
}
