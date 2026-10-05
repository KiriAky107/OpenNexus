//! Watch hints invalidate caches; only fresh disk hashes authorize mutations.
use super::{linked, Entry, HostError, Result, Workspace};
use notify::{Event, EventKind};
#[cfg(not(windows))]
use notify::{RecommendedWatcher, RecursiveMode, Watcher};
#[cfg(windows)]
#[path = "workspace_watch_windows.rs"]
mod windows;
#[cfg(windows)]
use windows::NativeWatcher;
#[path = "workspace_identity.rs"]
mod identity;
#[cfg(test)]
#[path = "workspace_identity_tests.rs"]
mod identity_tests;
#[cfg(not(windows))]
type NativeWatcher = RecommendedWatcher;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const MAX_HINTS: usize = 4096;
const VERIFY_INTERVAL: Duration = Duration::from_secs(300);
const METADATA_INTERVAL: Duration = Duration::from_secs(30);
const FALLBACK_INTERVAL: Duration = Duration::from_secs(2);

#[derive(Default, Clone, Serialize)]
pub struct ScanStats {
    pub full_scans: u64,
    pub metadata_scans: u64,
    pub incremental_batches: u64,
    pub hashed_files: u64,
    pub hashed_bytes: u64,
    pub last_scan_micros: u64,
}

#[derive(Serialize)]
pub struct WatchStatus {
    pub vault_id: String,
    pub mode: &'static str,
    pub error: Option<String>,
    pub revision: u64,
    pub stats: ScanStats,
}

#[derive(Clone, Serialize)]
pub struct WorkspaceChange {
    pub vault_id: String,
    pub revision: u64,
    pub paths: Vec<String>,
}

#[derive(Default)]
pub(super) struct Hints {
    paths: BTreeSet<String>,
    full: bool,
    error: Option<String>,
}

fn hidden(path: &str) -> bool {
    path.split('/')
        .any(|part| part.eq_ignore_ascii_case(".ainote") || part.eq_ignore_ascii_case(".git"))
}
fn supported(path: &str) -> bool {
    !hidden(path)
        && !path
            .split('/')
            .any(|part| part.eq_ignore_ascii_case("opennexus-records"))
        && Path::new(path).extension().is_some_and(|ext| {
            [
                "md", "canvas", "py", "json", "csv", "png", "jpg", "jpeg", "gif", "webp",
            ]
            .iter()
            .any(|value| ext.eq_ignore_ascii_case(value))
        })
}
fn below(path: &str, parent: &str) -> bool {
    path == parent || path.starts_with(&format!("{parent}/"))
}

impl Hints {
    fn receive(&mut self, root: &Path, result: notify::Result<Event>) {
        let event = match result {
            Ok(event) => event,
            Err(_) => {
                self.full = true;
                self.error = Some("WORKSPACE_WATCH_ERROR".into());
                return;
            }
        };
        if event.need_rescan() {
            self.full = true;
        }
        // Read/access notifications must not turn our own reads into refresh loops.
        if matches!(event.kind, EventKind::Access(_)) {
            return;
        }
        if event.paths.is_empty() {
            self.full = true;
        }
        for path in event.paths {
            let Ok(relative) = path.strip_prefix(root) else {
                self.full = true;
                continue;
            };
            let relative = relative.to_string_lossy().replace('\\', "/");
            if relative.is_empty() {
                self.full = true;
            } else if !hidden(&relative) {
                self.paths.insert(relative);
            }
        }
        if self.paths.len() > MAX_HINTS {
            self.paths.clear();
            self.full = true;
        }
    }
}

#[derive(Clone, PartialEq, Eq)]
struct Stamp {
    size: u64,
    modified: SystemTime,
    identity: Option<String>,
}
impl Stamp {
    fn read(path: &Path) -> Result<Self> {
        let metadata = fs::metadata(path)?;
        Ok(Self {
            size: metadata.len(),
            modified: metadata.modified()?,
            identity: disk_identity(path, &metadata)?,
        })
    }
}

