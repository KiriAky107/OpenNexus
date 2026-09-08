//! Reconcile externally edited files against committed snapshots, never against UI read caches.
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use std::{collections::HashSet, fs, path::Path};
use uuid::Uuid;
/// File transport only. Logical records receive their own versioned whitelist separately.
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
    extension == "md"
        || (parts
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
                } else if allowed(&path) && item.file_type()?.is_file() {
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
        let paths = self.sync_paths()?;
        let mut seen = HashSet::new();
        let mut changes = 0;
        for path in paths {
            let target = self.resolve(&path)?;
            let (digest, _) = crate::payloads::sync_file_info(&target, &path)?;
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
            // Confirm the snapshot without writing back over an external editor.
            let operation = Uuid::new_v4().to_string();
            self.store_payload_file(&operation, &target, &digest)?;
            let file_id = previous.map_or_else(|| Uuid::new_v4().to_string(), |e| e.file_id);
            let tx = self.db.transaction()?;
            tx.execute("INSERT INTO files VALUES (?1,?2,?3,1,0) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,revision=files.revision+1,deleted=0",params![file_id,path,digest])?;
            tx.execute("INSERT INTO outbox SELECT ?1,id,revision,path,hash,'put',X'','pending' FROM files WHERE id=?2",params![operation,file_id])?;
            tx.execute("INSERT INTO sync_observed VALUES (?1,?2,?3,0) ON CONFLICT(file_id) DO UPDATE SET path=excluded.path,hash=excluded.hash,deleted=0",params![file_id,path,digest])?;
            tx.commit()?;
            seen.insert(file_id);
            changes += 1;
        }
        let previous = {
            let mut statement = self
                .db
                .prepare("SELECT file_id,path FROM sync_observed WHERE deleted=0")?;
            let rows = statement
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            rows
        };
        for (file_id, path) in previous {
            if seen.contains(&file_id) || !allowed(&path) || self.resolve(&path)?.exists() {
                continue;
            }
            let operation = Uuid::new_v4().to_string();
            let tx = self.db.transaction()?;
            tx.execute(
                "UPDATE files SET deleted=1,revision=revision+1 WHERE id=?1",
                [&file_id],
            )?;
            tx.execute("INSERT INTO outbox SELECT ?1,id,revision,path,'','delete',X'','pending' FROM files WHERE id=?2",params![operation,file_id])?;
            tx.execute(
                "UPDATE sync_observed SET deleted=1 WHERE file_id=?1",
                [&file_id],
            )?;
            tx.commit()?;
            changes += 1;
        }
        Ok(changes)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
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
        ] {
            assert!(!allowed(path), "{path}");
        }
        for path in [
            "notes/a.md",
            "attachments/movie.mp4",
            "attachments/image.png",
            "attachments/fixture.bin",
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
