//! Prepare immutable rename/delete payloads before accepting their original journal.
use super::{hash, HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use uuid::Uuid;

pub(crate) struct PreparedMutation<'a> {
    workspace: &'a mut Workspace,
    operation: String,
    kind: String,
    path: String,
    destination: String,
    expected: String,
    origin: String,
    fingerprint: String,
    size: Option<u64>,
}

pub(crate) struct AcceptedMutation<'a> {
    workspace: &'a mut Workspace,
    operation: String,
    new: bool,
}

impl Workspace {
    pub(crate) fn prepare_mutation_checked(
        &mut self,
        kind: &str,
        path: &str,
        destination: &str,
        expected: &str,
        original: (&str, &str),
        check: &dyn Fn() -> Result<()>,
    ) -> Result<PreparedMutation<'_>> {
        let (operation, origin) = original;
        check()?;
        if !matches!(origin, "local" | "remote")
            || !matches!(kind, "rename" | "delete")
            || Uuid::parse_str(operation).is_err()
        {
            return Err(HostError::new("INVALID_OPERATION"));
        }
        let fingerprint = hash(
            &serde_json::to_vec(&(kind, path, destination, expected, origin))
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
        let size = if let Some(previous) = previous {
            if previous != fingerprint {
                return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
            }
            None
        } else {
            if kind == "rename"
                && self.resolve(destination)?.exists()
                && (path == destination || !self.paths_alias(path, destination)?)
            {
                return Err(HostError::new("PATH_CONFLICT"));
            }
            check()?;
            let source = self.resolve(path)?;
            if !source.is_file() {
                return Err(HostError::new("FILE_NOT_FOUND"));
            }
            if crate::payloads::hash_file_checked(&source, check)? != expected {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            self.entry(path)?
                .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))?;
            Some(self.snapshot_file_checked(&source, expected, check)?)
        };
        check()?;
        Ok(PreparedMutation {
            workspace: self,
            operation: operation.to_owned(),
            kind: kind.to_owned(),
            path: path.to_owned(),
            destination: destination.to_owned(),
            expected: expected.to_owned(),
            origin: origin.to_owned(),
            fingerprint,
            size,
        })
    }
}

impl<'a> PreparedMutation<'a> {
    /// Only short SQL acceptance belongs inside the original native lease.
    /// Existing accepted IDs are resumed outside that guard, without a new ID.
    pub(crate) fn accept_with(
        self,
        authorize: impl FnOnce() -> Result<()>,
    ) -> Result<AcceptedMutation<'a>> {
        let Self {
            workspace,
            operation,
            kind,
            path,
            destination,
            expected,
            origin,
            fingerprint,
            size,
        } = self;
        let new = size.is_some();
        if let Some(size) = size {
            if workspace
                .payload_ref(&operation)?
                .is_some_and(|old| old != (expected.clone(), size as i64))
            {
                return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
            }
            let tx = workspace.db.transaction()?;
            tx.execute(
                "INSERT OR IGNORE INTO payloads VALUES (?1,?2,?3)",
                params![operation, expected, size as i64],
            )?;
            tx.execute(
                "INSERT INTO operations VALUES (?1,?2,'pending',NULL)",
                params![operation, fingerprint],
            )?;
            tx.execute(
                "INSERT INTO file_ops VALUES (?1,?2,?3,?4,?5,?6,'pending',?7)",
                params![
                    operation,
                    kind,
                    path,
                    destination,
                    expected,
                    b"".as_slice(),
                    origin
                ],
            )?;
            authorize()?;
            tx.commit()?;
        }
        Ok(AcceptedMutation {
            workspace,
            operation,
            new,
        })
    }
}

