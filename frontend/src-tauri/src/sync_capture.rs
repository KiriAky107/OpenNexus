//! Verify one original pending outbox payload before short queue publication.
use crate::{
    payloads::WritePayload,
    workspace::{HostError, Result, Workspace},
    workspace_commit::CommitGuard,
};
use rusqlite::{params, OptionalExtension};

pub(crate) struct PreparedCapture<'a> {
    workspace: &'a mut Workspace,
    binding: String,
    operation_id: String,
    file_id: String,
    path: String,
    digest: String,
    operation: String,
    payload: Option<u64>,
    excluded: bool,
}

impl Workspace {
    pub(crate) fn sync_capture_checked(
        &mut self,
        binding: &str,
        guard: &impl CommitGuard,
    ) -> Result<()> {
        guard.check()?;
        self.check_binding(binding)?;
        self.normalize_record_links_checked(guard)?;
        while let Some(prepared) = self.sync_prepare_capture(binding, &|| guard.check())? {
            prepared.commit(guard)?;
        }
        guard.check()?;
        Ok(())
    }

    pub(crate) fn sync_prepare_capture(
        &mut self,
        binding: &str,
        check: &dyn Fn() -> Result<()>,
    ) -> Result<Option<PreparedCapture<'_>>> {
        check()?;
        self.check_binding(binding)?;
        let pending = self.db.query_row(
            "SELECT operation_id,file_id,path,hash,operation,content FROM outbox WHERE state='pending' ORDER BY rowid LIMIT 1", [],
            |row| Ok((row.get::<_,String>(0)?,row.get::<_,String>(1)?,row.get::<_,String>(2)?,row.get::<_,String>(3)?,row.get::<_,String>(4)?,row.get::<_,Vec<u8>>(5)?))
        ).optional()?;
        let Some((operation_id, file_id, path, digest, operation, content)) = pending else {
            return Ok(None);
        };
        check()?;
        let excluded = !self.sync_path_enabled(&path)?;
        let payload = if !excluded && operation == "put" {
            let stored = self.payload_ref(&operation_id)?;
            let payload = match stored {
                Some((ref stored, size)) => {
                    if stored != &digest || !(0..=100 * 1024 * 1024).contains(&size) {
                        return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
                    }
                    WritePayload::Stored {
                        digest: stored,
                        size: size as u64,
                    }
                }
                None => WritePayload::Inline(&content),
            };
            payload.validate_checked(self, &path, check)?;
            if payload.digest_checked(check)? != digest {
                return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
            }
            Some(payload.stage_checked(self, &digest, check)?)
        } else {
            None
        };
        check()?;
        Ok(Some(PreparedCapture {
            workspace: self,
            binding: binding.to_owned(),
            operation_id,
            file_id,
            path,
            digest,
            operation,
            payload,
            excluded,
        }))
    }
}

