//! 根据已提交的快照协调外部编辑的文件，而不是根据 UI 读取缓存。
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use std::{
    collections::{HashMap, HashSet},
    fs,
    path::Path,
};
use uuid::Uuid;
/// 仅文件传输。逻辑记录单独接收自己的版本白名单。
pub fn allowed(path: &str) -> bool {
    if crate::records::is_record(path) {
        return crate::records::allowed(path);
    }
    let parts: Vec<_> = path.split('/').collect();
    if parts.iter().any(|part| {
        part.starts_with('.')
            || matches!(
                part.to_ascii_lowercase().as_str(),
                "node_modules" | "target" | "__pycache__" | "cache" | "logs" | "models"
            )
    }) {
        return false;
    }
    let extension = Path::new(path)
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    matches!(
        extension.as_str(),
        "md" | "canvas" | "png" | "jpg" | "jpeg" | "gif" | "webp"
    ) || (parts
        .first()
        .is_some_and(|v| v.eq_ignore_ascii_case("attachments"))
        && matches!(
            extension.as_str(),
            "png"
                | "jpg"
                | "jpeg"
                | "gif"
                | "webp"
                | "svg"
                | "pdf"
                | "mp3"
                | "wav"
                | "m4a"
                | "ogg"
                | "flac"
                | "mp4"
                | "webm"
                | "mov"
                | "txt"
                | "csv"
                | "docx"
                | "xlsx"
                | "pptx"
                | "bin"
        ))
}
impl Workspace {
    pub(crate) fn sync_paths(&self) -> Result<Vec<String>> {
        fn walk(ws: &Workspace, directory: &Path, paths: &mut Vec<String>) -> Result<()> {
            for item in fs::read_dir(directory)? {
                let item = item?;
                let name = item.file_name().to_string_lossy().into_owned();
                if name.starts_with('.')
                    || matches!(
                        name.to_ascii_lowercase().as_str(),
                        "node_modules" | "target" | "__pycache__" | "cache" | "logs" | "models"
                    )
                {
                    continue;
                }
                let path = item
                    .path()
                    .strip_prefix(&ws.root)
                    .map_err(|_| HostError::new("UNSAFE_PATH"))?
                    .to_string_lossy()
                    .replace('\\', "/");
                let resolved = ws.resolve(&path)?;
                if item.file_type()?.is_dir() {
                    walk(ws, &resolved, paths)?;
                } else if ws.sync_path_enabled(&path)? && item.file_type()?.is_file() {
                    paths.push(path);
                }
            }
            Ok(())
        }
        let mut paths = Vec::new();
        walk(self, &self.root, &mut paths)?;
        paths.sort();
        Ok(paths)
    }
    pub fn sync_discover(&mut self, binding: &str) -> Result<usize> {
        self.check_binding(binding)?;
        self.refresh_snapshot(false)?;
        let paths = self.sync_paths()?;
        let mut seen = HashSet::new();
        for path in &paths {
            if let Some(entry) = self.entry(path)?.filter(|entry| !entry.deleted) {
                seen.insert(entry.file_id);
            }
        }
        // Queue the retired occupant's deletion before the replacement's put.
        // The observed path is the last public path, even when identity
        // reconciliation moved a tombstone into internal metadata storage.
        let previous = {
            let mut statement = self
                .db
                .prepare("SELECT file_id,path FROM sync_observed WHERE deleted=0")?;
            let rows = statement
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            rows
        };
        let mut deletions = Vec::new();
        for (file_id, path) in &previous {
            if seen.contains(file_id) || !self.sync_path_enabled(path)? {
                continue;
            }
            if self.resolve(path)?.exists()
                && self
                    .entry(path)?
                    .is_none_or(|entry| entry.file_id == *file_id && !entry.deleted)
            {
                continue;
            }
            deletions.push((file_id.clone(), path.clone()));
        }
        let mut puts = Vec::new();
        for path in paths {
            let target = self.resolve(&path)?;
            let (digest, _) = self.cached_sync_file_info(&target, &path)?;
            let previous = self.entry(&path)?;
            let observed: Option<(String, String, bool)> = if let Some(entry) = &previous {
                self.db
                    .query_row(
                        "SELECT path,hash,deleted FROM sync_observed WHERE file_id=?1",
                        [&entry.file_id],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                    )
                    .optional()?
            } else {
                None
            };
            if let Some(entry) = &previous {
                seen.insert(entry.file_id.clone());
            }
            if observed
                .as_ref()
                .is_some_and(|v| v == &(path.clone(), digest.clone(), false))
            {
                continue;
            }
            // 确认快照而不通过外部编辑器回写。
            let operation = Uuid::new_v4().to_string();
            self.store_payload_file(&operation, &target, &digest)?;
            let file_id = previous.map_or_else(|| Uuid::new_v4().to_string(), |e| e.file_id);
            puts.push(DiscoveredPut {
                file_id,
                path,
                digest,
                operation,
            });
        }
        let ordered = self.order_discovered_puts(&previous, &deletions, &puts)?;
        // Publish the entire plan at once. In particular, a cycle's temporary
        // put can never become sendable without its already-spooled final put.
        let tx = self.db.transaction()?;
        for (file_id, path) in &deletions {
            tx.execute(
                "UPDATE files SET deleted=1,revision=revision+1 WHERE id=?1 AND deleted=0",
                [file_id],
            )?;
            tx.execute("INSERT INTO outbox SELECT ?1,id,revision,?3,'','delete',X'','pending' FROM files WHERE id=?2",params![Uuid::new_v4().to_string(),file_id,path])?;
            tx.execute(
                "UPDATE sync_observed SET deleted=1 WHERE file_id=?1",
                [file_id],
            )?;
        }
        for put in &puts {
            tx.execute("INSERT INTO files VALUES (?1,?2,?3,1,0) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,revision=files.revision+1,deleted=0",params![put.file_id,put.path,put.digest])?;
            tx.execute("INSERT INTO sync_observed VALUES (?1,?2,?3,0) ON CONFLICT(file_id) DO UPDATE SET path=excluded.path,hash=excluded.hash,deleted=0",params![put.file_id,put.path,put.digest])?;
        }
        for planned in ordered {
            let put = &puts[planned.index];
            if planned.operation != put.operation {
                tx.execute(
                    "INSERT INTO payloads SELECT ?1,hash,size FROM payloads WHERE operation_id=?2",
                    params![planned.operation, put.operation],
                )?;
            }
            tx.execute("INSERT INTO outbox SELECT ?1,id,revision,?3,?4,'put',X'','pending' FROM files WHERE id=?2",params![planned.operation,put.file_id,planned.path,put.digest])?;
        }
        tx.commit()?;
        let changes = deletions.len() + puts.len();
        for (_, path) in deletions {
            self.changed_path(&path);
        }
        for put in puts {
            self.changed_path(&put.path);
        }
        Ok(changes)
    }