#[cfg(windows)]
pub(super) fn disk_identity(path: &Path, _: &fs::Metadata) -> Result<Option<String>> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };
    let file = fs::File::open(path)?;
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
        return Ok(None);
    }
    Ok(Some(format!(
        "{}:{}:{}:{}:{}",
        info.dwVolumeSerialNumber,
        info.nFileIndexHigh,
        info.nFileIndexLow,
        info.ftCreationTime.dwHighDateTime,
        info.ftCreationTime.dwLowDateTime
    )))
}
#[cfg(unix)]
pub(super) fn disk_identity(_: &Path, metadata: &fs::Metadata) -> Result<Option<String>> {
    use std::os::unix::fs::MetadataExt;
    // An inode may be reused after deletion. Without a birth time there is not
    // enough evidence to persist an identity across process lifetimes.
    Ok(metadata
        .created()
        .ok()
        .map(|created| format!("{}:{}:{created:?}", metadata.dev(), metadata.ino())))
}
#[cfg(not(any(windows, unix)))]
pub(super) fn disk_identity(_: &Path, _: &fs::Metadata) -> Result<Option<String>> {
    Ok(None)
}

#[derive(Clone)]
struct CachedEntry {
    stamp: Option<Stamp>,
    entry: Entry,
}

pub(super) struct Cache {
    // Dropping a vault drops its watcher and pending events together.
    watcher: Option<NativeWatcher>,
    hints: Arc<Mutex<Hints>>,
    entries: BTreeMap<String, CachedEntry>,
    sync_digests: BTreeMap<String, (Stamp, String, Instant)>,
    last_metadata: Instant,
    last_verified: Instant,
    revision: u64,
    notified_revision: u64,
    changed: BTreeSet<String>,
    stats: ScanStats,
    last_error: Option<String>,
}
impl Cache {
    pub(super) fn new(root: &Path) -> Self {
        let hints = Arc::new(Mutex::new(Hints::default()));
        let pending = hints.clone();
        let watched_root = root.to_owned();
        #[cfg(windows)]
        let watcher = NativeWatcher::new(watched_root, pending).ok();
        #[cfg(not(windows))]
        let watcher = notify::recommended_watcher(move |event| {
            if let Ok(mut pending) = pending.lock() {
                pending.receive(&watched_root, event);
            }
        })
        .and_then(|mut watcher| {
            watcher.watch(root, RecursiveMode::Recursive)?;
            Ok(watcher)
        })
        .ok();
        if watcher.is_none() {
            hints.lock().unwrap().error = Some("WORKSPACE_WATCH_UNAVAILABLE".into());
        }
        Self {
            watcher,
            hints,
            entries: BTreeMap::new(),
            sync_digests: BTreeMap::new(),
            last_metadata: Instant::now(),
            last_verified: Instant::now(),
            revision: 0,
            notified_revision: 0,
            changed: BTreeSet::new(),
            stats: ScanStats::default(),
            last_error: None,
        }
    }
}

impl Workspace {
    pub(crate) fn changed_path(&self, path: &str) {
        if let Ok(mut hints) = self.cache.hints.lock() {
            hints.paths.insert(path.into());
            if hints.paths.len() > MAX_HINTS {
                hints.paths.clear();
                hints.full = true;
            }
        }
    }
    pub fn watch_status(&self) -> WatchStatus {
        let error = self
            .cache
            .hints
            .lock()
            .ok()
            .and_then(|hints| hints.error.clone());
        WatchStatus {
            vault_id: self.vault_id.clone(),
            mode: if self.cache.watcher.is_some() && error.is_none() {
                "watch"
            } else {
                "poll-fallback"
            },
            error: error.or_else(|| self.cache.last_error.clone()),
            revision: self.cache.revision,
            stats: self.cache.stats.clone(),
        }
    }
    pub(super) fn cached_entries(&self) -> Vec<Entry> {
        self.cache
            .entries
            .values()
            .map(|item| item.entry.clone())
            .collect()
    }
    pub fn poll_change(&mut self) -> Result<Option<WorkspaceChange>> {
        self.refresh_snapshot(false)?;
        if self.cache.notified_revision == self.cache.revision {
            return Ok(None);
        }
        self.cache.notified_revision = self.cache.revision;
        Ok(Some(WorkspaceChange {
            vault_id: self.vault_id.clone(),
            revision: self.cache.revision,
            paths: std::mem::take(&mut self.cache.changed)
                .into_iter()
                .collect(),
        }))
    }

