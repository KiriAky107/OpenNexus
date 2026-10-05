//! A single durable, Host-owned cleanup obligation across runs and vaults.
//! Opening it never takes ownership of or deletes an AppContainer or its files.
use crate::workspace::{HostError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

const MAX_RECORD: usize = 16 * 1024;
fn failed() -> HostError {
    HostError::new("EXPERIMENT_CLEANUP_JOURNAL_FAILED")
}
fn required() -> HostError {
    HostError::new("EXPERIMENT_CLEANUP_REQUIRED")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CleanupPhase {
    Reserved,
    Creating,
    Created,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CleanupStatus {
    pub vault_id: String,
    pub operation_id: String,
    pub profile_name: String,
    pub phase: CleanupPhase,
    pub profile_root: Option<PathBuf>,
    pub source_root: Option<PathBuf>,
    pub runtime_root: Option<PathBuf>,
    pub error: Option<String>,
}
pub(crate) struct Journal {
    db: Mutex<Connection>,
    gate: crate::experiment_cleanup_lease::Gate,
}
pub(crate) struct Attempt {
    journal: Arc<Journal>,
    token: String,
    name: String,
    lease: Mutex<Option<crate::experiment_cleanup_lease::Lease>>,
}
fn read(db: &Connection) -> Result<Option<(String, CleanupStatus)>> {
    let value: Option<(Option<String>, Option<String>)> = db.query_row(
        "SELECT CASE WHEN length(CAST(token AS BLOB))=36 THEN token END,CASE WHEN length(CAST(record AS BLOB))<=?1 THEN record END FROM cleanup WHERE id=1",
        [MAX_RECORD], |r| Ok((r.get(0)?,r.get(1)?))).optional().map_err(|_| failed())?;
    value
        .map(|(token, encoded)| {
            let token = token.ok_or_else(failed)?;
            let id = uuid::Uuid::parse_str(&token).map_err(|_| failed())?;
            let record: CleanupStatus =
                serde_json::from_str(&encoded.ok_or_else(failed)?).map_err(|_| failed())?;
            if record.profile_name != format!("OpenNexus.sandbox.{}", id.simple())
                || uuid::Uuid::parse_str(&record.vault_id).is_err()
                || uuid::Uuid::parse_str(&record.operation_id).is_err()
                || (record.phase == CleanupPhase::Reserved
                    && (record.profile_root.is_some()
                        || record.source_root.is_some()
                        || record.runtime_root.is_some()))
                || (record.phase == CleanupPhase::Creating
                    && (record.profile_root.is_some()
                        || record.source_root.is_some()
                        || record.runtime_root.is_some()))
                || (record.phase == CleanupPhase::Created && record.profile_root.is_none())
                || [
                    &record.profile_root,
                    &record.source_root,
                    &record.runtime_root,
                ]
                .iter()
                .filter_map(|p| p.as_ref())
                .any(|p| !p.is_absolute())
                || record.error.as_ref().is_some_and(|s| {
                    s.is_empty()
                        || s.len() > 96
                        || !s
                            .bytes()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
                })
            {
                return Err(failed());
            }
            Ok((token, record))
        })
        .transpose()
}
impl Journal {
    pub(crate) fn open(host_root: &Path) -> Result<Arc<Self>> {
        // This root comes once from Host setup, never from a run or vault path.
        std::fs::create_dir_all(host_root).map_err(|_| failed())?;
        let gate = crate::experiment_cleanup_lease::Gate::open(host_root)?;
        let db =
            Connection::open(host_root.join("experiment-cleanup.sqlite3")).map_err(|_| failed())?;
        db.busy_timeout(std::time::Duration::from_secs(2))
            .map_err(|_| failed())?;
        let version: i64 = db
            .query_row("PRAGMA user_version", [], |r| r.get(0))
            .map_err(|_| failed())?;
        if !(0..=1).contains(&version) {
            return Err(failed());
        }
        // EXTRA also syncs rollback-journal directory removal in DELETE mode.
        db.execute_batch("PRAGMA journal_mode=DELETE; PRAGMA synchronous=EXTRA; CREATE TABLE IF NOT EXISTS cleanup (id INTEGER PRIMARY KEY CHECK(id=1),token TEXT NOT NULL,record TEXT NOT NULL); PRAGMA user_version=1;").map_err(|_| failed())?;
        read(&db)?;
        Ok(Arc::new(Self {
            db: Mutex::new(db),
            gate,
        }))
    }
    pub(crate) fn status(&self) -> Result<Option<CleanupStatus>> {
        let db = self.db.lock().map_err(|_| failed())?;
        Ok(read(&db)?.map(|(_, record)| record))
    }
    pub(crate) fn reserve(self: &Arc<Self>, vault: &str, operation: &str) -> Result<Arc<Attempt>> {
        if uuid::Uuid::parse_str(vault).is_err() || uuid::Uuid::parse_str(operation).is_err() {
            return Err(failed());
        }
        let lease = self.gate.acquire().map_err(|error| {
            if error.code == "EXPERIMENT_CLEANUP_OWNER_ACTIVE" {
                required()
            } else {
                error
            }
        })?;
        let token = uuid::Uuid::new_v4();
        let name = format!("OpenNexus.sandbox.{}", token.simple());
        let record = CleanupStatus {
            vault_id: vault.into(),
            operation_id: operation.into(),
            profile_name: name.clone(),
            phase: CleanupPhase::Reserved,
            profile_root: None,
            source_root: None,
            runtime_root: None,
            error: None,
        };
        let encoded = serde_json::to_string(&record).map_err(|_| failed())?;
        let db = self.db.lock().map_err(|_| failed())?;
        // One atomic statement also arbitrates separate Host connections. An
        // existing or unknown obligation cannot be replaced by a new request.
        if db
            .execute(
                "INSERT OR IGNORE INTO cleanup VALUES (1,?1,?2)",
                params![token.to_string(), encoded],
            )
            .map_err(|_| failed())?
            != 1
        {
            return Err(required());
        }
        Ok(Arc::new(Attempt {
            journal: Arc::clone(self),
            token: token.to_string(),
            name,
            lease: Mutex::new(Some(lease)),
        }))
    }
}
impl Attempt {
    pub(crate) fn profile_name(&self) -> &str {
        &self.name
    }
    fn update(&self, change: impl FnOnce(&mut CleanupStatus) -> Result<()>) -> Result<()> {
        let lease = self.lease.lock().map_err(|_| failed())?;
        if lease.is_none() {
            return Err(failed());
        }
        let mut db = self.journal.db.lock().map_err(|_| failed())?;
        let tx = db.transaction().map_err(|_| failed())?;
        let (token, mut record) = read(&tx)?.ok_or_else(failed)?;
        if token != self.token {
            return Err(failed());
        }
        change(&mut record)?;
        let encoded = serde_json::to_string(&record).map_err(|_| failed())?;
        if encoded.len() > MAX_RECORD {
            return Err(failed());
        }
        if tx
            .execute(
                "UPDATE cleanup SET record=?2 WHERE id=1 AND token=?1",
                params![self.token, encoded],
            )
            .map_err(|_| failed())?
            != 1
        {
            return Err(failed());
        }
        tx.commit().map_err(|_| failed())
    }
    pub(crate) fn before_create(&self) -> Result<()> {
        self.update(|r| {
            if r.phase != CleanupPhase::Reserved {
                return Err(failed());
            }
            r.phase = CleanupPhase::Creating;
            Ok(())
        })
    }
    pub(crate) fn created(&self, root: &Path) -> Result<()> {
        self.update(|r| {
            if r.phase != CleanupPhase::Creating || !root.is_absolute() {
                return Err(failed());
            }
            r.phase = CleanupPhase::Created;
            r.profile_root = Some(root.into());
            Ok(())
        })
    }
    pub(crate) fn source_created(&self, root: &Path) -> Result<()> {
        self.update(|r| {
            if r.phase != CleanupPhase::Created || r.source_root.is_some() || !root.is_absolute() {
                return Err(failed());
            }
            r.source_root = Some(root.into());
            Ok(())
        })
    }
    pub(crate) fn runtime_bound(&self, root: &Path) -> Result<()> {
        self.update(|r| {
            if r.phase != CleanupPhase::Created || r.runtime_root.is_some() || !root.is_absolute() {
                return Err(failed());
            }
            r.runtime_root = Some(root.into());
            Ok(())
        })
    }
    pub(crate) fn note_error(&self, code: &str) -> Result<()> {
        self.update(|r| {
            if code.is_empty()
                || code.len() > 96
                || !code
                    .bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
            {
                return Err(failed());
            }
            r.error = Some(code.into());
            Ok(())
        })
    }
    /// Only before the native factory was entered. Missing means an already
    /// completed obligation; a creating/created obligation stays durable.
    pub(crate) fn release_unstarted(&self) -> Result<bool> {
        let mut lease = self.lease.lock().map_err(|_| failed())?;
        if lease.is_none() {
            return Ok(false);
        }
        let mut db = self.journal.db.lock().map_err(|_| failed())?;
        let tx = db.transaction().map_err(|_| failed())?;
        let Some((token, record)) = read(&tx)? else {
            return Ok(false);
        };
        if token != self.token {
            return Err(failed());
        }
        if record.phase != CleanupPhase::Reserved {
            return Ok(false);
        }
        tx.execute("DELETE FROM cleanup WHERE id=1 AND token=?1", [&self.token])
            .map_err(|_| failed())?;
        tx.commit().map_err(|_| failed())?;
        drop(db);
        lease.take();
        Ok(true)
    }
    pub(crate) fn complete(
        &self,
        proof: crate::experiment_execution::CleanupProof<'_>,
    ) -> Result<()> {
        if !std::ptr::eq(self, proof.attempt()) {
            return Err(failed());
        }
        let mut lease = self.lease.lock().map_err(|_| failed())?;
        if lease.is_none() {
            return Err(failed());
        }
        let mut db = self.journal.db.lock().map_err(|_| failed())?;
        let tx = db.transaction().map_err(|_| failed())?;
        let (token, record) = read(&tx)?.ok_or_else(failed)?;
        if token != self.token
            || record.phase != CleanupPhase::Created
            || record.source_root.is_none()
            || record.runtime_root.is_none()
        {
            return Err(failed());
        }
        tx.execute("DELETE FROM cleanup WHERE id=1 AND token=?1", [&self.token])
            .map_err(|_| failed())?;
        tx.commit().map_err(|_| failed())?;
        drop(db);
        lease.take();
        Ok(())
    }
}
// No Drop deletion: thread unwind or Host death does not prove OS cleanup.

#[cfg(test)]
mod tests {
    use super::*;
    fn ids() -> (String, String) {
        (
            uuid::Uuid::new_v4().to_string(),
            uuid::Uuid::new_v4().to_string(),
        )
    }
    #[test]
    fn restart_preserves_uncertain_ownership_without_touching_objects() {
        let root = tempfile::tempdir().unwrap();
        let sentinel = root.path().join("unrelated");
        std::fs::write(&sentinel, b"preserve").unwrap();
        let (v, o) = ids();
        let journal = Journal::open(root.path()).unwrap();
        let attempt = journal.reserve(&v, &o).unwrap();
        attempt.before_create().unwrap();
        assert!(!attempt.release_unstarted().unwrap());
        drop(attempt);
        drop(journal);
        let restarted = Journal::open(root.path()).unwrap();
        assert_eq!(
            restarted.status().unwrap().unwrap().phase,
            CleanupPhase::Creating
        );
        let (v2, o2) = ids();
        assert_eq!(
            restarted.reserve(&v2, &o2).err().unwrap().code,
            "EXPERIMENT_CLEANUP_REQUIRED"
        );
        assert_eq!(std::fs::read(sentinel).unwrap(), b"preserve");
    }
    #[test]
    fn aborted_preflight_releases_only_unstarted_intent_and_delete_failure_stays_durable() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let (v, o) = ids();
        let attempt = journal.reserve(&v, &o).unwrap();
        assert!(attempt.release_unstarted().unwrap());
        assert!(journal.status().unwrap().is_none());
        let attempt = journal.reserve(&v, &o).unwrap();
        journal.db.lock().unwrap().execute_batch("CREATE TRIGGER fail_cleanup BEFORE DELETE ON cleanup BEGIN SELECT RAISE(ABORT,'injected failure');END;").unwrap();
        assert!(attempt.release_unstarted().is_err());
        drop(attempt);
        drop(journal);
        let journal = Journal::open(root.path()).unwrap();
        assert!(journal.status().unwrap().is_some());
        assert!(journal.reserve(&v, &o).is_err());
    }
    #[test]
    fn separate_connections_arbitrate_a_single_obligation() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let other = Journal::open(root.path()).unwrap();
        let (v, o) = ids();
        let attempt = journal.reserve(&v, &o).unwrap();
        assert!(other.reserve(&v, &o).is_err());
        attempt.before_create().unwrap();
        attempt.created(root.path()).unwrap();
        attempt
            .source_created(&root.path().join("sources"))
            .unwrap();
        attempt.runtime_bound(&root.path().join("runtime")).unwrap();
        attempt
            .note_error("EXPERIMENT_SOURCE_CLEANUP_FAILED")
            .unwrap();
        assert!(!attempt.release_unstarted().unwrap());
        let status = other.status().unwrap().unwrap();
        assert_eq!(status.phase, CleanupPhase::Created);
        assert_eq!(
            status.error.as_deref(),
            Some("EXPERIMENT_SOURCE_CLEANUP_FAILED")
        );
    }
    #[test]
    fn released_attempt_cannot_unlock_or_change_the_next_attempt() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let other = Journal::open(root.path()).unwrap();
        let (v, o) = ids();
        let first = journal.reserve(&v, &o).unwrap();
        assert!(first.release_unstarted().unwrap());
        let (v2, o2) = ids();
        let second = other.reserve(&v2, &o2).unwrap();
        assert!(first.before_create().is_err());
        assert!(!first.release_unstarted().unwrap());
        assert_eq!(journal.status().unwrap().unwrap().operation_id, o2);
        assert_eq!(
            journal.gate.acquire().err().unwrap().code,
            "EXPERIMENT_CLEANUP_OWNER_ACTIVE"
        );
        assert!(second.release_unstarted().unwrap());
        assert!(journal.status().unwrap().is_none());
        assert!(journal.gate.acquire().is_ok());
    }
    #[test]
    fn corrupt_and_future_journals_fail_closed() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let (v, o) = ids();
        let _attempt = journal.reserve(&v, &o).unwrap();
        journal
            .db
            .lock()
            .unwrap()
            .execute("UPDATE cleanup SET record=?1", ["x".repeat(MAX_RECORD + 1)])
            .unwrap();
        assert!(journal.status().is_err());
        drop(_attempt);
        drop(journal);
        assert!(Journal::open(root.path()).is_err());
        let db = Connection::open(root.path().join("experiment-cleanup.sqlite3")).unwrap();
        db.execute_batch("DELETE FROM cleanup;PRAGMA user_version=2;")
            .unwrap();
        drop(db);
        assert!(Journal::open(root.path()).is_err());
    }
}