impl PreparedCapture<'_> {
    pub(crate) fn commit(self, guard: &impl CommitGuard) -> Result<()> {
        let Self {
            workspace,
            binding,
            operation_id,
            file_id,
            path,
            digest,
            operation,
            payload,
            excluded,
        } = self;
        let tx = workspace.db.transaction()?;
        if !excluded {
            if let Some(size) = payload {
                let previous: Option<(String, i64)> = tx
                    .query_row(
                        "SELECT hash,size FROM payloads WHERE operation_id=?1",
                        [&operation_id],
                        |row| Ok((row.get(0)?, row.get(1)?)),
                    )
                    .optional()?;
                if previous.is_some_and(|old| old != (digest.clone(), size as i64)) {
                    return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
                }
                tx.execute(
                    "INSERT OR IGNORE INTO payloads VALUES (?1,?2,?3)",
                    params![operation_id, digest, size as i64],
                )?;
            }
            tx.execute("INSERT OR IGNORE INTO sync_jobs VALUES (?1,?2,?3,?4,?5,?6,?7,'pending',NULL,NULL,NULL,NULL)",
                params![binding,operation_id,file_id,path,digest,payload.unwrap_or(0) as i64,operation])?;
        }
        let changed = tx.execute(
            "UPDATE outbox SET state=?6,content=CASE WHEN ?6='queued' THEN X'' ELSE content END WHERE operation_id=?1 AND file_id=?2 AND path=?3 AND hash=?4 AND operation=?5 AND state='pending'",
            params![operation_id,file_id,path,digest,operation,if excluded {"excluded"} else {"queued"}])?;
        if changed != 1 {
            return Err(HostError::new("SYNC_OPERATION_SUPERSEDED"));
        }
        guard.commit(tx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::{hash, Entry};
    use crate::workspace_commit::UnscopedCommit;
    use std::fs;
    use uuid::Uuid;

    fn legacy(ws: &mut Workspace, bytes: &[u8]) -> (String, Entry) {
        fs::write(ws.root.join("legacy.md"), bytes).unwrap();
        ws.scan().unwrap();
        let entry = ws.entry("legacy.md").unwrap().unwrap();
        let operation = Uuid::new_v4().to_string();
        ws.db
            .execute(
                "INSERT INTO outbox VALUES (?1,?2,1,'legacy.md',?3,'put',?4,'pending')",
                params![operation, entry.file_id, entry.hash, bytes],
            )
            .unwrap();
        (operation, entry)
    }
    fn binding(ws: &mut Workspace) -> String {
        ws.sync_bind_download("http://localhost:1", "remote", "account")
            .unwrap()
            .id
    }
    fn state(ws: &Workspace, operation: &str) -> String {
        ws.db
            .query_row(
                "SELECT state FROM outbox WHERE operation_id=?1",
                [operation],
                |row| row.get(0),
            )
            .unwrap()
    }
    fn jobs(ws: &Workspace) -> i64 {
        ws.db
            .query_row("SELECT COUNT(*) FROM sync_jobs", [], |row| row.get(0))
            .unwrap()
    }
    struct Reject;
    impl CommitGuard for Reject {
        fn check(&self) -> Result<()> {
            Ok(())
        }
        fn commit(&self, _transaction: rusqlite::Transaction<'_>) -> Result<()> {
            Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
        }
    }

    #[test]
    fn sync_capture_cancel_after_private_legacy_spool_keeps_original_pending_id() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = binding(&mut ws);
        let bytes = vec![7; 256 * 1024];
        let (operation, entry) = legacy(&mut ws, &bytes);
        let spool = ws.sync_spool(&entry.hash).unwrap();
        let before = ws.db.total_changes();
        let error = ws
            .sync_prepare_capture(&binding, &|| {
                if spool.exists() {
                    Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
                } else {
                    Ok(())
                }
            })
            .err()
            .unwrap();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(ws.db.total_changes(), before);
        assert_eq!(fs::read(spool).unwrap(), bytes);
        assert_eq!(state(&ws, &operation), "pending");
        assert!(ws.payload_ref(&operation).unwrap().is_none());
        assert_eq!(jobs(&ws), 0);
        assert_eq!(ws.entry("legacy.md").unwrap(), Some(entry));
    }

    #[test]
    fn sync_capture_drop_or_commit_refusal_never_publishes_legacy_payload_or_job() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = binding(&mut ws);
        let (operation, entry) = legacy(&mut ws, b"legacy exact\r\n");
        let before = ws.db.total_changes();
        drop(
            ws.sync_prepare_capture(&binding, &|| Ok(()))
                .unwrap()
                .unwrap(),
        );
        assert_eq!(ws.db.total_changes(), before);
        assert!(ws.payload_ref(&operation).unwrap().is_none());
        let prepared = ws
            .sync_prepare_capture(&binding, &|| Ok(()))
            .unwrap()
            .unwrap();
        assert_eq!(
            prepared.commit(&Reject).unwrap_err().code,
            "SYNC_FOREGROUND_CHANGED"
        );
        assert_eq!(state(&ws, &operation), "pending");
        assert!(ws.payload_ref(&operation).unwrap().is_none());
        assert_eq!(jobs(&ws), 0);
        assert_eq!(
            fs::read(root.path().join("legacy.md")).unwrap(),
            b"legacy exact\r\n"
        );
        assert_eq!(ws.entry("legacy.md").unwrap(), Some(entry));
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        assert_eq!(state(&ws, &operation), "pending");
        assert_eq!(jobs(&ws), 0);
    }

    #[test]
    fn sync_capture_acceptance_queues_original_ids_once_and_preserves_excluded_content() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = binding(&mut ws);
        let (operation, entry) = legacy(&mut ws, b"legacy exact\r\n");
        let other = Uuid::new_v4().to_string();
        ws.write_operation("other.md", "", b"other", "local", &other)
            .unwrap();
        let excluded = Uuid::new_v4().to_string();
        ws.write_operation("cache/blocked.md", "", b"private cache", "local", &excluded)
            .unwrap();
        ws.sync_capture_checked(&binding, &UnscopedCommit).unwrap();
        assert_eq!(jobs(&ws), 2);
        assert_eq!(state(&ws, &operation), "queued");
        assert_eq!(state(&ws, &other), "queued");
        assert_eq!(state(&ws, &excluded), "excluded");
        assert_eq!(ws.payload(&operation, b"").unwrap(), b"legacy exact\r\n");
        let job = ws.sync_next(&binding).unwrap().unwrap();
        assert_eq!(job.operation_id, operation);
        assert_eq!(job.file_id, entry.file_id);
        assert_eq!(job.hash, hash(b"legacy exact\r\n"));
        assert!(job.base_revision.is_none());
        assert!(job.upload_id.is_none());
        let before = ws.db.total_changes();
        ws.sync_capture_checked(&binding, &UnscopedCommit).unwrap();
        assert_eq!(ws.db.total_changes(), before);
        assert_eq!(jobs(&ws), 2);
        assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
    }

    #[test]
    fn sync_capture_corrupt_original_spool_is_not_queued_or_replaced() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = binding(&mut ws);
        let operation = Uuid::new_v4().to_string();
        let entry = ws
            .write_operation("original.md", "", b"original", "local", &operation)
            .unwrap();
        fs::write(ws.sync_spool(&entry.hash).unwrap(), b"modified").unwrap();
        let before = ws.db.total_changes();
        assert_eq!(
            ws.sync_capture_checked(&binding, &UnscopedCommit)
                .unwrap_err()
                .code,
            "SYNC_SPOOL_CORRUPT"
        );
        assert_eq!(ws.db.total_changes(), before);
        assert_eq!(state(&ws, &operation), "pending");
        assert_eq!(jobs(&ws), 0);
        assert_eq!(
            fs::read(root.path().join("original.md")).unwrap(),
            b"original"
        );
        assert_eq!(ws.payload_ref(&operation).unwrap().unwrap().0, entry.hash);
    }
}