    pub(crate) fn refresh_snapshot(&mut self, explicit: bool) -> Result<()> {
        let started = Instant::now();
        let (dirty, lost, fallback) = {
            let mut hints = self
                .cache
                .hints
                .lock()
                .map_err(|_| HostError::new("HOST_BUSY"))?;
            (
                std::mem::take(&mut hints.paths),
                std::mem::take(&mut hints.full),
                self.cache.watcher.is_none() || hints.error.is_some(),
            )
        };
        let verify_interval = if fallback {
            METADATA_INTERVAL
        } else {
            VERIFY_INTERVAL
        };
        let full = explicit
            || lost
            || self.cache.revision == 0
            || self.cache.last_verified.elapsed() >= verify_interval;
        let enumerate = full
            || self.cache.last_metadata.elapsed()
                >= if fallback {
                    FALLBACK_INTERVAL
                } else {
                    METADATA_INTERVAL
                };
        if !enumerate && dirty.is_empty() {
            return Ok(());
        }
        let result = self.update_snapshot(&dirty, full, enumerate);
        self.cache.stats.last_scan_micros = started.elapsed().as_micros() as u64;
        self.cache.last_error = result.as_ref().err().map(|error| error.code.clone());
        if result.is_err() {
            // A racing rename/temporary sharing violation must not consume the hint.
            if let Ok(mut hints) = self.cache.hints.lock() {
                hints.full = true;
            }
        }
        result
    }

    fn update_snapshot(
        &mut self,
        dirty: &BTreeSet<String>,
        full: bool,
        enumerate: bool,
    ) -> Result<()> {
        // Structural hints need a metadata enumeration: the old-name event can
        // arrive alone, and Windows still resolves an obsolete case spelling.
        // Content edits keep the incremental path and idle polls remain free.
        let enumerate = enumerate || self.identity_rescan_needed(dirty)?;
        if full {
            self.cache.sync_digests.clear();
            self.cache.stats.full_scans += 1;
        } else {
            self.cache
                .sync_digests
                .retain(|path, _| !dirty.iter().any(|parent| below(path, parent)));
            if enumerate {
                self.cache.stats.metadata_scans += 1;
            } else {
                self.cache.stats.incremental_batches += 1;
            }
        }
        let mut paths = Vec::new();
        let mut removals = BTreeSet::new();
        if enumerate {
            self.scan_dir(&self.root, &mut paths, true)?;
            let present: BTreeSet<_> = paths.iter().cloned().collect();
            removals.extend(
                self.cache
                    .entries
                    .keys()
                    .filter(|path| !present.contains(*path))
                    .cloned(),
            );
        } else {
            // Collapse parent/child duplicates; a directory hint scans only its subtree.
            let parents: Vec<_> = dirty
                .iter()
                .filter(|path| {
                    !dirty
                        .iter()
                        .any(|parent| parent != *path && below(path, parent))
                })
                .cloned()
                .collect();
            for parent in parents {
                if parent
                    .split('/')
                    .any(|part| part.eq_ignore_ascii_case("opennexus-records"))
                {
                    continue;
                }
                let target = self.resolve(&parent)?;
                removals.extend(
                    self.cache
                        .entries
                        .keys()
                        .filter(|path| below(path, &parent))
                        .cloned(),
                );
                if !target.exists() || linked(&target)? {
                    continue;
                }
                if target.is_dir() {
                    paths.push(parent);
                    self.scan_dir(&target, &mut paths, true)?;
                } else if supported(&parent) {
                    paths.push(parent);
                }
            }
        }
        paths.sort();
        paths.dedup();
        let mut next = self.cache.entries.clone();
        for path in &removals {
            next.remove(path);
        }
        for path in paths {
            let target = self.resolve(&path)?;
            let metadata = fs::metadata(&target)?;
            let modified = metadata
                .modified()?
                .duration_since(UNIX_EPOCH)
                .map_err(|_| HostError::new("FILESYSTEM_ERROR"))?
                .as_millis()
                .to_string();
            if metadata.is_dir() {
                next.insert(
                    path.clone(),
                    CachedEntry {
                        stamp: None,
                        entry: Entry {
                            file_id: format!("folder:{path}"),
                            path,
                            hash: String::new(),
                            revision: 0,
                            deleted: false,
                            is_folder: true,
                            updated_at: None,
                        },
                    },
                );
                continue;
            }
            let stamp = Stamp::read(&target)?;
            let cached = self.cache.entries.get(&path);
            // Event hints override stamps, including same size and preserved mtime.
            let rehash = full
                || dirty.iter().any(|parent| below(&path, parent))
                || cached.is_none_or(|item| item.stamp.as_ref() != Some(&stamp));
            if !rehash {
                next.insert(path, cached.unwrap().clone());
                continue;
            }
            let digest = crate::payloads::hash_file(&target)?;
            self.cache.stats.hashed_files += 1;
            self.cache.stats.hashed_bytes += stamp.size;
            let entry = Entry {
                file_id: String::new(),
                path: path.clone(),
                hash: digest,
                revision: 0,
                deleted: false,
                is_folder: false,
                updated_at: Some(modified),
            };
            next.insert(
                path,
                CachedEntry {
                    stamp: Some(stamp),
                    entry,
                },
            );
        }
        self.reconcile_file_identities(&mut next, enumerate)?;
        let all: BTreeSet<_> = self
            .cache
            .entries
            .keys()
            .chain(next.keys())
            .cloned()
            .collect();
        let changed: Vec<_> = all
            .into_iter()
            .filter(|path| {
                self.cache.entries.get(path).map(|item| &item.entry)
                    != next.get(path).map(|item| &item.entry)
            })
            .collect();
        if !changed.is_empty() || self.cache.revision == 0 {
            self.cache.revision += 1;
            self.cache.changed.extend(changed);
        }
        self.cache.entries = next;
        if enumerate {
            self.cache.last_metadata = Instant::now();
        }
        if full {
            self.cache.last_verified = Instant::now();
        }
        Ok(())
    }

