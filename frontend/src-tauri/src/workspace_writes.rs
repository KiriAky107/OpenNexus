//! Checked preparation, short durable acceptance, original journal completion.
use super::{hash, Entry, HostError, Result, Workspace};
use crate::payloads::WritePayload;
use rusqlite::{params, OptionalExtension};
use uuid::Uuid;

struct NewWrite {
    file_id: String,
    digest: String,
    size: u64,
    identity_moves: Vec<(String, String)>,
}

/// Exclusive, nonclone and nonserde. Preparation publishes no write, identity
/// move, payload reference or recoverable operation. Only private spool bytes.
pub(crate) struct PreparedWrite<'a> {
    workspace: &'a mut Workspace,
    operation: String,
    fingerprint: String,
    path: String,
    expected: String,
    origin: String,
    new: Option<NewWrite>,
}

/// An accepted journal is a durable original outcome even when its caller goes
/// away. Complete it outside the lifecycle guard; recovery uses the same ID.
pub(crate) struct AcceptedWrite<'a> {
    workspace: &'a mut Workspace,
    operation: String,
    path: String,
    expected: String,
    origin: String,
    new_file_id: Option<String>,
}

impl Workspace {
    pub(crate) fn prepare_write_checked(
        &mut self,
        path: &str,
        expected: &str,
        content: WritePayload<'_>,
        origin: &str,
        operation: &str,
        preparation: (Option<&str>, &dyn Fn() -> Result<()>),
    ) -> Result<PreparedWrite<'_>> {
        let (identity, check) = preparation;
        check()?;
        if Uuid::parse_str(operation).is_err() {
            return Err(HostError::new("OPERATION_ID_INVALID"));
        }
        content.validate_checked(self, path, check)?;
        if !matches!(origin, "local" | "remote") {
            return Err(HostError::new("INVALID_ORIGIN"));
        }
        let digest = content.digest_checked(check)?;
        let fingerprint = hash(
            &serde_json::to_vec(&(path, expected, &digest, origin))
                .map_err(|_| HostError::new("INVALID_OPERATION"))?,
        );
        let previous: Option<String> = self
            .db
            .query_row(
                "SELECT fingerprint FROM operations WHERE operation_id=?1",
                [operation],
                |row| row.get(0),
            )
            .optional()?;
        let new = if let Some(previous) = previous {
            if previous != fingerprint {
                return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
            }
            None
        } else {
            crate::experiment_import::check_new_write(
                &self.db,
                &self.vault_id,
                operation,
                path,
                expected,
                &digest,
                (origin, identity),
            )?;
            let target = self.resolve(path)?;
            let current = if target.exists() {
                crate::payloads::hash_file_checked(&target, check)?
            } else {
                String::new()
            };
            if current != expected {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            let mut previous = self.entry(path)?;
            let mut identity_moves = Vec::new();
            if let Some(id) = identity {
                if let Some(retired) = previous
                    .as_ref()
                    .filter(|entry| entry.deleted && entry.file_id != id)
                {
                    identity_moves.push((
                        retired.file_id.clone(),
                        format!(".ainote/retired/{}", retired.file_id),
                    ));
                    previous = None;
                }
                if previous.is_none() {
                    let retired: Option<(String, bool)> = self
                        .db
                        .query_row("SELECT path,deleted FROM files WHERE id=?1", [id], |row| {
                            Ok((row.get(0)?, row.get(1)?))
                        })
                        .optional()?;
                    if let Some((old_path, deleted)) = retired {
                        if !deleted {
                            return Err(HostError::new("PATH_CONFLICT"));
                        }
                        let mut entry = self
                            .entry(&old_path)?
                            .ok_or_else(|| HostError::new("DATABASE_ERROR"))?;
                        entry.path = path.to_owned();
                        previous = Some(entry);
                        identity_moves.push((id.to_owned(), path.to_owned()));
                    }
                }
            }
            if identity.is_some_and(|id| previous.as_ref().is_some_and(|entry| entry.file_id != id))
            {
                return Err(HostError::new("PATH_CONFLICT"));
            }
            let file_id = previous.map_or_else(
                || {
                    identity
                        .map(str::to_owned)
                        .unwrap_or_else(|| Uuid::new_v4().to_string())
                },
                |entry| entry.file_id,
            );
            let size = content.stage_checked(self, &digest, check)?;
            Some(NewWrite {
                file_id,
                digest,
                size,
                identity_moves,
            })
        };
        check()?;
        Ok(PreparedWrite {
            workspace: self,
            operation: operation.to_owned(),
            fingerprint,
            path: path.to_owned(),
            expected: expected.to_owned(),
            origin: origin.to_owned(),
            new,
        })
    }
}

