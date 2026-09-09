//! User decisions are durable before changing files; journal IDs make restart replay safe.
use crate::workspace::hash;
use crate::{
    sync_inbox::RemoteRevision,
    workspace::{Entry, HostError, Result, Workspace},
};
use rusqlite::{params, OptionalExtension};
use std::fs;
use uuid::Uuid;

impl Workspace {
    pub(crate) fn sync_conflict_local_path(&self, revision: &RemoteRevision) -> Result<String> {
        let identity_path = self.path_for_id(&revision.file_id).ok();
        let destination_occupied = self
            .entry(&revision.path)?
            .is_some_and(|entry| !entry.deleted && entry.file_id != revision.file_id);
        if destination_occupied
            && identity_path
                .as_deref()
                .is_some_and(|path| path != revision.path)
        {
            return Ok(revision.path.clone());
        }
        Ok(identity_path.unwrap_or_else(|| revision.path.clone()))
    }

    pub fn sync_resolve(
        &mut self,
        binding: &str,
        sequence: i64,
        choice: &str,
        destination: &str,
        expected: &str,
    ) -> Result<()> {
        self.check_binding(binding)?;
        let scope_path: Option<String> = self
            .db
            .query_row(
                "SELECT local_path FROM sync_conflicts WHERE binding=?1 AND sequence=?2",
                params![binding, sequence],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(path) = scope_path {
            if !self.sync_path_enabled(&path)? {
                return Err(HostError::new("SYNC_SCOPE_DISABLED"));
            }
        }
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
            let stored_path: String = self.db.query_row("SELECT local_path FROM sync_conflicts WHERE binding=?1 AND sequence=?2 AND state='open'",params![binding,sequence],|r| r.get(0))?;
            let path = stored_path;
            let source = self.resolve(&path)?;
            let current = if source.is_file() {
                self.sync_store_file(&source)?
            } else {
                String::new()
            };
            if current != expected {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            if choice == "copy" {
                if current.is_empty() || self.resolve(destination)?.exists() {
                    return Err(HostError::new("PATH_CONFLICT"));
                }
                if !crate::sync_discovery::allowed(destination) {
                    return Err(HostError::new("SYNC_PATH_DENIED"));
                }
                // Validate the intended record path before freezing the decision.
                // A typo must not leave an unchangeable, unappliable resolution.
                if crate::records::is_record(destination) {
                    let spool = self.sync_spool(&current)?;
                    let size = fs::metadata(&spool)?.len();
                    let mut file = crate::payloads::open_verified(&spool, &current, size)?;
                    crate::payloads::validate_record(&mut file, destination, size)?;
                }
            } else if !destination.is_empty() {
                return Err(HostError::new("SYNC_RESOLUTION_INVALID"));
            }
            self.db.execute(
                "INSERT INTO sync_resolutions (binding,sequence,choice,destination,expected,operation_id,rename_id,copy_id,state,retire_id,restore_id) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,'pending',?9,?10)",
                params![
                    binding,
                    sequence,
                    choice,
                    destination,
                    expected,
                    Uuid::new_v4().to_string(),
                    Uuid::new_v4().to_string(),
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
        let (choice,destination,expected,operation,rename,copy,state,mut retire,mut restore): (String,String,String,String,String,String,String,String,String) = self.db.query_row("SELECT choice,destination,expected,operation_id,rename_id,copy_id,state,retire_id,restore_id FROM sync_resolutions WHERE binding=?1 AND sequence=?2",params![binding,sequence],|r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?,r.get(7)?,r.get(8)?)))?;
        if retire.is_empty() || restore.is_empty() {
            retire = Uuid::new_v4().to_string();
            restore = Uuid::new_v4().to_string();
            self.db.execute(
                "UPDATE sync_resolutions SET retire_id=CASE WHEN retire_id='' THEN ?3 ELSE retire_id END,restore_id=CASE WHEN restore_id='' THEN ?4 ELSE restore_id END WHERE binding=?1 AND sequence=?2",
                params![binding, sequence, retire, restore],
            )?;
            let ids: (String, String) = self.db.query_row(
                "SELECT retire_id,restore_id FROM sync_resolutions WHERE binding=?1 AND sequence=?2",
                params![binding, sequence],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )?;
            retire = ids.0;
            restore = ids.1;
        }
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
        if !self.sync_path_enabled(&revision.path)? {
            return Ok(());
        }
        let head: i64 = self.db.query_row(
            "SELECT revision FROM sync_heads WHERE binding=?1 AND file_id=?2",
            params![binding, revision.file_id],
            |r| r.get(0),
        )?;
        if head != sequence {
            return Err(HostError::new("SYNC_CONFLICT_CHANGED"));
        }
        // local_path is frozen when the conflict is recorded. Recomputing it from
        // file identity after a partial resolution can select a different file.
        let path = stored_path;
        if !self
            .operation(&operation)?
            .is_some_and(|value| value["state"] == "committed")
        {
            let conflict_source = self.resolve(&path)?;
            let current = if conflict_source.is_file() {
                crate::payloads::hash_file(&conflict_source)?
            } else {
                String::new()
            };
            let target_retired = self.operation(&retire)?.is_some_and(|value| {
                value["state"] == "committed" && value["result"]["deleted"] == true
            });
            let source_renamed = self
                .operation(&rename)?
                .is_some_and(|value| value["state"] == "committed");
            if current != expected && !target_retired && !source_renamed {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            if choice == "copy" {
                let size = fs::metadata(self.sync_spool(&expected)?)?.len();
                self.write_spooled_with_identity(
                    &destination,
                    "",
                    (&expected, size),
                    "local",
                    &copy,
                    None,
                )?;
            }
            let target_collision = target_retired
                || (path == revision.path
                    && self
                        .entry(&path)?
                        .is_some_and(|entry| !entry.deleted && entry.file_id != revision.file_id));
            if target_collision && !target_retired {
                self.mutate_with_origin("delete", &path, "", &current, &retire, "remote")?;
            }
            let source_path = self
                .path_for_id(&revision.file_id)
                .unwrap_or_else(|_| revision.path.clone());
            let source = self.resolve(&source_path)?;
            let mut source_hash = if source.is_file() {
                crate::payloads::hash_file(&source)?
            } else {
                String::new()
            };
            if target_collision
                && source_path != revision.path
                && !source_hash.is_empty()
                && !source_renamed
            {
                // The deleted target still owns its unique database path until the
                // final identity-aware write retires that tombstone. Retire the
                // incoming identity at its old path, then resurrect it at the
                // target with the frozen remote or chosen-local bytes.
                self.mutate_with_origin(
                    "delete",
                    &source_path,
                    "",
                    &source_hash,
                    &rename,
                    "remote",
                )?;
                source_hash.clear();
            }
            if choice == "local" {
                if expected.is_empty() {
                    self.sync_delete_intent(&revision, &operation)?;
                } else {
                    let destination = if target_collision {
                        revision.path.as_str()
                    } else {
                        path.as_str()
                    };
                    let destination_hash = if target_collision {
                        source_hash.as_str()
                    } else {
                        current.as_str()
                    };
                    let size = fs::metadata(self.sync_spool(&expected)?)?.len();
                    self.write_spooled_with_identity(
                        destination,
                        destination_hash,
                        (&expected, size),
                        "local",
                        &operation,
                        Some(&revision.file_id),
                    )?;
                }
            } else if revision.operation == "delete" {
                if !source_hash.is_empty() {
                    self.mutate_with_origin(
                        "delete",
                        &source_path,
                        "",
                        &source_hash,
                        &operation,
                        "remote",
                    )?;
                }
            } else {
                if !target_collision
                    && source_path != revision.path
                    && !source_hash.is_empty()
                    && !source_renamed
                {
                    self.mutate_with_origin(
                        "rename",
                        &source_path,
                        &revision.path,
                        &source_hash,
                        &rename,
                        "remote",
                    )?;
                    source_hash = crate::payloads::hash_file(&self.resolve(&revision.path)?)?;
                }
                let digest = revision
                    .hash
                    .as_deref()
                    .ok_or_else(|| HostError::new("SYNC_RESPONSE_INVALID"))?;
                self.write_spooled_with_identity(
                    &revision.path,
                    &source_hash,
                    (digest, revision.size as u64),
                    "remote",
                    &operation,
                    Some(&revision.file_id),
                )?;
            }
        }
        let retired = self.operation(&retire)?.and_then(|value| {
            (value["state"] == "committed" && value["result"]["deleted"] == true)
                .then(|| value["result"]["file_id"].as_str().map(str::to_owned))
                .flatten()
        });
        let mut restored_retired = false;
        if let Some(retired_id) = retired.as_deref().filter(|id| *id != revision.file_id) {
            let remote_head: Option<(String, String)> = self
                .db
                .query_row(
                    "SELECT path,hash FROM sync_heads WHERE binding=?1 AND file_id=?2 AND hash!=''",
                    params![binding, retired_id],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .optional()?;
            if let Some((head_path, head_hash)) =
                remote_head.filter(|(head_path, _)| head_path != &revision.path)
            {
                if !self
                    .operation(&restore)?
                    .is_some_and(|value| value["state"] == "committed")
                {
                    if self.resolve(&head_path)?.exists() {
                        return Err(HostError::new("REVISION_CONFLICT"));
                    }
                    let size = fs::metadata(self.sync_spool(&head_hash)?)?.len();
                    self.write_spooled_with_identity(
                        &head_path,
                        "",
                        (&head_hash, size),
                        "remote",
                        &restore,
                        Some(retired_id),
                    )?;
                }
                restored_retired = true;
            }
        }
        let tx = self.db.transaction()?;
        if let Some(retired) = retired.filter(|id| id != &revision.file_id) {
            if !restored_retired {
                tx.execute(
                    "UPDATE file_aliases SET file_id=?1 WHERE file_id=?2",
                    params![revision.file_id, retired],
                )?;
                tx.execute(
                    "INSERT OR REPLACE INTO file_aliases VALUES (?1,?2)",
                    params![retired, revision.file_id],
                )?;
            }
            tx.execute("UPDATE outbox SET state='archived' WHERE file_id=?1 AND state IN ('pending','queued')",[&retired])?;
            tx.execute("UPDATE sync_jobs SET state='archived' WHERE binding=?1 AND file_id=?2 AND state!='acked'",params![binding,retired])?;
        }
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
    fn invalid_record_copy_destination_does_not_freeze_conflict_decision() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.sync_set_optional_scope(crate::sync_scope::OptionalScope {
            persona: true,
            layout: true,
            ..Default::default()
        })
        .unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example", "remote-vault", "account")
            .unwrap();
        let record = |name: &str| {
            serde_json::to_vec(&serde_json::json!({"schema":1,"kind":"persona","id":"default","data":{"version":1,"name":name,"system_prompt":"","dialogue_pairs":[]}})).unwrap()
        };
        let base = record("base");
        let local = record("local");
        let remote = record("remote");
        let path = crate::records::path_for("persona", "default").unwrap();
        let mut revision = RemoteRevision {
            vault_id: "remote-vault".into(),
            sequence: 1,
            file_id: Uuid::new_v4().to_string(),
            base_revision: 0,
            path: path.clone(),
            operation: "put".into(),
            hash: Some(ws.sync_store_bytes(&base).unwrap()),
            size: base.len() as i64,
            operation_id: Uuid::new_v4().to_string(),
        };
        receive(&mut ws, &binding.id, &revision);
        let local_hash = ws
            .write(&path, revision.hash.as_deref().unwrap(), &local, "local")
            .unwrap()
            .hash;
        revision.sequence = 2;
        revision.base_revision = 1;
        revision.hash = Some(ws.sync_store_bytes(&remote).unwrap());
        revision.size = remote.len() as i64;
        revision.operation_id = Uuid::new_v4().to_string();
        receive(&mut ws, &binding.id, &revision);
        for destination in ["copy.json", "opennexus-records/v1/layout/sidebars.json"] {
            assert!(ws
                .sync_resolve(&binding.id, 2, "copy", destination, &local_hash)
                .is_err());
            assert_eq!(
                ws.db
                    .query_row("SELECT COUNT(*) FROM sync_resolutions", [], |r| r
                        .get::<_, i64>(0))
                    .unwrap(),
                0
            );
            assert_eq!(fs::read(root.path().join(&path)).unwrap(), local);
        }
        ws.sync_resolve(
            &binding.id,
            2,
            "copy",
            "attachments/persona-copy.txt",
            &local_hash,
        )
        .unwrap();
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        assert_eq!(
            fs::read(root.path().join("attachments/persona-copy.txt")).unwrap(),
            local
        );
        assert_eq!(fs::read(root.path().join(path)).unwrap(), remote);
        assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
    }
    #[test]
    fn hundred_mib_conflicts_resolve_all_choices_and_reopen_without_duplicate_jobs() {
        use std::io::Write;
        let fixtures = tempfile::tempdir().unwrap();
        for (name, value) in [("local", 31u8), ("remote", 47u8)] {
            let mut file = fs::File::create(fixtures.path().join(name)).unwrap();
            let block = vec![value; 64 * 1024];
            for _ in 0..1600 {
                file.write_all(&block).unwrap();
            }
            file.sync_all().unwrap();
        }
        for choice in ["local", "remote", "copy"] {
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let binding = ws
                .sync_bind_download("https://sync.example", "remote-vault", "account")
                .unwrap();
            let mut revision = RemoteRevision {
                vault_id: "remote-vault".into(),
                sequence: 1,
                file_id: Uuid::new_v4().to_string(),
                base_revision: 0,
                path: "attachments/large.bin".into(),
                operation: "put".into(),
                hash: Some(ws.sync_store_bytes(b"base").unwrap()),
                size: 4,
                operation_id: Uuid::new_v4().to_string(),
            };
            receive(&mut ws, &binding.id, &revision);
            let local_hash = ws.sync_store_file(&fixtures.path().join("local")).unwrap();
            let remote_hash = ws.sync_store_file(&fixtures.path().join("remote")).unwrap();
            ws.write_spooled_with_identity(
                &revision.path,
                revision.hash.as_deref().unwrap(),
                (&local_hash, 100 * 1024 * 1024),
                "local",
                &Uuid::new_v4().to_string(),
                Some(&revision.file_id),
            )
            .unwrap();
            ws.sync_capture(&binding.id).unwrap();
            let stale = ws.sync_next(&binding.id).unwrap().unwrap();
            revision.sequence = 2;
            revision.base_revision = 1;
            revision.hash = Some(remote_hash.clone());
            revision.size = 100 * 1024 * 1024;
            revision.operation_id = Uuid::new_v4().to_string();
            receive(&mut ws, &binding.id, &revision);
            assert_eq!(
                ws.sync_conflicts(&binding.id).unwrap()[0]["current_hash"],
                local_hash
            );
            let destination = if choice == "copy" {
                "attachments/copy.bin"
            } else {
                ""
            };
            assert_eq!(
                ws.sync_resolve(&binding.id, 2, choice, destination, &hash(b"stale"))
                    .unwrap_err()
                    .code,
                "REVISION_CONFLICT"
            );
            ws.sync_resolve(&binding.id, 2, choice, destination, &local_hash)
                .unwrap();
            drop(ws);
            let mut ws = Workspace::open(root.path()).unwrap();
            ws.sync_resume_resolutions(&binding.id).unwrap();
            ws.sync_resolve(&binding.id, 2, choice, destination, &local_hash)
                .unwrap();
            assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
            assert_eq!(
                crate::payloads::hash_file(&root.path().join(&revision.path)).unwrap(),
                if choice == "local" {
                    local_hash.clone()
                } else {
                    remote_hash
                }
            );
            assert_eq!(
                ws.pending_count().unwrap(),
                if choice == "remote" { 0 } else { 1 }
            );
            assert_eq!(
                ws.entry(&revision.path).unwrap().unwrap().file_id,
                revision.file_id
            );
            if choice == "copy" {
                assert_eq!(
                    crate::payloads::hash_file(&root.path().join(destination)).unwrap(),
                    local_hash
                );
                assert_ne!(
                    ws.entry(destination).unwrap().unwrap().file_id,
                    revision.file_id
                );
            }
            assert_eq!(
                ws.sync_commit_payload(&stale).unwrap_err().code,
                "SYNC_OPERATION_SUPERSEDED"
            );
        }
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
                    "INSERT INTO sync_resolutions (binding,sequence,choice,destination,expected,operation_id,rename_id,copy_id,state,retire_id,restore_id) VALUES (?1,2,?2,?3,?4,?5,?6,?7,'pending',?8,?9)",
                    params![
                        binding.id,
                        choice,
                        if choice == "copy" { "copy.md" } else { "" },
                        local.hash,
                        operation,
                        Uuid::new_v4().to_string(),
                        copy,
                        Uuid::new_v4().to_string(),
                        Uuid::new_v4().to_string()
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
    #[test]
    fn same_path_independent_identities_resolve_and_recover_twenty_rounds() {
        for _ in 0..20 {
            for choice in ["local", "remote", "copy"] {
                for crash in [false, true] {
                    let root = tempfile::tempdir().unwrap();
                    let mut ws = Workspace::open(root.path()).unwrap();
                    let local = ws.write("a.md", "", b"local", "local").unwrap();
                    let binding = ws
                        .sync_bind_empty("https://sync.example", "remote-vault", "account")
                        .unwrap();
                    let remote = RemoteRevision {
                        vault_id: "remote-vault".into(),
                        sequence: 1,
                        file_id: Uuid::new_v4().to_string(),
                        base_revision: 0,
                        path: "a.md".into(),
                        operation: "put".into(),
                        hash: Some(ws.sync_store_bytes(b"remote").unwrap()),
                        size: 6,
                        operation_id: Uuid::new_v4().to_string(),
                    };
                    receive(&mut ws, &binding.id, &remote);
                    if crash {
                        let operation = Uuid::new_v4().to_string();
                        let rename = Uuid::new_v4().to_string();
                        let copy = Uuid::new_v4().to_string();
                        let retire = Uuid::new_v4().to_string();
                        ws.db.execute("INSERT INTO sync_resolutions (binding,sequence,choice,destination,expected,operation_id,rename_id,copy_id,state,retire_id,restore_id) VALUES (?1,1,?2,?3,?4,?5,?6,?7,'pending',?8,?9)",params![binding.id,choice,if choice=="copy" {"copy.md"} else {""},local.hash,operation,rename,copy,retire,Uuid::new_v4().to_string()]).unwrap();
                        if choice == "copy" {
                            ws.write_operation("copy.md", "", b"local", "local", &copy)
                                .unwrap();
                        }
                        ws.mutate_with_origin("delete", "a.md", "", &local.hash, &retire, "remote")
                            .unwrap();
                        drop(ws);
                        ws = Workspace::open(root.path()).unwrap();
                        ws.sync_resume_resolutions(&binding.id).unwrap();
                    } else {
                        ws.sync_resolve(
                            &binding.id,
                            1,
                            choice,
                            if choice == "copy" { "copy.md" } else { "" },
                            &local.hash,
                        )
                        .unwrap();
                    }
                    let actual = ws.read("a.md").unwrap();
                    assert_eq!(actual.entry.file_id, remote.file_id);
                    assert_eq!(
                        actual.content,
                        if choice == "local" { "local" } else { "remote" }
                    );
                    assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
                    assert_eq!(ws.path_for_id(&local.file_id).unwrap(), "a.md");
                    ws.sync_capture(&binding.id).unwrap();
                    let job = ws.sync_next(&binding.id).unwrap();
                    if choice == "remote" {
                        assert!(job.is_none());
                    } else {
                        assert_ne!(job.unwrap().file_id, local.file_id);
                    }
                }
            }
        }
    }

    #[test]
    fn same_target_renames_preserve_the_occupant_for_all_choices_twenty_rounds() {
        for round in 0..20 {
            let choice = ["local", "remote", "copy"][round % 3];
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let binding = ws
                .sync_bind_download("https://sync.example", "remote-vault", "account")
                .unwrap();
            let mut left = RemoteRevision {
                vault_id: "remote-vault".into(),
                sequence: 1,
                file_id: Uuid::new_v4().to_string(),
                base_revision: 0,
                path: "left.md".into(),
                operation: "put".into(),
                hash: Some(ws.sync_store_bytes(b"left-content").unwrap()),
                size: 12,
                operation_id: Uuid::new_v4().to_string(),
            };
            let right = RemoteRevision {
                vault_id: "remote-vault".into(),
                sequence: 2,
                file_id: Uuid::new_v4().to_string(),
                base_revision: 0,
                path: "right.md".into(),
                operation: "put".into(),
                hash: Some(ws.sync_store_bytes(b"right-content").unwrap()),
                size: 13,
                operation_id: Uuid::new_v4().to_string(),
            };
            receive(&mut ws, &binding.id, &left);
            receive(&mut ws, &binding.id, &right);
            let right_hash = right.hash.as_deref().unwrap();
            ws.rename("right.md", "target.md", right_hash).unwrap();

            left.sequence = 3;
            left.base_revision = 1;
            left.path = "target.md".into();
            left.operation_id = Uuid::new_v4().to_string();
            receive(&mut ws, &binding.id, &left);

            let conflict = ws.sync_conflicts(&binding.id).unwrap().remove(0);
            assert_eq!(conflict["current_path"], "target.md");
            assert_eq!(conflict["current_hash"], right_hash);
            assert!(ws.sync_spool(right_hash).unwrap().is_file());
            assert!(ws
                .sync_spool(left.hash.as_deref().unwrap())
                .unwrap()
                .is_file());
            let copy = format!("copies/target-{round}.md");
            ws.sync_resolve(
                &binding.id,
                3,
                choice,
                if choice == "copy" { &copy } else { "" },
                right_hash,
            )
            .unwrap();
            drop(ws);

            let mut ws = Workspace::open(root.path()).unwrap();
            ws.sync_resume_resolutions(&binding.id).unwrap();
            assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
            assert!(!root.path().join("left.md").exists());
            let target = ws.read("target.md").unwrap();
            assert_eq!(target.entry.file_id, left.file_id);
            assert_eq!(
                target.content,
                if choice == "local" {
                    "right-content"
                } else {
                    "left-content"
                }
            );
            let restored = ws.read("right.md").unwrap();
            assert_eq!(restored.entry.file_id, right.file_id);
            assert_eq!(restored.content, "right-content");
            if choice == "copy" {
                assert_eq!(ws.read(&copy).unwrap().content, "right-content");
            }
            ws.sync_capture(&binding.id).unwrap();
            assert_eq!(
                ws.pending_count().unwrap(),
                if choice == "remote" { 0 } else { 1 }
            );
        }
    }
}