    fn order_discovered_puts(
        &self,
        previous: &[(String, String)],
        deletions: &[(String, String)],
        puts: &[DiscoveredPut],
    ) -> Result<Vec<PlannedPut>> {
        if puts.is_empty() {
            return Ok(Vec::new());
        }
        let deleted: HashSet<_> = deletions.iter().map(|(id, _)| id.as_str()).collect();
        let mut positions: HashMap<_, _> = previous
            .iter()
            .filter(|(id, _)| !deleted.contains(id.as_str()))
            .map(|(id, path)| Ok((id.as_str(), self.sync_path_key(path)?)))
            .collect::<Result<_>>()?;
        let mut occupied: HashMap<_, _> = positions
            .iter()
            .map(|(id, path)| (path.clone(), *id))
            .collect();
        let indices: HashMap<_, _> = puts
            .iter()
            .enumerate()
            .map(|(index, put)| (put.file_id.as_str(), index))
            .collect();
        let mut reserved: HashSet<_> = occupied.keys().cloned().collect();
        for put in puts {
            reserved.insert(self.sync_path_key(&put.path)?);
        }
        let mut states = vec![0u8; puts.len()];
        let mut ordered = Vec::new();
        for start in 0..puts.len() {
            if states[start] == 2 {
                continue;
            }
            let mut stack = vec![start];
            states[start] = 1;
            while let Some(&index) = stack.last() {
                let put = &puts[index];
                let target = self.sync_path_key(&put.path)?;
                if let Some(owner) = occupied
                    .get(&target)
                    .copied()
                    .filter(|id| *id != put.file_id)
                {
                    let dependency = *indices
                        .get(owner)
                        .ok_or_else(|| HostError::new("PATH_CONFLICT"))?;
                    if states[dependency] == 0 {
                        states[dependency] = 1;
                        stack.push(dependency);
                        continue;
                    }
                    if states[dependency] == 2 {
                        return Err(HostError::new("PATH_CONFLICT"));
                    }
                    // Break a cycle with an ordinary v1 put, preserving identity
                    // and collision checks on older receivers. Only the peer
                    // sees this transient name; local files/references stay put.
                    let staged = &puts[dependency];
                    let path = self.sync_rename_staging_path(&staged.path, &reserved)?;
                    let key = self.sync_path_key(&path)?;
                    if let Some(old) = positions.insert(owner, key.clone()) {
                        occupied.remove(&old);
                    }
                    occupied.insert(key.clone(), owner);
                    reserved.insert(key);
                    ordered.push(PlannedPut {
                        index: dependency,
                        path,
                        operation: Uuid::new_v4().to_string(),
                    });
                    continue;
                }
                if let Some(old) = positions.insert(put.file_id.as_str(), target.clone()) {
                    occupied.remove(&old);
                }
                occupied.insert(target, put.file_id.as_str());
                ordered.push(PlannedPut {
                    index,
                    path: put.path.clone(),
                    operation: put.operation.clone(),
                });
                states[index] = 2;
                stack.pop();
            }
        }
        Ok(ordered)
    }

