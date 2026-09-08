//! Durable queue state. Network code never invents a remote base from a local revision.
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{fs, path::PathBuf};
use uuid::Uuid;

#[derive(Clone, Serialize, Deserialize)]
pub struct Binding {
    pub id: String,
    pub endpoint: String,
    pub remote_vault: String,
    pub account: String,
    pub cursor: i64,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Job {
    pub binding: String,
    pub operation_id: String,
    pub file_id: String,
    pub path: String,
    pub hash: String,
    pub size: i64,
    pub operation: String,
    pub state: String,
    pub base_revision: Option<i64>,
    pub upload_id: Option<String>,
}

impl Workspace {
    pub fn sync_paused(&self, binding: &str) -> Result<bool> {
        self.check_binding(binding)?;
        Ok(self
            .db
            .query_row(
                "SELECT paused FROM sync_preferences WHERE binding=?1",
                [binding],
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(false))
    }
    pub fn sync_pause(&mut self, binding: &str, paused: bool) -> Result<()> {
        self.check_binding(binding)?;
        self.db.execute("INSERT INTO sync_preferences VALUES (?1,?2) ON CONFLICT(binding) DO UPDATE SET paused=excluded.paused",params![binding,paused])?;
        Ok(())
    }
    pub fn sync_binding(&self) -> Result<Option<Binding>> {
        Ok(self.db.query_row("SELECT id,endpoint,remote_vault,account,cursor FROM sync_bindings WHERE state='active'", [], |r| {
            Ok(Binding { id:r.get(0)?, endpoint:r.get(1)?, remote_vault:r.get(2)?, account:r.get(3)?, cursor:r.get(4)? })
        }).optional()?)
    }
    pub(crate) fn check_binding(&self, binding: &str) -> Result<()> {
        if self.sync_binding()?.is_none_or(|b| b.id != binding) {
            return Err(HostError::new("SYNC_BINDING_CHANGED"));
        }
        Ok(())
    }
    /// Caller verifies an empty remote and obtains a reconciliation confirmation first.
    pub fn sync_bind_empty(
        &mut self,
        endpoint: &str,
        remote_vault: &str,
        account: &str,
    ) -> Result<Binding> {
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        let had_binding: bool =
            self.db
                .query_row("SELECT EXISTS(SELECT 1 FROM sync_bindings)", [], |r| {
                    r.get(0)
                })?;
        let entries = self.scan()?;
        let id = Uuid::new_v4().to_string();
        // Rebinding explicitly starts from the current snapshot, never an old account's queue.
        if had_binding {
            self.db.execute(
                "UPDATE outbox SET state='archived' WHERE state IN ('pending','queued')",
                [],
            )?;
        }
        for entry in entries.into_iter().filter(|e| !e.is_folder && !e.deleted) {
            let queued: bool = self.db.query_row(
                "SELECT EXISTS(SELECT 1 FROM outbox WHERE file_id=?1 AND state='pending')",
                [&entry.file_id],
                |r| r.get(0),
            )?;
            if !queued {
                let content = fs::read(self.resolve(&entry.path)?)?;
                self.write(&entry.path, &entry.hash, &content, "local")?;
            }
        }
        self.db.execute(
            "INSERT INTO sync_bindings VALUES (?1,?2,?3,?4,'active',0)",
            params![id, endpoint, remote_vault, account],
        )?;
        self.sync_capture(&id)?;
        self.sync_binding()?
            .ok_or_else(|| HostError::new("DATABASE_ERROR"))
    }
    pub fn sync_unbind(&mut self, binding: &str) -> Result<()> {
        self.check_binding(binding)?;
        let tx = self.db.transaction()?;
        tx.execute(
            "UPDATE sync_bindings SET state='archived' WHERE id=?1",
            [binding],
        )?;
        tx.execute(
            "UPDATE outbox SET state='archived' WHERE state IN ('pending','queued')",
            [],
        )?;
        tx.commit()?;
        Ok(())
    }
    pub fn sync_spool(&self, digest: &str) -> Result<PathBuf> {
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|v| v.is_ascii_hexdigit() && !v.is_ascii_uppercase())
        {
            return Err(HostError::new("SYNC_HASH_INVALID"));
        }
        let root = self.root.join(".ainote/sync-spool");
        if root.exists() {
            let meta = fs::symlink_metadata(&root)?;
            if !meta.is_dir() || meta.file_type().is_symlink() {
                return Err(HostError::new("UNSAFE_PATH"));
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes() & 0x400 != 0 {
                    return Err(HostError::new("UNSAFE_PATH"));
                }
            }
        }
        fs::create_dir_all(&root)?;
        Ok(root.join(digest))
    }
    pub fn sync_capture(&mut self, binding: &str) -> Result<()> {
        self.check_binding(binding)?;
        loop {
            let pending = self.db.query_row("SELECT operation_id,file_id,path,hash,operation,content FROM outbox WHERE state='pending' ORDER BY rowid LIMIT 1", [], |r| {
                Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?,r.get::<_,String>(4)?,r.get::<_,Vec<u8>>(5)?))
            }).optional()?;
            let Some((operation_id, file_id, path, digest, operation, content)) = pending else {
                break;
            };
            if !crate::sync_discovery::allowed(&path) {
                self.db.execute(
                    "UPDATE outbox SET state='excluded' WHERE operation_id=?1",
                    [&operation_id],
                )?;
                continue;
            }
            let size = if operation == "put" {
                if self.payload_ref(&operation_id)?.is_none() {
                    self.store_payload(&operation_id, &content)?;
                }
                let (stored, size) = self
                    .payload_ref(&operation_id)?
                    .ok_or_else(|| HostError::new("SYNC_SPOOL_CORRUPT"))?;
                if stored != digest {
                    return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
                }
                crate::payloads::verify(&self.sync_spool(&digest)?, &digest, size as u64)?;
                size
            } else {
                0
            };
            let tx = self.db.transaction()?;
            tx.execute("INSERT OR IGNORE INTO sync_jobs VALUES (?1,?2,?3,?4,?5,?6,?7,'pending',NULL,NULL,NULL,NULL)",
                params![binding,operation_id,file_id,path,digest,size,operation])?;
            tx.execute(
                "UPDATE outbox SET state='queued',content=X'' WHERE operation_id=?1",
                [&operation_id],
            )?;
            tx.commit()?;
        }
        Ok(())
    }
    pub fn sync_next(&self, binding: &str) -> Result<Option<Job>> {
        self.check_binding(binding)?;
        let job=self.db.query_row("SELECT binding,operation_id,file_id,path,hash,size,operation,state,base_revision,upload_id FROM sync_jobs WHERE binding=?1 AND state NOT IN ('acked','archived','conflict') AND NOT EXISTS (SELECT 1 FROM sync_conflicts c WHERE c.binding=sync_jobs.binding AND c.file_id=sync_jobs.file_id AND c.state='open') ORDER BY rowid LIMIT 1", [binding], |r| {
            Ok(Job { binding:r.get(0)?,operation_id:r.get(1)?,file_id:r.get(2)?,path:r.get(3)?,hash:r.get(4)?,size:r.get(5)?,operation:r.get(6)?,state:r.get(7)?,base_revision:r.get(8)?,upload_id:r.get(9)? })
        }).optional()?;
        if job
            .as_ref()
            .is_some_and(|job| !crate::sync_discovery::allowed(&job.path))
        {
            return Err(HostError::new("SYNC_CLASS_UNSUPPORTED"));
        }
        Ok(job)
    }
    fn check_job(&self, job: &Job) -> Result<()> {
        self.check_binding(&job.binding)?;
        let state: String = self.db.query_row(
            "SELECT state FROM sync_jobs WHERE binding=?1 AND operation_id=?2",
            params![job.binding, job.operation_id],
            |r| r.get(0),
        )?;
        if matches!(state.as_str(), "archived" | "conflict") {
            return Err(HostError::new("SYNC_OPERATION_SUPERSEDED"));
        }
        Ok(())
    }
    pub fn sync_upload(&self, job: &Job, upload: Option<&str>) -> Result<()> {
        self.check_job(job)?;
        self.db.execute("UPDATE sync_jobs SET state='uploading',upload_id=?3 WHERE binding=?1 AND operation_id=?2 AND base_revision IS NULL", params![job.binding,job.operation_id,upload])?;
        Ok(())
    }
    pub fn sync_commit_payload(&self, job: &Job) -> Result<serde_json::Value> {
        self.check_job(job)?;
        // The base is frozen exactly once. A response loss reuses the byte-equivalent payload.
        self.db.execute("UPDATE sync_jobs SET state='committing',base_revision=COALESCE((SELECT revision FROM sync_heads WHERE binding=?1 AND file_id=?3),0) WHERE binding=?1 AND operation_id=?2 AND base_revision IS NULL",
            params![job.binding,job.operation_id,job.file_id])?;
        let base: i64 = self.db.query_row(
            "SELECT base_revision FROM sync_jobs WHERE binding=?1 AND operation_id=?2",
            params![job.binding, job.operation_id],
            |r| r.get(0),
        )?;
        Ok(
            serde_json::json!({"operation_id":job.operation_id,"file_id":job.file_id,"base_revision":base,"path":job.path,
            "operation":job.operation,"content_hash":if job.operation=="put" {Some(&job.hash)} else {None},"size":job.size}),
        )
    }
    pub fn sync_ack(&mut self, job: &Job, revision: &serde_json::Value) -> Result<()> {
        self.check_binding(&job.binding)?;
        let payload = self.sync_commit_payload(job)?;
        for field in [
            "operation_id",
            "file_id",
            "base_revision",
            "path",
            "operation",
            "size",
        ] {
            if revision[field] != payload[field] {
                return Err(HostError::new("SYNC_RESPONSE_INVALID"));
            }
        }
        if revision["hash"] != payload["content_hash"]
            || revision["vault_id"]
                != self
                    .sync_binding()?
                    .ok_or_else(|| HostError::new("SYNC_BINDING_CHANGED"))?
                    .remote_vault
        {
            return Err(HostError::new("SYNC_RESPONSE_INVALID"));
        }
        let sequence = revision["sequence"]
            .as_i64()
            .filter(|v| *v > payload["base_revision"].as_i64().unwrap_or(0))
            .ok_or_else(|| HostError::new("SYNC_RESPONSE_INVALID"))?;
        let tx = self.db.transaction()?;
        tx.execute("INSERT INTO sync_heads VALUES (?1,?2,?3,?4,?5) ON CONFLICT(binding,file_id) DO UPDATE SET revision=excluded.revision,path=excluded.path,hash=excluded.hash WHERE sync_heads.revision<excluded.revision", params![job.binding,job.file_id,sequence,job.path,job.hash])?;
        tx.execute("UPDATE sync_jobs SET state='acked',remote_revision=?3,error=NULL WHERE binding=?1 AND operation_id=?2", params![job.binding,job.operation_id,sequence])?;
        tx.execute(
            "UPDATE outbox SET state='acked' WHERE operation_id=?1",
            [&job.operation_id],
        )?;
        tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::hash;
    #[test]
    fn queue_uses_remote_bases_and_keeps_retry_payload_across_restart() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let mut digest = String::new();
        for index in 0..20 {
            digest = ws
                .write("a.md", &digest, index.to_string().as_bytes(), "local")
                .unwrap()
                .hash;
        }
        let binding = ws
            .sync_bind_empty("https://sync.example", "fixture-vault", "fixture-user")
            .unwrap();
        for sequence in 1..=20 {
            let job = ws.sync_next(&binding.id).unwrap().unwrap();
            let payload = ws.sync_commit_payload(&job).unwrap();
            assert_eq!(payload["base_revision"], sequence - 1);
            drop(ws);
            ws = Workspace::open(root.path()).unwrap();
            assert_eq!(ws.sync_commit_payload(&job).unwrap(), payload);
            let response = serde_json::json!({"operation_id":job.operation_id,"file_id":job.file_id,"base_revision":sequence-1,
                "path":"a.md","operation":"put","hash":hash((sequence-1).to_string().as_bytes()),"size":(sequence-1).to_string().len(),
                "sequence":sequence,"vault_id":"fixture-vault"});
            ws.sync_ack(&job, &response).unwrap();
        }
        assert!(ws.sync_next(&binding.id).unwrap().is_none());
        assert_eq!(ws.read("a.md").unwrap().content, "19");
    }
    #[test]
    fn unbind_archives_old_work_and_stale_completion_is_rejected() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("a.md", "", b"safe", "local").unwrap();
        let first = ws
            .sync_bind_empty("https://one.example", "remote-one", "account-one")
            .unwrap();
        let old = ws.sync_next(&first.id).unwrap().unwrap();
        ws.sync_unbind(&first.id).unwrap();
        let second = ws
            .sync_bind_empty("https://two.example", "remote-two", "account-two")
            .unwrap();
        let new = ws.sync_next(&second.id).unwrap().unwrap();
        assert_ne!(old.operation_id, new.operation_id);
        assert_eq!(
            ws.sync_commit_payload(&old).unwrap_err().code,
            "SYNC_BINDING_CHANGED"
        );
        assert_eq!(ws.read("a.md").unwrap().content, "safe");
    }
}