impl<'a> PreparedWrite<'a> {
    /// The native caller holds its short original lease only for this method.
    /// The callback preserves the existing desktop second authorization check.
    pub(crate) fn accept_with(
        self,
        authorize: impl FnOnce() -> Result<()>,
    ) -> Result<AcceptedWrite<'a>> {
        self.accept_via(|transaction| {
            authorize()?;
            transaction.commit()?;
            Ok(())
        })
    }

    pub(crate) fn accept_guarded(
        self,
        guard: &impl crate::workspace_commit::CommitGuard,
    ) -> Result<AcceptedWrite<'a>> {
        self.accept_via(|transaction| guard.commit(transaction))
    }

    fn accept_via(
        self,
        commit: impl FnOnce(rusqlite::Transaction<'_>) -> Result<()>,
    ) -> Result<AcceptedWrite<'a>> {
        let Self {
            workspace,
            operation,
            fingerprint,
            path,
            expected,
            origin,
            new,
        } = self;
        let new_file_id = if let Some(new) = new {
            if workspace
                .payload_ref(&operation)?
                .is_some_and(|old| old != (new.digest.clone(), new.size as i64))
            {
                return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
            }
            let tx = workspace.db.transaction()?;
            for (id, destination) in new.identity_moves {
                tx.execute(
                    "UPDATE files SET path=?2 WHERE id=?1 AND deleted=1",
                    params![id, destination],
                )?;
            }
            tx.execute(
                "INSERT OR IGNORE INTO payloads VALUES (?1,?2,?3)",
                params![operation, new.digest, new.size as i64],
            )?;
            tx.execute(
                "INSERT INTO operations VALUES (?1,?2,'pending',NULL)",
                params![operation, fingerprint],
            )?;
            tx.execute(
                "INSERT INTO journal VALUES (?1,?2,?3,?4,?5,?6,'pending')",
                params![
                    operation,
                    new.file_id,
                    path,
                    expected,
                    b"".as_slice(),
                    origin
                ],
            )?;
            commit(tx)?;
            Some(new.file_id)
        } else {
            None
        };
        Ok(AcceptedWrite {
            workspace,
            operation,
            path,
            expected,
            origin,
            new_file_id,
        })
    }
}