    fn sync_rename_staging_path(
        &self,
        destination: &str,
        reserved: &HashSet<String>,
    ) -> Result<String> {
        let destination = Path::new(destination);
        let parent = destination.parent().unwrap_or_else(|| Path::new(""));
        let extension = destination
            .extension()
            .and_then(|value| value.to_str())
            .ok_or_else(|| HostError::new("SYNC_CLASS_UNSUPPORTED"))?;
        loop {
            let path = parent
                .join(format!(
                    "OpenNexus-sync-rename-{}.{}",
                    Uuid::new_v4(),
                    extension
                ))
                .to_string_lossy()
                .replace('\\', "/");
            if !self.sync_path_enabled(&path)? {
                return Err(HostError::new("SYNC_CLASS_UNSUPPORTED"));
            }
            if !reserved.contains(&self.sync_path_key(&path)?) && !self.resolve(&path)?.exists() {
                let queued: bool = self.db.query_row(
                    "SELECT EXISTS(SELECT 1 FROM outbox WHERE path=?1 AND state IN ('pending','queued'))",
                    [&path], |row| row.get(0))?;
                if !queued {
                    return Ok(path);
                }
            }
        }
    }

    fn sync_path_key(&self, path: &str) -> Result<String> {
        // Use the actual filesystem's spelling rules, including case-sensitive
        // Windows directories. Unicode uppercasing can merge distinct names.
        #[cfg(windows)]
        {
            let target = self.resolve(path)?;
            Ok(if target.exists() {
                target.canonicalize()?
            } else {
                target
            }
            .to_string_lossy()
            .into_owned())
        }
        #[cfg(not(windows))]
        Ok(path.to_owned())
    }
}

struct DiscoveredPut {
    file_id: String,
    path: String,
    digest: String,
    operation: String,
}

struct PlannedPut {
    index: usize,
    path: String,
    operation: String,
}

#[cfg(test)]
#[path = "sync_rename_tests.rs"]
mod rename_tests;
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canvas_and_image_external_edits_enter_sync_discovery() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        fs::write(
            root.path().join("map.canvas"),
            br#"{"nodes":[],"edges":[]}"#,
        )
        .unwrap();
        fs::write(root.path().join("diagram.png"), b"image-bytes").unwrap();
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 2);
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
        assert_eq!(ws.pending_count().unwrap(), 2);
    }
    #[test]
    fn external_edits_after_ui_reads_are_queued_once_and_deletions_survive_restart() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        fs::write(root.path().join("a.md"), b"one").unwrap();
        ws.read("a.md").unwrap();
        ws.scan().unwrap();
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
        fs::write(root.path().join("a.md"), b"two").unwrap();
        ws.read("a.md").unwrap();
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
        fs::remove_file(root.path().join("a.md")).unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
        assert_eq!(ws.pending_count().unwrap(), 3);
        let inline: i64 = ws
            .db
            .query_row(
                "SELECT COALESCE(SUM(length(content)),0) FROM outbox",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(inline, 0);
    }
    #[test]
    fn file_classification_excludes_credentials_caches_and_executable_packages() {
        for path in [
            ".env",
            ".ainote/credentials.md",
            ".git/readme.md",
            "models/model.md",
            "logs/trace.md",
            "node_modules/package/readme.md",
            "attachments/tool.exe",
            "attachments/plugin.zip",
            "settings.json",
            "index.sqlite3",
            "vectors.bin",
            "cache/index.md",
            "logs/agent.md",
            "models/embedding.md",
        ] {
            assert!(!allowed(path), "{path}");
        }
        for path in [
            "notes/a.md",
            "maps/course.canvas",
            "diagrams/chart.png",
            "attachments/movie.mp4",
            "attachments/image.png",
            "attachments/fixture.bin",
            "opennexus-records/v1/conversations/conversation_00000000000000000000000000000001.json",
            "opennexus-records/v1/extension-installations/extension_00000000000000000000000000000001.json",
        ] {
            assert!(allowed(path), "{path}");
        }
    }
    #[test]
    fn prohibited_workspace_outbox_is_retained_but_never_sent() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("provider-settings.json", "", b"fixture-secret", "local")
            .unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        assert!(ws.sync_next(&binding.id).unwrap().is_none());
        assert_eq!(ws.pending_count().unwrap(), 0);
        assert_eq!(
            ws.read("provider-settings.json").unwrap().content,
            "fixture-secret"
        );
        let state: String = ws
            .db
            .query_row("SELECT state FROM outbox", [], |r| r.get(0))
            .unwrap();
        assert_eq!(state, "excluded");
    }
}