    pub(crate) fn cached_sync_file_info(
        &mut self,
        source: &Path,
        path: &str,
    ) -> Result<(String, u64)> {
        let stamp = Stamp::read(source)?;
        if let Some((previous, digest, checked)) = self.cache.sync_digests.get(path) {
            if previous == &stamp && checked.elapsed() < VERIFY_INTERVAL {
                return Ok((digest.clone(), stamp.size));
            }
        }
        let (digest, size) = crate::payloads::sync_file_info(source, path)?;
        self.cache.stats.hashed_files += 1;
        self.cache.stats.hashed_bytes += size;
        self.cache
            .sync_digests
            .insert(path.into(), (stamp, digest.clone(), Instant::now()));
        Ok((digest, size))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use notify::event::Flag;

    fn hint(ws: &Workspace, paths: &[&str]) {
        let event = Event::new(EventKind::Any);
        let event = paths
            .iter()
            .fold(event, |event, path| event.add_path(ws.root.join(path)));
        ws.cache.hints.lock().unwrap().receive(&ws.root, Ok(event));
    }
    fn reset_hints(ws: &Workspace) {
        ws.cache.hints.lock().unwrap().paths.clear();
    }

    #[test]
    fn mixed_vault_idle_and_metadata_checks_read_zero_bytes() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("attachments/ab")).unwrap();
        for index in 0..100 {
            fs::write(root.path().join(format!("{index}.md")), vec![b'x'; 4096]).unwrap();
        }
        for index in 0..5 {
            fs::write(
                root.path().join(format!("{index}.canvas")),
                br#"{"nodes":[],"edges":[]}"#,
            )
            .unwrap();
        }
        for index in 0..8 {
            fs::write(
                root.path().join(format!("{index}.png")),
                vec![7; 1024 * 1024],
            )
            .unwrap();
        }
        fs::write(
            root.path()
                .join(format!("attachments/ab/{}.png", "ab".repeat(32))),
            vec![8; 1024 * 1024],
        )
        .unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let initial = ws.watch_status().stats;
        let start = Instant::now();
        for _ in 0..5 {
            ws.scan().unwrap();
        }
        let forced_us = start.elapsed().as_micros();
        let baseline = ws.watch_status().stats;
        reset_hints(&ws);
        let start = Instant::now();
        for _ in 0..20 {
            ws.poll_change().unwrap();
        }
        let idle_us = start.elapsed().as_micros();
        let start = Instant::now();
        for _ in 0..5 {
            ws.cache.last_metadata = Instant::now() - METADATA_INTERVAL;
            ws.tree().unwrap();
        }
        let metadata_us = start.elapsed().as_micros();
        let after = ws.watch_status().stats;
        assert_eq!(baseline.hashed_bytes, after.hashed_bytes);
        assert_eq!(baseline.hashed_files, after.hashed_files);
        assert_eq!(
            ws.tree()
                .unwrap()
                .iter()
                .filter(|entry| !entry.is_folder)
                .count(),
            114
        );
        println!("WORKSPACE_PERF files=114 checks=5 baseline_bytes={} baseline_hashes={} baseline_us={} cached_bytes=0 cached_hashes=0 metadata_us={} idle_checks=20 idle_us={}", baseline.hashed_bytes-initial.hashed_bytes, baseline.hashed_files-initial.hashed_files, forced_us, metadata_us, idle_us);
    }

    #[test]
    fn same_size_preserved_timestamp_hints_merge_and_cas_reads_real_disk() {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("a.md"), b"old").unwrap();
        fs::write(root.path().join("b.md"), b"other").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.poll_change().unwrap();
        reset_hints(&ws);
        let before = ws.entry("a.md").unwrap().unwrap();
        let stamp = fs::metadata(root.path().join("a.md"))
            .unwrap()
            .modified()
            .unwrap();
        let file = fs::OpenOptions::new()
            .write(true)
            .open(root.path().join("a.md"))
            .unwrap();
        use std::io::Write;
        (&file).write_all(b"new").unwrap();
        file.set_modified(stamp).unwrap();
        assert_eq!(
            ws.write("a.md", &before.hash, b"overwrite", "local")
                .unwrap_err()
                .code,
            "REVISION_CONFLICT"
        );
        for _ in 0..100 {
            hint(&ws, &["a.md"]);
        }
        let bytes = ws.cache.stats.hashed_bytes;
        let change = ws.poll_change().unwrap().unwrap();
        assert_eq!(change.paths, ["a.md"]);
        assert_eq!(ws.cache.stats.hashed_bytes - bytes, 3);
        assert_eq!(ws.entry("a.md").unwrap().unwrap().file_id, before.file_id);
        assert_eq!(
            ws.entry("a.md").unwrap().unwrap().hash,
            super::super::hash(b"new")
        );
        hint(&ws, &["a.md"]);
        assert!(ws.poll_change().unwrap().is_none());
        assert_eq!(fs::read(root.path().join("a.md")).unwrap(), b"new");
    }

    #[test]
    fn atomic_replace_directory_rename_and_delete_preserve_identity() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("folder/empty")).unwrap();
        fs::write(root.path().join("folder/a.md"), b"old").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let id = ws.entry("folder/a.md").unwrap().unwrap().file_id;
        let temp = tempfile::NamedTempFile::new_in(root.path().join("folder")).unwrap();
        fs::write(temp.path(), b"new").unwrap();
        temp.persist(root.path().join("folder/a.md")).unwrap();
        hint(&ws, &["folder/a.md"]);
        ws.tree().unwrap();
        assert_eq!(ws.entry("folder/a.md").unwrap().unwrap().file_id, id);
        fs::rename(root.path().join("folder"), root.path().join("renamed")).unwrap();
        hint(&ws, &["folder", "renamed", "renamed/a.md"]);
        let tree = ws.tree().unwrap();
        assert!(tree.iter().any(|entry| entry.path == "renamed/empty"));
        assert!(!tree.iter().any(|entry| entry.path.starts_with("folder/")));
        assert_eq!(ws.path_for_id(&id).unwrap(), "renamed/a.md");
        fs::remove_file(root.path().join("renamed/a.md")).unwrap();
        hint(&ws, &["renamed/a.md"]);
        ws.tree().unwrap();
        assert!(ws.entry("renamed/a.md").unwrap().unwrap().deleted);
    }

    #[test]
    fn overflow_missing_events_resume_and_unavailable_watcher_rescan() {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("a.md"), b"old").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.cache.watcher = None;
        let stamp = fs::metadata(root.path().join("a.md"))
            .unwrap()
            .modified()
            .unwrap();
        fs::write(root.path().join("a.md"), b"new").unwrap();
        fs::File::options()
            .write(true)
            .open(root.path().join("a.md"))
            .unwrap()
            .set_modified(stamp)
            .unwrap();
        reset_hints(&ws);
        ws.cache.last_verified = Instant::now() - METADATA_INTERVAL;
        ws.tree().unwrap();
        assert_eq!(
            ws.entry("a.md").unwrap().unwrap().hash,
            super::super::hash(b"new")
        );
        assert_eq!(ws.watch_status().mode, "poll-fallback");
        fs::write(root.path().join("a.md"), b"end").unwrap();
        let event = Event::new(EventKind::Any).set_flag(Flag::Rescan);
        ws.cache.hints.lock().unwrap().receive(&ws.root, Ok(event));
        ws.tree().unwrap();
        assert_eq!(
            ws.entry("a.md").unwrap().unwrap().hash,
            super::super::hash(b"end")
        );
        for index in 0..=MAX_HINTS {
            ws.changed_path(&format!("{index}.md"));
        }
        assert!(ws.cache.hints.lock().unwrap().full);
        assert!(ws.cache.hints.lock().unwrap().paths.len() <= MAX_HINTS);
        drop(ws);
        let mut reopened = Workspace::open(root.path()).unwrap();
        assert_eq!(
            reopened
                .tree()
                .unwrap()
                .iter()
                .find(|entry| entry.path == "a.md")
                .unwrap()
                .hash,
            super::super::hash(b"end")
        );
    }

    #[test]
    fn real_watcher_observes_external_edit_without_ui_polling() {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("a.md"), b"old").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.watch_status().mode, "watch");
        ws.poll_change().unwrap();
        fs::write(root.path().join("a.md"), b"new").unwrap();
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if let Some(change) = ws.poll_change().unwrap() {
                assert_eq!(change.paths, ["a.md"]);
                break;
            }
            assert!(Instant::now() < deadline, "native watcher missed edit");
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_watch_close_drains_io_before_immediate_directory_cleanup() {
        let started = Instant::now();
        for _ in 0..50 {
            let root = tempfile::tempdir().unwrap();
            let ws = Workspace::open(root.path()).unwrap();
            for index in 0..5 {
                fs::write(root.path().join(format!("笔记😀-{index}.md")), b"event").unwrap();
            }
            drop(ws);
            root.close().unwrap();
        }
        assert!(started.elapsed() < Duration::from_secs(10));
    }

    #[test]
    fn sync_idle_cache_invalidates_same_timestamp_and_remote_commits() {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("a.md"), b"old").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        // Binding already queues the initial snapshot; discovery must not duplicate it.
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
        reset_hints(&ws);
        let bytes = ws.cache.stats.hashed_bytes;
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
        assert_eq!(ws.cache.stats.hashed_bytes, bytes);
        let stamp = fs::metadata(root.path().join("a.md"))
            .unwrap()
            .modified()
            .unwrap();
        fs::write(root.path().join("a.md"), b"new").unwrap();
        fs::File::options()
            .write(true)
            .open(root.path().join("a.md"))
            .unwrap()
            .set_modified(stamp)
            .unwrap();
        hint(&ws, &["a.md"]);
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
        let before = ws.entry("a.md").unwrap().unwrap();
        ws.write("a.md", &before.hash, b"remote", "remote").unwrap();
        assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
        assert_eq!(
            ws.tree()
                .unwrap()
                .iter()
                .find(|entry| entry.path == "a.md")
                .unwrap()
                .hash,
            super::super::hash(b"remote")
        );
        fs::write(root.path().join("a.md"), b"manual").unwrap();
        reset_hints(&ws);
        assert_eq!(
            ws.rename("a.md", "b.md", &before.hash).unwrap_err().code,
            "REVISION_CONFLICT"
        );
        assert_eq!(
            ws.delete("a.md", &before.hash).unwrap_err().code,
            "REVISION_CONFLICT"
        );
    }
}