impl AcceptedWrite<'_> {
    pub(crate) fn complete(self) -> Result<Entry> {
        if let Some(file_id) = self.new_file_id {
            self.workspace.apply_stored_journal(
                &self.operation,
                &file_id,
                &self.path,
                &self.expected,
                &self.origin,
            )?;
            self.workspace
                .entry(&self.path)?
                .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))
        } else {
            self.workspace.recover()?;
            let receipt = self
                .workspace
                .operation(&self.operation)?
                .ok_or_else(|| HostError::new("DATABASE_ERROR"))?;
            if receipt["state"] != "committed" {
                return Err(HostError::new("RECOVERY_CONFLICT"));
            }
            serde_json::from_value(receipt["result"].clone())
                .map_err(|_| HostError::new("DATABASE_ERROR"))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::Cell, fs};

    fn metadata_counts(ws: &Workspace) -> Vec<i64> {
        ["operations", "journal", "payloads", "outbox"]
            .iter()
            .map(|table| {
                ws.db
                    .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                        row.get(0)
                    })
                    .unwrap()
            })
            .collect()
    }

    #[test]
    fn sync_write_canceled_stored_verification_keeps_original_bytes_and_metadata() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let original = ws.write("note.md", "", b"original", "local").unwrap();
        let source = root.path().join("incoming-private.bin");
        let bytes = vec![7; 256 * 1024];
        fs::write(&source, &bytes).unwrap();
        let digest = ws.sync_store_file(&source).unwrap();
        let operation = Uuid::new_v4().to_string();
        let before = metadata_counts(&ws);
        let changes = ws.db.total_changes();
        let calls = Cell::new(0);
        let error = ws
            .prepare_write_checked(
                "note.md",
                &original.hash,
                WritePayload::Stored {
                    digest: &digest,
                    size: bytes.len() as u64,
                },
                "remote",
                &operation,
                (Some(&original.file_id), &|| {
                    calls.set(calls.get() + 1);
                    if calls.get() == 5 {
                        Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
                    } else {
                        Ok(())
                    }
                }),
            )
            .err()
            .unwrap();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(calls.get(), 5);
        assert_eq!(ws.db.total_changes(), changes);
        assert_eq!(metadata_counts(&ws), before);
        assert_eq!(ws.entry("note.md").unwrap(), Some(original));
        assert_eq!(fs::read(root.path().join("note.md")).unwrap(), b"original");
        assert_eq!(fs::read(source).unwrap(), bytes);
        assert!(ws.operation(&operation).unwrap().is_none());
    }

    #[test]
    fn sync_write_cancellation_after_private_spool_has_no_accepted_operation() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let bytes = vec![9; 256 * 1024];
        let digest = hash(&bytes);
        let spool = ws.sync_spool(&digest).unwrap();
        let operation = Uuid::new_v4().to_string();
        let before = metadata_counts(&ws);
        let changes = ws.db.total_changes();
        let error = ws
            .prepare_write_checked(
                "new.md",
                "",
                WritePayload::Inline(&bytes),
                "remote",
                &operation,
                (None, &|| {
                    if spool.exists() {
                        Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
                    } else {
                        Ok(())
                    }
                }),
            )
            .err()
            .unwrap();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(fs::read(spool).unwrap(), bytes);
        assert_eq!(ws.db.total_changes(), changes);
        assert_eq!(metadata_counts(&ws), before);
        assert!(ws.entry("new.md").unwrap().is_none());
        assert!(!root.path().join("new.md").exists());
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        assert!(ws.operation(&operation).unwrap().is_none());
        assert!(!root.path().join("new.md").exists());
    }

    #[test]
    fn sync_write_dropped_preparation_never_publishes_its_private_payload_reference() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let operation = Uuid::new_v4().to_string();
        let before = metadata_counts(&ws);
        let changes = ws.db.total_changes();
        let prepared = ws
            .prepare_write_checked(
                "new.md",
                "",
                WritePayload::Inline(b"prepared"),
                "local",
                &operation,
                (None, &|| Ok(())),
            )
            .unwrap();
        let original_file_id = prepared.new.as_ref().unwrap().file_id.clone();
        drop(prepared);
        assert_eq!(ws.db.total_changes(), changes);
        assert_eq!(metadata_counts(&ws), before);
        assert!(ws.payload_ref(&operation).unwrap().is_none());
        assert!(ws.operation(&operation).unwrap().is_none());
        assert!(!root.path().join("new.md").exists());
        assert!(!original_file_id.is_empty());
        ws.recover().unwrap();
        assert!(!root.path().join("new.md").exists());
    }

    #[test]
    fn sync_write_rejected_acceptance_rolls_back_both_tombstone_moves_and_journal() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let first = ws.write("target.md", "", b"first", "local").unwrap();
        let second = ws.write("retired.md", "", b"second", "local").unwrap();
        ws.delete("target.md", &first.hash).unwrap();
        ws.delete("retired.md", &second.hash).unwrap();
        let tombstone_a = ws.entry("target.md").unwrap();
        let tombstone_b = ws.entry("retired.md").unwrap();
        let before = metadata_counts(&ws);
        let operation = Uuid::new_v4().to_string();
        let prepared = ws
            .prepare_write_checked(
                "target.md",
                "",
                WritePayload::Inline(b"restore second"),
                "remote",
                &operation,
                (Some(&second.file_id), &|| Ok(())),
            )
            .unwrap();
        assert_eq!(prepared.new.as_ref().unwrap().identity_moves.len(), 2);
        let error = prepared
            .accept_with(|| Err(HostError::new("SYNC_FOREGROUND_CHANGED")))
            .err()
            .unwrap();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(metadata_counts(&ws), before);
        assert_eq!(ws.entry("target.md").unwrap(), tombstone_a);
        assert_eq!(ws.entry("retired.md").unwrap(), tombstone_b);
        assert!(ws.operation(&operation).unwrap().is_none());
        assert!(ws.payload_ref(&operation).unwrap().is_none());
        assert!(!root.path().join("target.md").exists());
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.entry("target.md").unwrap(), tombstone_a);
        assert_eq!(ws.entry("retired.md").unwrap(), tombstone_b);
        let restored = ws
            .write_with_identity(
                "target.md",
                "",
                b"restore second",
                "remote",
                &operation,
                Some(&second.file_id),
            )
            .unwrap();
        assert_eq!(restored.file_id, second.file_id);
        assert_eq!(
            fs::read(root.path().join("target.md")).unwrap(),
            b"restore second"
        );
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "committed"
        );
        assert!(ws.entry("retired.md").unwrap().is_none());
        let retired = ws
            .entry(&format!(".ainote/retired/{}", first.file_id))
            .unwrap()
            .unwrap();
        assert_eq!(retired.file_id, first.file_id);
        assert!(retired.deleted);
    }

    #[test]
    fn sync_write_accepted_original_is_recovered_once_after_its_caller_disappears() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let initial = ws.write("note.md", "", b"before", "local").unwrap();
        let pending = ws.pending_count().unwrap();
        let operation = Uuid::new_v4().to_string();
        let accepted = ws
            .prepare_write_checked(
                "note.md",
                &initial.hash,
                WritePayload::Inline(b"accepted original"),
                "local",
                &operation,
                (Some(&initial.file_id), &|| Ok(())),
            )
            .unwrap()
            .accept_with(|| Ok(()))
            .unwrap();
        drop(accepted);
        assert_eq!(fs::read(root.path().join("note.md")).unwrap(), b"before");
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "pending"
        );
        assert_eq!(ws.pending_count().unwrap(), pending);
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let result = ws.entry("note.md").unwrap().unwrap();
        assert_eq!(result.file_id, initial.file_id);
        assert_eq!(result.revision, initial.revision + 1);
        assert_eq!(result.hash, hash(b"accepted original"));
        assert_eq!(
            fs::read(root.path().join("note.md")).unwrap(),
            b"accepted original"
        );
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "committed"
        );
        assert_eq!(ws.pending_count().unwrap(), pending + 1);
        ws.recover().unwrap();
        assert_eq!(ws.entry("note.md").unwrap(), Some(result));
        assert_eq!(ws.pending_count().unwrap(), pending + 1);
        let original: i64 = ws
            .db
            .query_row(
                "SELECT COUNT(*) FROM outbox WHERE operation_id=?1",
                [&operation],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(original, 1);
    }

    #[test]
    fn sync_write_accepted_original_preserves_external_edits_and_reports_original_conflict() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let initial = ws.write("note.md", "", b"before", "local").unwrap();
        let pending = ws.pending_count().unwrap();
        let operation = Uuid::new_v4().to_string();
        let accepted = ws
            .prepare_write_checked(
                "note.md",
                &initial.hash,
                WritePayload::Inline(b"accepted original"),
                "remote",
                &operation,
                (Some(&initial.file_id), &|| Ok(())),
            )
            .unwrap()
            .accept_with(|| Ok(()))
            .unwrap();
        fs::write(root.path().join("note.md"), b"external after acceptance").unwrap();
        assert_eq!(accepted.complete().unwrap_err().code, "RECOVERY_CONFLICT");
        assert_eq!(
            fs::read(root.path().join("note.md")).unwrap(),
            b"external after acceptance"
        );
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "conflict"
        );
        assert_eq!(ws.payload(&operation, b"").unwrap(), b"accepted original");
        assert_eq!(ws.pending_count().unwrap(), pending);
        ws.recover().unwrap();
        assert_eq!(
            fs::read(root.path().join("note.md")).unwrap(),
            b"external after acceptance"
        );
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "conflict"
        );
    }
}
