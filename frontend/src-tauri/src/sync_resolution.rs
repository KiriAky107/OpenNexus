//! User decisions are durable before changing files; journal IDs make restart replay safe.
use crate::{
    sync_inbox::RemoteRevision,
    workspace::{hash, Entry, HostError, Result, Workspace},
};
use rusqlite::{params, OptionalExtension};
use std::fs;
use uuid::Uuid;

impl Workspace {
    pub fn sync_resolve(
        &mut self,
        binding: &str,
        sequence: i64,
        choice: &str,
        destination: &str,
        expected: &str,
    ) -> Result<()> {
        self.check_binding(binding)?;
        if !matches!(choice, "local" | "remote" | "copy") {
            return Err(HostError::new("SYNC_RESOLUTION_INVALID"));
        }
        let existing: Option<(String,String,String)> = self.db.query_row("SELECT choice,destination,expected FROM sync_resolutions WHERE binding=?1 AND sequence=?2",params![binding,sequence],|r| Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
        if let Some(previous) = existing {
            if previous
                != (
                    choice.to_owned(),
                    destination.to_owned(),
                    expected.to_owned(),
                )
            {
                return Err(HostError::new("SYNC_RESOLUTION_CHANGED"));
            }
        } else {
            let (path,remote): (String,String) = self.db.query_row("SELECT local_path,remote FROM sync_conflicts WHERE binding=?1 AND sequence=?2 AND state='open'",params![binding,sequence],|r| Ok((r.get(0)?,r.get(1)?)))?;
            let revision: RemoteRevision = serde_json::from_str(&remote)
                .map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
            let path = self.path_for_id(&revision.file_id).unwrap_or(path);
            let source = self.resolve(&path)?;
            let current = if source.is_file() {
                self.sync_store_bytes(&fs::read(source)?)?
            } else {
                String::new()
            };
            if current != expected {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            if !current.is_empty() && self.path_for_id(&revision.file_id).is_err() {
                return Err(HostError::new("SYNC_CONFLICT_REQUIRES_RENAME"));
            }
            if choice == "copy" {
                if current.is_empty() || self.resolve(destination)?.exists() {
                    return Err(HostError::new("PATH_CONFLICT"));
                }
            } else if !destination.is_empty() {
                return Err(HostError::new("SYNC_RESOLUTION_INVALID"));
            }
            self.db.execute(
                "INSERT INTO sync_resolutions VALUES (?1,?2,?3,?4,?5,?6,?7,?8,'pending')",
                params![
                    binding,
                    sequence,
                    choice,
                    destination,
                    expected,
                    Uuid::new_v4().to_string(),
                    Uuid::new_v4().to_string(),
                    Uuid::new_v4().to_string()
                ],
            )?;
        }
        self.sync_apply_resolution(binding, sequence)
    }
    pub fn sync_resume_resolutions(&mut self, binding: &str) -> Result<()> {
        self.check_binding(binding)?;
        let pending = {
            let mut statement=self.db.prepare("SELECT sequence FROM sync_resolutions WHERE binding=?1 AND state='pending' ORDER BY sequence")?;
            let values = statement
                .query_map([binding], |r| r.get::<_, i64>(0))?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            values
        };
        for sequence in pending {
            self.sync_apply_resolution(binding, sequence)?;
        }
        Ok(())
    }
    fn sync_apply_resolution(&mut self, binding: &str, sequence: i64) -> Result<()> {
        self.check_binding(binding)?;
        let (choice,destination,expected,operation,rename,copy,state): (String,String,String,String,String,String,String) = self.db.query_row("SELECT choice,destination,expected,operation_id,rename_id,copy_id,state FROM sync_resolutions WHERE binding=?1 AND sequence=?2",params![binding,sequence],|r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?)))?;
        if state == "completed" {
            return Ok(());
        }
        let (stored_path, remote): (String, String) = self.db.query_row(
            "SELECT local_path,remote FROM sync_conflicts WHERE binding=?1 AND sequence=?2",
            params![binding, sequence],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let revision: RemoteRevision =
            serde_json::from_str(&remote).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
        let head: i64 = self.db.query_row(
            "SELECT revision FROM sync_heads WHERE binding=?1 AND file_id=?2",
            params![binding, revision.file_id],
            |r| r.get(0),
        )?;
        if head != sequence {
            return Err(HostError::new("SYNC_CONFLICT_CHANGED"));
        }
        let path = self.path_for_id(&revision.file_id).unwrap_or(stored_path);
        if !self
            .operation(&operation)?
            .is_some_and(|value| value["state"] == "committed")
        {
            let source = self.resolve(&path)?;
            let current = if source.is_file() {
                hash(&fs::read(source)?)
            } else {
                String::new()
            };
            if current != expected {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            if choice == "copy" {
                let content = fs::read(self.sync_spool(&expected)?)?;
                self.write_operation(&destination, "", &content, "local", &copy)?;
            }
            if choice == "local" {
                if current.is_empty() {
                    self.sync_delete_intent(&revision, &operation)?;
                } else {
                    let content = fs::read(self.sync_spool(&expected)?)?;
                    self.write_operation(&path, &expected, &content, "local", &operation)?;
                }
            } else if revision.operation == "delete" {
                if !current.is_empty() {
                    self.mutate_with_origin("delete", &path, "", &current, &operation, "remote")?;
                }
            } else {
                if path != revision.path && !current.is_empty() {
                    self.mutate_with_origin(
                        "rename",
                        &path,
                        &revision.path,
                        &current,
                        &rename,
                        "remote",
                    )?;
                }
                let content = fs::read(
                    self.sync_spool(
                        revision
                            .hash
                            .as_deref()
                            .ok_or_else(|| HostError::new("SYNC_RESPONSE_INVALID"))?,
                    )?,
                )?;
                self.write_with_identity(
                    &revision.path,
                    &current,
                    &content,
                    "remote",
                    &operation,
                    Some(&revision.file_id),
                )?;
            }
        }
        let tx = self.db.transaction()?;
        tx.execute("UPDATE outbox SET state='archived' WHERE file_id=?1 AND state IN ('pending','queued') AND operation_id!=?2",params![revision.file_id,operation])?;
        tx.execute("UPDATE sync_jobs SET state='archived' WHERE binding=?1 AND file_id=?2 AND state!='acked' AND operation_id!=?3",params![binding,revision.file_id,operation])?;
        tx.execute(
            "UPDATE sync_conflicts SET state='resolved' WHERE binding=?1 AND sequence=?2",
            params![binding, sequence],
        )?;
        tx.execute(
            "UPDATE sync_resolutions SET state='completed' WHERE binding=?1 AND sequence=?2",
            params![binding, sequence],
        )?;
        tx.commit()?;
        Ok(())
    }
    fn sync_delete_intent(&mut self, revision: &RemoteRevision, operation: &str) -> Result<()> {
        let tx = self.db.transaction()?;
        tx.execute(
            "UPDATE files SET deleted=1,revision=revision+1 WHERE id=?1",
            [&revision.file_id],
        )?;
        tx.execute(
            "UPDATE sync_observed SET deleted=1 WHERE file_id=?1",
            [&revision.file_id],
        )?;
        let entry: Entry = tx.query_row(
            "SELECT id,path,hash,revision FROM files WHERE id=?1",
            [&revision.file_id],
            |r| {
                Ok(Entry {
                    file_id: r.get(0)?,
                    path: r.get(1)?,
                    hash: r.get(2)?,
                    revision: r.get(3)?,
                    deleted: true,
                    is_folder: false,
                })
            },
        )?;
        tx.execute(
            "INSERT INTO outbox VALUES (?1,?2,?3,?4,'','delete',X'','pending')",
            params![operation, entry.file_id, entry.revision, revision.path],
        )?;
        let encoded =
            serde_json::to_string(&entry).map_err(|_| HostError::new("DATABASE_ERROR"))?;
        tx.execute(
            "INSERT INTO operations VALUES (?1,?2,'committed',?3)",
            params![
                operation,
                hash(format!("sync-delete:{}:{}", revision.file_id, revision.sequence).as_bytes()),
                encoded
            ],
        )?;
        tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn receive(ws: &mut Workspace, binding: &str, revision: &RemoteRevision) {
        ws.sync_set_boundary(binding, revision.sequence).unwrap();
        ws.sync_stage(binding, revision).unwrap();
        ws.sync_apply_pending(binding).unwrap();
    }
    #[test]
    fn decisions_recover_after_file_commit_without_duplicate_outbox() {
        for choice in ["local", "remote", "copy"] {
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let binding = ws
                .sync_bind_download("https://sync.example", "remote-vault", "account")
                .unwrap();
            let file_id = Uuid::new_v4().to_string();
            let mut remote = RemoteRevision {
                vault_id: "remote-vault".into(),
                sequence: 1,
                file_id,
                base_revision: 0,
                path: "a.md".into(),
                operation: "put".into(),
                hash: Some(ws.sync_store_bytes(b"one").unwrap()),
                size: 3,
                operation_id: Uuid::new_v4().to_string(),
            };
            receive(&mut ws, &binding.id, &remote);
            let local = ws.write("a.md", &hash(b"one"), b"local", "local").unwrap();
            ws.sync_capture(&binding.id).unwrap();
            let stale = ws.sync_next(&binding.id).unwrap().unwrap();
            remote.sequence = 2;
            remote.base_revision = 1;
            remote.hash = Some(ws.sync_store_bytes(b"two").unwrap());
            remote.operation_id = Uuid::new_v4().to_string();
            receive(&mut ws, &binding.id, &remote);
            assert!(ws.sync_next(&binding.id).unwrap().is_none());
            assert_eq!(
                ws.sync_commit_payload(&stale).unwrap_err().code,
                "SYNC_OPERATION_SUPERSEDED"
            );
            let operation = Uuid::new_v4().to_string();
            let copy = Uuid::new_v4().to_string();
            ws.db
                .execute(
                    "INSERT INTO sync_resolutions VALUES (?1,2,?2,?3,?4,?5,?6,?7,'pending')",
                    params![
                        binding.id,
                        choice,
                        if choice == "copy" { "copy.md" } else { "" },
                        local.hash,
                        operation,
                        Uuid::new_v4().to_string(),
                        copy
                    ],
                )
                .unwrap();
            // Simulate a crash after the filesystem journal commits but before the resolution transaction.
            if choice == "copy" {
                ws.write_operation("copy.md", "", b"local", "local", &copy)
                    .unwrap();
            }
            if choice == "local" {
                ws.write_operation("a.md", &local.hash, b"local", "local", &operation)
                    .unwrap();
            } else {
                ws.write_with_identity(
                    "a.md",
                    &local.hash,
                    b"two",
                    "remote",
                    &operation,
                    Some(&remote.file_id),
                )
                .unwrap();
            }
            drop(ws);
            let mut ws = Workspace::open(root.path()).unwrap();
            ws.sync_resume_resolutions(&binding.id).unwrap();
            ws.sync_resume_resolutions(&binding.id).unwrap();
            assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
            assert_eq!(
                ws.read("a.md").unwrap().content,
                if choice == "local" { "local" } else { "two" }
            );
            assert_eq!(
                ws.pending_count().unwrap(),
                if choice == "remote" { 0 } else { 1 }
            );
            if choice == "copy" {
                assert_eq!(ws.read("copy.md").unwrap().content, "local");
            }
            assert_eq!(
                ws.sync_commit_payload(&stale).unwrap_err().code,
                "SYNC_OPERATION_SUPERSEDED"
            );
        }
    }
    #[test]
    fn deletion_resolution_and_remote_recreation_preserve_identity() {
        for choice in ["local", "remote", "copy"] {
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let binding = ws
                .sync_bind_download("https://sync.example", "remote-vault", "account")
                .unwrap();
            let mut remote = RemoteRevision {
                vault_id: "remote-vault".into(),
                sequence: 1,
                file_id: Uuid::new_v4().to_string(),
                base_revision: 0,
                path: "a.md".into(),
                operation: "put".into(),
                hash: Some(ws.sync_store_bytes(b"one").unwrap()),
                size: 3,
                operation_id: Uuid::new_v4().to_string(),
            };
            receive(&mut ws, &binding.id, &remote);
            let original = remote.file_id.clone();
            let local = ws.write("a.md", &hash(b"one"), b"local", "local").unwrap();
            remote.sequence = 2;
            remote.base_revision = 1;
            remote.operation = "delete".into();
            remote.hash = None;
            remote.size = 0;
            remote.operation_id = Uuid::new_v4().to_string();
            receive(&mut ws, &binding.id, &remote);
            ws.sync_resolve(
                &binding.id,
                2,
                choice,
                if choice == "copy" { "copy.md" } else { "" },
                &local.hash,
            )
            .unwrap();
            if choice == "local" {
                assert_eq!(ws.read("a.md").unwrap().content, "local");
                continue;
            }
            assert!(!root.path().join("a.md").exists());
            remote.sequence = 3;
            remote.base_revision = 0;
            remote.file_id = Uuid::new_v4().to_string();
            remote.operation = "put".into();
            remote.hash = Some(ws.sync_store_bytes(b"new").unwrap());
            remote.size = 3;
            remote.operation_id = Uuid::new_v4().to_string();
            receive(&mut ws, &binding.id, &remote);
            assert_eq!(ws.read("a.md").unwrap().entry.file_id, remote.file_id);
            // A tombstoned identity can reappear at another free path.
            remote.sequence = 4;
            remote.base_revision = 2;
            remote.file_id = original.clone();
            remote.path = "restored.md".into();
            remote.operation_id = Uuid::new_v4().to_string();
            receive(&mut ws, &binding.id, &remote);
            assert_eq!(ws.read("restored.md").unwrap().entry.file_id, original);
            assert_eq!(ws.read("a.md").unwrap().content, "new");
        }
    }
    #[test]
    fn keep_local_deletion_queues_new_delete_on_remote_head() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example", "remote-vault", "account")
            .unwrap();
        let mut remote = RemoteRevision {
            vault_id: "remote-vault".into(),
            sequence: 1,
            file_id: Uuid::new_v4().to_string(),
            base_revision: 0,
            path: "a.md".into(),
            operation: "put".into(),
            hash: Some(ws.sync_store_bytes(b"one").unwrap()),
            size: 3,
            operation_id: Uuid::new_v4().to_string(),
        };
        receive(&mut ws, &binding.id, &remote);
        ws.mutate_operation(
            "delete",
            "a.md",
            "",
            &hash(b"one"),
            &Uuid::new_v4().to_string(),
        )
        .unwrap();
        remote.sequence = 2;
        remote.base_revision = 1;
        remote.hash = Some(ws.sync_store_bytes(b"two").unwrap());
        remote.operation_id = Uuid::new_v4().to_string();
        receive(&mut ws, &binding.id, &remote);
        ws.sync_resolve(&binding.id, 2, "local", "", "").unwrap();
        ws.sync_capture(&binding.id).unwrap();
        let job = ws.sync_next(&binding.id).unwrap().unwrap();
        let payload = ws.sync_commit_payload(&job).unwrap();
        assert_eq!(payload["base_revision"], 2);
        assert_eq!(payload["operation"], "delete");
        assert_eq!(ws.pending_count().unwrap(), 1);
    }
}
