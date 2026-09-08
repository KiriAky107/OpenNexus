//! Persist received revisions before Workspace writes; cursor advancement follows application.
use crate::{
    sync_state::{Binding, Job},
    workspace::{hash, HostError, Result, Workspace},
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{fs, io::Write};
use uuid::Uuid;

#[derive(Clone, Serialize, Deserialize)]
pub struct RemoteRevision {
    pub vault_id: String,
    pub sequence: i64,
    pub file_id: String,
    pub base_revision: i64,
    pub path: String,
    pub operation: String,
    pub hash: Option<String>,
    pub size: i64,
    pub operation_id: String,
}

impl RemoteRevision {
    pub fn validate(&self, binding: &Binding) -> Result<()> {
        if self.vault_id != binding.remote_vault
            || self.sequence <= 0
            || self.base_revision < 0
            || self.base_revision >= self.sequence
            || !(0..=104857600).contains(&self.size)
            || self.file_id.len() < 16
            || self.file_id.len() > 80
            || !self
                .file_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
            || !matches!(self.operation.as_str(), "put" | "delete")
            || (self.operation == "delete" && (self.hash.is_some() || self.size != 0))
            || (self.operation == "put"
                && self.hash.as_ref().is_none_or(|h| {
                    h.len() != 64
                        || !h
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                }))
        {
            return Err(HostError::new("SYNC_RESPONSE_INVALID"));
        }
        if !crate::sync_discovery::allowed(&self.path) {
            return Err(HostError::new("SYNC_CLASS_UNSUPPORTED"));
        }
        Ok(())
    }
}

impl Workspace {
    pub fn sync_bind_download(
        &mut self,
        endpoint: &str,
        remote_vault: &str,
        account: &str,
    ) -> Result<Binding> {
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        if !self.sync_paths()?.is_empty() {
            return Err(HostError::new("SYNC_RECONCILIATION_REQUIRED"));
        }
        let id = Uuid::new_v4().to_string();
        let tx = self.db.transaction()?;
        tx.execute(
            "UPDATE outbox SET state='archived' WHERE state IN ('pending','queued')",
            [],
        )?;
        tx.execute(
            "INSERT INTO sync_bindings VALUES (?1,?2,?3,?4,'active',0)",
            params![id, endpoint, remote_vault, account],
        )?;
        tx.commit()?;
        self.sync_binding()?
            .ok_or_else(|| HostError::new("DATABASE_ERROR"))
    }
    pub fn sync_boundary(&self, binding: &str) -> Result<Option<i64>> {
        self.check_binding(binding)?;
        Ok(self
            .db
            .query_row(
                "SELECT boundary FROM sync_windows WHERE binding=?1",
                [binding],
                |r| r.get(0),
            )
            .optional()?)
    }
    pub fn sync_set_boundary(&self, binding: &str, boundary: i64) -> Result<()> {
        self.check_binding(binding)?;
        let cursor = self
            .sync_binding()?
            .ok_or_else(|| HostError::new("SYNC_BINDING_CHANGED"))?
            .cursor;
        if boundary < cursor
            || self
                .sync_boundary(binding)?
                .is_some_and(|old| old != boundary)
        {
            return Err(HostError::new("SYNC_RESPONSE_INVALID"));
        }
        self.db.execute(
            "INSERT OR IGNORE INTO sync_windows VALUES (?1,?2)",
            params![binding, boundary],
        )?;
        Ok(())
    }
    pub fn sync_store_bytes(&self, bytes: &[u8]) -> Result<String> {
        let digest = hash(bytes);
        let path = self.sync_spool(&digest)?;
        if path.exists() {
            if fs::symlink_metadata(&path)?.file_type().is_symlink()
                || hash(&fs::read(&path)?) != digest
            {
                return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
            }
        } else {
            let mut temp = tempfile::NamedTempFile::new_in(path.parent().unwrap())?;
            temp.write_all(bytes)?;
            temp.as_file().sync_all()?;
            temp.persist_noclobber(path)
                .map_err(|_| HostError::new("SYNC_SPOOL_FAILED"))?;
        }
        Ok(digest)
    }
    pub fn sync_stage(&self, binding: &str, revision: &RemoteRevision) -> Result<()> {
        self.check_binding(binding)?;
        let active = self
            .sync_binding()?
            .ok_or_else(|| HostError::new("SYNC_BINDING_CHANGED"))?;
        revision.validate(&active)?;
        self.resolve(&revision.path)?;
        let encoded =
            serde_json::to_string(revision).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
        if let Some(initial) = self.initial_revision(binding, revision.sequence)? {
            if initial != encoded {
                return Err(HostError::new("SYNC_REVISION_CHANGED"));
            }
        } else if revision.sequence != active.cursor + 1
            || self
                .sync_boundary(binding)?
                .is_none_or(|end| revision.sequence > end)
        {
            return Err(HostError::new("SYNC_CURSOR_INVALID"));
        }
        if let Some(digest) = &revision.hash {
            let bytes = fs::read(self.sync_spool(digest)?)?;
            if bytes.len() as i64 != revision.size || hash(&bytes) != *digest {
                return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
            }
        }
        let encoded =
            serde_json::to_string(revision).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
        let existing: Option<String> = self
            .db
            .query_row(
                "SELECT revision FROM sync_inbox WHERE binding=?1 AND sequence=?2",
                params![binding, revision.sequence],
                |r| r.get(0),
            )
            .optional()?;
        if existing.is_some_and(|value| value != encoded) {
            return Err(HostError::new("SYNC_REVISION_CHANGED"));
        }
        self.db.execute(
            "INSERT OR IGNORE INTO sync_inbox VALUES (?1,?2,?3,?4,?5,'pending')",
            params![
                binding,
                revision.sequence,
                encoded,
                Uuid::new_v4().to_string(),
                Uuid::new_v4().to_string()
            ],
        )?;
        Ok(())
    }
    fn sync_finish(&mut self, binding: &str, revision: &RemoteRevision, state: &str) -> Result<()> {
        self.check_binding(binding)?;
        let tx = self.db.transaction()?;
        let initial: Option<i64> = tx
            .query_row(
                "SELECT boundary FROM sync_initial WHERE binding=?1",
                [binding],
                |r| r.get(0),
            )
            .optional()?;
        if initial.is_none() {
            let changed = tx.execute(
                "UPDATE sync_bindings SET cursor=?2 WHERE id=?1 AND state='active' AND cursor=?3",
                params![binding, revision.sequence, revision.sequence - 1],
            )?;
            if changed != 1 {
                return Err(HostError::new("SYNC_CURSOR_INVALID"));
            }
        }
        tx.execute("INSERT INTO sync_heads VALUES (?1,?2,?3,?4,?5) ON CONFLICT(binding,file_id) DO UPDATE SET revision=excluded.revision,path=excluded.path,hash=excluded.hash WHERE sync_heads.revision<excluded.revision", params![binding,revision.file_id,revision.sequence,revision.path,revision.hash.as_deref().unwrap_or("")])?;
        tx.execute(
            "UPDATE sync_inbox SET state=?3 WHERE binding=?1 AND sequence=?2",
            params![binding, revision.sequence, state],
        )?;
        if let Some(boundary) = initial {
            let pending:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM sync_initial_items i WHERE i.binding=?1 AND NOT EXISTS(SELECT 1 FROM sync_inbox n WHERE n.binding=i.binding AND n.sequence=i.sequence AND n.state!='pending'))",[binding],|r|r.get(0))?;
            if !pending {
                tx.execute(
                    "UPDATE sync_bindings SET cursor=?2 WHERE id=?1",
                    params![binding, boundary],
                )?;
                tx.execute("DELETE FROM sync_initial WHERE binding=?1", [binding])?;
                tx.execute("DELETE FROM sync_initial_items WHERE binding=?1", [binding])?;
            }
        }
        tx.execute(
            "DELETE FROM sync_windows WHERE binding=?1 AND boundary=?2",
            params![binding, revision.sequence],
        )?;
        tx.commit()?;
        Ok(())
    }
    fn sync_preserve_conflict(
        &mut self,
        binding: &str,
        revision: &RemoteRevision,
        local_path: &str,
    ) -> Result<()> {
        let path = self.resolve(local_path)?;
        let digest = if path.is_file() {
            self.sync_store_bytes(&fs::read(path)?)?
        } else {
            String::new()
        };
        let remote =
            serde_json::to_string(revision).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
        let tx = self.db.transaction()?;
        tx.execute("UPDATE sync_conflicts SET state='superseded' WHERE binding=?1 AND file_id=?2 AND state='open' AND sequence<?3", params![binding,revision.file_id,revision.sequence])?;
        tx.execute(
            "INSERT OR IGNORE INTO sync_conflicts VALUES (?1,?2,?3,?4,?5,?6,'open')",
            params![
                binding,
                revision.sequence,
                revision.file_id,
                local_path,
                digest,
                remote
            ],
        )?;
        tx.execute("UPDATE sync_jobs SET state='conflict' WHERE binding=?1 AND (file_id=?2 OR path=?3) AND state NOT IN ('acked','archived')", params![binding,revision.file_id,local_path])?;
        tx.commit()?;
        self.sync_finish(binding, revision, "conflict")
    }
    pub fn sync_apply_pending(&mut self, binding: &str) -> Result<bool> {
        self.check_binding(binding)?;
        let pending: Option<(String,String,String)> = self.db.query_row("SELECT revision,operation_id,rename_id FROM sync_inbox WHERE binding=?1 AND state='pending' ORDER BY sequence LIMIT 1", [binding], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
        let Some((encoded, operation_id, rename_id)) = pending else {
            return Ok(false);
        };
        let revision: RemoteRevision =
            serde_json::from_str(&encoded).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
        let own: Option<Job> = self.db.query_row("SELECT binding,operation_id,file_id,path,hash,size,operation,state,base_revision,upload_id FROM sync_jobs WHERE binding=?1 AND operation_id=?2", params![binding,revision.operation_id], |r| {
            Ok(Job { binding:r.get(0)?,operation_id:r.get(1)?,file_id:r.get(2)?,path:r.get(3)?,hash:r.get(4)?,size:r.get(5)?,operation:r.get(6)?,state:r.get(7)?,base_revision:r.get(8)?,upload_id:r.get(9)? })
        }).optional()?;
        if let Some(job) = own.filter(|job| !matches!(job.state.as_str(), "archived" | "conflict"))
        {
            self.sync_ack(
                &job,
                &serde_json::to_value(&revision)
                    .map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?,
            )?;
            self.sync_finish(binding, &revision, "applied")?;
            return Ok(true);
        }
        if self
            .operation(&operation_id)?
            .is_some_and(|value| value["state"] == "committed")
        {
            self.sync_finish(binding, &revision, "applied")?;
            return Ok(true);
        }
        let known: Option<i64> = self
            .db
            .query_row(
                "SELECT revision FROM sync_heads WHERE binding=?1 AND file_id=?2",
                params![binding, revision.file_id],
                |r| r.get(0),
            )
            .optional()?;
        if known.is_some_and(|head| head >= revision.sequence) {
            self.sync_finish(binding, &revision, "applied")?;
            return Ok(true);
        }
        let local_path = self.path_for_id(&revision.file_id).ok();
        let path = local_path.as_deref().unwrap_or(&revision.path);
        let local = self.resolve(path)?;
        let current = if local.is_file() {
            hash(&fs::read(&local)?)
        } else {
            String::new()
        };
        let queued: bool = self.db.query_row("SELECT EXISTS(SELECT 1 FROM outbox WHERE file_id=?1 AND state IN ('pending','queued'))", [&revision.file_id], |r| r.get(0))?;
        let head: Option<String> = self
            .db
            .query_row(
                "SELECT hash FROM sync_heads WHERE binding=?1 AND file_id=?2",
                params![binding, revision.file_id],
                |r| r.get(0),
            )
            .optional()?;
        let conflict = queued
            || (local_path.is_none() && local.exists())
            || (local_path.is_some() && head.as_deref() != Some(current.as_str()))
            || (path != revision.path && self.resolve(&revision.path)?.exists());
        if conflict {
            self.sync_preserve_conflict(binding, &revision, path)?;
            return Ok(true);
        }
        if revision.operation == "delete" {
            if local_path.is_some() && local.exists() {
                self.mutate_with_origin("delete", path, "", &current, &operation_id, "remote")?;
            }
        } else {
            if let Some(previous) = local_path
                .as_deref()
                .filter(|previous| *previous != revision.path)
            {
                self.mutate_with_origin(
                    "rename",
                    previous,
                    &revision.path,
                    &current,
                    &rename_id,
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
                &operation_id,
                Some(&revision.file_id),
            )?;
        }
        self.sync_finish(binding, &revision, "applied")?;
        Ok(true)
    }
    pub fn sync_conflicts(&self, binding: &str) -> Result<Vec<Value>> {
        self.check_binding(binding)?;
        let mut statement = self.db.prepare("SELECT sequence,file_id,local_path,local_hash,remote FROM sync_conflicts WHERE binding=?1 AND state='open' ORDER BY sequence")?;
        let rows = statement
            .query_map([binding], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        rows.into_iter().map(|(sequence,file_id,local_path,local_hash,remote)| {
            let remote: Value = serde_json::from_str(&remote).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?;
            let current_path=self.path_for_id(&file_id).unwrap_or_else(|_|local_path.clone());
            let source=self.resolve(&current_path)?;
            let current_hash=if source.is_file() {hash(&fs::read(source)?)} else {String::new()};
            Ok(serde_json::json!({"sequence":sequence,"file_id":file_id,"local_path":local_path,"local_hash":local_hash,"current_path":current_path,"current_hash":current_hash,"remote":remote}))
        }).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn inbox_reopen_before_and_after_file_commit_never_advances_cursor_early() {
        for committed in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let binding = ws
                .sync_bind_download("https://sync.example", "remote-vault", "account")
                .unwrap();
            let file_id = Uuid::new_v4().to_string();
            let digest = ws.sync_store_bytes(b"remote-content").unwrap();
            let revision = RemoteRevision {
                vault_id: "remote-vault".into(),
                sequence: 1,
                file_id: file_id.clone(),
                base_revision: 0,
                path: "nested/a.md".into(),
                operation: "put".into(),
                hash: Some(digest),
                size: 14,
                operation_id: Uuid::new_v4().to_string(),
            };
            ws.sync_set_boundary(&binding.id, 1).unwrap();
            ws.sync_stage(&binding.id, &revision).unwrap();
            if committed {
                let operation: String = ws
                    .db
                    .query_row("SELECT operation_id FROM sync_inbox", [], |r| r.get(0))
                    .unwrap();
                ws.write_with_identity(
                    "nested/a.md",
                    "",
                    b"remote-content",
                    "remote",
                    &operation,
                    Some(&file_id),
                )
                .unwrap();
            }
            assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
            drop(ws);
            let mut ws = Workspace::open(root.path()).unwrap();
            assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
            assert!(ws.sync_apply_pending(&binding.id).unwrap());
            assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 1);
            let document = ws.read("nested/a.md").unwrap();
            assert_eq!(document.content, "remote-content");
            assert_eq!(document.entry.file_id, file_id);
            assert_eq!(document.entry.revision, 1);
            assert_eq!(ws.pending_count().unwrap(), 0);
            assert!(!ws.sync_apply_pending(&binding.id).unwrap());
        }
    }
}