impl AcceptedMutation<'_> {
    fn resume_original(&mut self) -> Result<()> {
        if !self.new {
            self.workspace.recover()?;
            if self
                .workspace
                .operation(&self.operation)?
                .is_some_and(|receipt| receipt["state"] == "conflict")
            {
                return Err(HostError::new("RECOVERY_CONFLICT"));
            }
        }
        Ok(())
    }

    /// Preserve the existing desktop preparation API used by directory recovery.
    pub(crate) fn original_id(mut self) -> Result<String> {
        self.resume_original()?;
        Ok(self.operation)
    }

    pub(crate) fn complete(mut self) -> Result<serde_json::Value> {
        self.resume_original()?;
        if !self
            .workspace
            .operation(&self.operation)?
            .is_some_and(|receipt| receipt["state"] == "committed")
        {
            self.workspace.apply_file_op(&self.operation)?;
        }
        self.workspace
            .operation(&self.operation)?
            .ok_or_else(|| HostError::new("DATABASE_ERROR"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::Cell, fs};

    fn counts(ws: &Workspace) -> Vec<i64> {
        ["operations", "file_ops", "payloads", "outbox"]
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
    fn sync_mutation_cancellation_during_original_hash_accepts_no_operation() {
        let root = tempfile::tempdir().unwrap();
        let bytes = vec![7; 256 * 1024];
        fs::write(root.path().join("original.md"), &bytes).unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let entry = ws.entry("original.md").unwrap().unwrap();
        let operation = Uuid::new_v4().to_string();
        let before = counts(&ws);
        let changes = ws.db.total_changes();
        let calls = Cell::new(0);
        let error = ws
            .prepare_mutation_checked(
                "rename",
                "original.md",
                "renamed.md",
                &entry.hash,
                (&operation, "local"),
                &|| {
                    calls.set(calls.get() + 1);
                    if calls.get() == 6 {
                        Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
                    } else {
                        Ok(())
                    }
                },
            )
            .err()
            .unwrap();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(calls.get(), 6);
        assert_eq!(ws.db.total_changes(), changes);
        assert_eq!(counts(&ws), before);
        assert_eq!(ws.entry("original.md").unwrap(), Some(entry));
        assert_eq!(fs::read(root.path().join("original.md")).unwrap(), bytes);
        assert!(!root.path().join("renamed.md").exists());
        assert!(ws.operation(&operation).unwrap().is_none());
    }

    #[test]
    fn sync_mutation_dropped_or_rejected_preparation_keeps_private_spool_unreferenced() {
        for reject in [false, true] {
            let root = tempfile::tempdir().unwrap();
            fs::write(root.path().join("original.md"), b"original").unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let entry = ws.entry("original.md").unwrap().unwrap();
            let operation = Uuid::new_v4().to_string();
            let before = counts(&ws);
            let prepared = ws
                .prepare_mutation_checked(
                    "delete",
                    "original.md",
                    "",
                    &entry.hash,
                    (&operation, "local"),
                    &|| Ok(()),
                )
                .unwrap();
            if reject {
                assert_eq!(
                    prepared
                        .accept_with(|| Err(HostError::new("SYNC_FOREGROUND_CHANGED")))
                        .err()
                        .unwrap()
                        .code,
                    "SYNC_FOREGROUND_CHANGED"
                );
            } else {
                drop(prepared);
            }
            assert_eq!(counts(&ws), before);
            assert!(ws.operation(&operation).unwrap().is_none());
            assert!(ws.payload_ref(&operation).unwrap().is_none());
            drop(ws);
            let ws = Workspace::open(root.path()).unwrap();
            assert!(ws.operation(&operation).unwrap().is_none());
            assert_eq!(
                ws.entry("original.md").unwrap().unwrap().file_id,
                entry.file_id
            );
            assert_eq!(
                fs::read(root.path().join("original.md")).unwrap(),
                b"original"
            );
        }
    }

    #[test]
    fn sync_mutation_accepted_rename_or_delete_recovers_exact_original_id_once() {
        for kind in ["rename", "delete"] {
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let entry = ws.write("original.md", "", b"original", "local").unwrap();
            let pending = ws.pending_count().unwrap();
            let operation = Uuid::new_v4().to_string();
            let destination = if kind == "rename" { "renamed.md" } else { "" };
            drop(
                ws.prepare_mutation_checked(
                    kind,
                    "original.md",
                    destination,
                    &entry.hash,
                    (&operation, "local"),
                    &|| Ok(()),
                )
                .unwrap()
                .accept_with(|| Ok(()))
                .unwrap(),
            );
            assert_eq!(
                ws.operation(&operation).unwrap().unwrap()["state"],
                "pending"
            );
            assert_eq!(
                fs::read(root.path().join("original.md")).unwrap(),
                b"original"
            );
            assert_eq!(ws.pending_count().unwrap(), pending);
            drop(ws);
            let mut ws = Workspace::open(root.path()).unwrap();
            let receipt = ws.operation(&operation).unwrap().unwrap();
            assert_eq!(receipt["state"], "committed");
            assert_eq!(receipt["result"]["file_id"], entry.file_id);
            assert_eq!(receipt["result"]["revision"], entry.revision + 1);
            assert_eq!(ws.pending_count().unwrap(), pending + 1);
            assert!(!root.path().join("original.md").exists());
            let bytes = if kind == "rename" {
                root.path().join(destination)
            } else {
                root.path().join(".ainote/trash").join(&operation)
            };
            assert_eq!(fs::read(bytes).unwrap(), b"original");
            ws.recover().unwrap();
            let repeated = ws
                .prepare_mutation_checked(
                    kind,
                    "original.md",
                    destination,
                    &entry.hash,
                    (&operation, "local"),
                    &|| Ok(()),
                )
                .unwrap()
                .accept_with(|| panic!("Original accepted intent is not a new acceptance"))
                .unwrap()
                .complete()
                .unwrap();
            assert_eq!(repeated, receipt);
            assert_eq!(ws.pending_count().unwrap(), pending + 1);
        }
    }

    #[test]
    fn sync_mutation_original_recovery_conflict_retains_both_paths_and_original_payload() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let entry = ws.write("original.md", "", b"original", "local").unwrap();
        let pending = ws.pending_count().unwrap();
        let operation = Uuid::new_v4().to_string();
        let accepted = ws
            .prepare_mutation_checked(
                "rename",
                "original.md",
                "renamed.md",
                &entry.hash,
                (&operation, "remote"),
                &|| Ok(()),
            )
            .unwrap()
            .accept_with(|| Ok(()))
            .unwrap();
        fs::write(root.path().join("renamed.md"), b"external target").unwrap();
        assert_eq!(accepted.complete().unwrap_err().code, "RECOVERY_CONFLICT");
        assert_eq!(
            fs::read(root.path().join("original.md")).unwrap(),
            b"original"
        );
        assert_eq!(
            fs::read(root.path().join("renamed.md")).unwrap(),
            b"external target"
        );
        assert_eq!(ws.payload(&operation, b"").unwrap(), b"original");
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "conflict"
        );
        assert_eq!(ws.pending_count().unwrap(), pending);
        ws.recover().unwrap();
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "conflict"
        );
        assert_eq!(
            fs::read(root.path().join("renamed.md")).unwrap(),
            b"external target"
        );
    }
}
