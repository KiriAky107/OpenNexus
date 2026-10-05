//! A single durable, Host-owned cleanup obligation across runs and vaults.
//! Opening it never takes ownership of or deletes an AppContainer or its files.
use crate::workspace::{HostError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

const MAX_RECORD: usize = 128 * 1024;
pub(crate) const MAX_GRANTS: usize = 512;
pub use crate::experiment_cleanup_objects::{ObjectKind, ObjectReceipt};
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CleanupObjects {
    pub profile: Option<ObjectReceipt>,
    pub profile_sid: Option<Vec<u8>>,
    pub source: Option<ObjectReceipt>,
    pub runtime: Option<ObjectReceipt>,
    pub source_grants: Option<Vec<ObjectReceipt>>,
    pub runtime_grants: Option<Vec<ObjectReceipt>>,
}
#[derive(Clone, Copy)]
pub(crate) enum GrantKind {
    Source,
    Runtime,
}
fn derived_sid(name: &str) -> Result<Vec<u8>> {
    use windows_sys::Win32::Security::{
        FreeSid, GetLengthSid, IsValidSid, Isolation::DeriveAppContainerSidFromAppContainerName,
    };
    let name: Vec<_> = name.encode_utf16().chain(Some(0)).collect();
    let mut sid = std::ptr::null_mut();
    let status = unsafe { DeriveAppContainerSidFromAppContainerName(name.as_ptr(), &mut sid) };
    let result = if status < 0 || sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
        Err(failed())
    } else {
        let length = unsafe { GetLengthSid(sid) } as usize;
        if !(8..=68).contains(&length) {
            Err(failed())
        } else {
            Ok(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), length) }.to_vec())
        }
    };
    if !sid.is_null() {
        unsafe {
            FreeSid(sid);
        }
    }
    result
}
impl CleanupObjects {
    fn validate(&self, record: &CleanupStatus) -> Result<()> {
        let roots = [&self.profile, &self.source, &self.runtime];
        for receipt in roots.iter().filter_map(|value| value.as_ref()) {
            receipt.validate()?;
            if receipt.kind != ObjectKind::Directory {
                return Err(failed());
            }
        }
        if record.phase != CleanupPhase::Created {
            if roots.iter().any(|value| value.is_some())
                || self.profile_sid.is_some()
                || self.source_grants.is_some()
                || self.runtime_grants.is_some()
            {
                return Err(failed());
            }
            return Ok(());
        }
        if self.profile.is_none()
            || self.profile_sid.as_ref() != Some(&derived_sid(&record.profile_name)?)
            || self.source.is_some() != record.source_root.is_some()
            || self.runtime.is_some() != record.runtime_root.is_some()
        {
            return Err(failed());
        }
        for (index, left) in roots.iter().filter_map(|value| value.as_ref()).enumerate() {
            for right in roots
                .iter()
                .filter_map(|value| value.as_ref())
                .skip(index + 1)
            {
                if left.path.starts_with(&right.path) || right.path.starts_with(&left.path) {
                    return Err(failed());
                }
            }
        }
        for (root, receipts) in [
            (&self.source, &self.source_grants),
            (&self.runtime, &self.runtime_grants),
        ] {
            if let Some(receipts) = receipts {
                let root = root.as_ref().ok_or_else(failed)?;
                if receipts.is_empty() || receipts.len() > MAX_GRANTS || !receipts.contains(root) {
                    return Err(failed());
                }
                let mut identities = std::collections::BTreeSet::new();
                let mut paths = std::collections::BTreeSet::new();
                for receipt in receipts {
                    receipt.validate()?;
                    if !receipt.path.starts_with(&root.path)
                        || !identities.insert((receipt.volume, receipt.file_id))
                        || !paths.insert(&receipt.path)
                    {
                        return Err(failed());
                    }
                }
            }
        }
        Ok(())
    }
}
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
    /// None marks an older path-only record; it cannot authorize recovery.
    #[serde(default)]
    pub objects: Option<CleanupObjects>,
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
            if let Some(objects) = &record.objects {
                objects.validate(&record)?;
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
        if !(0..=2).contains(&version) {
            return Err(failed());
        }
        // EXTRA also syncs rollback-journal directory removal in DELETE mode.
        db.execute_batch("PRAGMA journal_mode=DELETE; PRAGMA synchronous=EXTRA; CREATE TABLE IF NOT EXISTS cleanup (id INTEGER PRIMARY KEY CHECK(id=1),token TEXT NOT NULL,record TEXT NOT NULL); PRAGMA user_version=2;").map_err(|_| failed())?;
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
            objects: Some(CleanupObjects::default()),
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
        if let Some(objects) = &record.objects {
            objects.validate(&record)?;
        }
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
    pub(crate) fn created(&self, root: &Path, sid: &[u8]) -> Result<()> {
        let (receipt, _held) = ObjectReceipt::directory(root)?;
        self.update(|r| {
            if r.phase != CleanupPhase::Creating || !root.is_absolute() {
                return Err(failed());
            }
            r.phase = CleanupPhase::Created;
            r.profile_root = Some(root.into());
            let objects = r.objects.as_mut().ok_or_else(failed)?;
            objects.profile = Some(receipt);
            objects.profile_sid = Some(sid.into());
            Ok(())
        })
    }
    pub(crate) fn source_created(&self, root: &Path) -> Result<()> {
        let (receipt, _held) = ObjectReceipt::directory(root)?;
        self.update(|r| {
            if r.phase != CleanupPhase::Created || r.source_root.is_some() || !root.is_absolute() {
                return Err(failed());
            }
            r.source_root = Some(root.into());
            r.objects.as_mut().ok_or_else(failed)?.source = Some(receipt);
            Ok(())
        })
    }
    pub(crate) fn runtime_bound(&self, root: &Path) -> Result<()> {
        let (receipt, _held) = ObjectReceipt::directory(root)?;
        self.update(|r| {
            if r.phase != CleanupPhase::Created || r.runtime_root.is_some() || !root.is_absolute() {
                return Err(failed());
            }
            r.runtime_root = Some(root.into());
            r.objects.as_mut().ok_or_else(failed)?.runtime = Some(receipt);
            Ok(())
        })
    }
    /// Complete target intent is committed before the first borrowed ACL is changed.
    pub(crate) fn grants_bound(&self, kind: GrantKind, receipts: Vec<ObjectReceipt>) -> Result<()> {
        self.update(|record| {
            if record.phase != CleanupPhase::Created {
                return Err(failed());
            }
            let objects = record.objects.as_mut().ok_or_else(failed)?;
            let targets = match kind {
                GrantKind::Source => &mut objects.source_grants,
                GrantKind::Runtime => &mut objects.runtime_grants,
            };
            if targets.is_some() {
                return Err(failed());
            }
            *targets = Some(receipts);
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
            || record.objects.as_ref().is_none_or(|objects| {
                objects.source_grants.is_none() || objects.runtime_grants.is_none()
            })
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
        for name in ["profile", "sources", "runtime"] {
            std::fs::create_dir(root.path().join(name)).unwrap();
        }
        attempt
            .created(
                &root.path().join("profile"),
                &derived_sid(attempt.profile_name()).unwrap(),
            )
            .unwrap();
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
        db.execute_batch("DELETE FROM cleanup;PRAGMA user_version=3;")
            .unwrap();
        drop(db);
        assert!(Journal::open(root.path()).is_err());
    }
    #[test]
    fn object_receipts_survive_restart_and_reject_foreign_or_repeated_grant_intents() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let (vault, operation) = ids();
        let attempt = journal.reserve(&vault, &operation).unwrap();
        for name in ["profile", "sources", "runtime", "foreign"] {
            std::fs::create_dir(root.path().join(name)).unwrap();
        }
        attempt.before_create().unwrap();
        let sid = derived_sid(attempt.profile_name()).unwrap();
        assert!(attempt
            .created(&root.path().join("profile"), b"forged sid")
            .is_err());
        assert_eq!(
            journal.status().unwrap().unwrap().phase,
            CleanupPhase::Creating
        );
        attempt.created(&root.path().join("profile"), &sid).unwrap();
        attempt
            .source_created(&root.path().join("sources"))
            .unwrap();
        attempt.runtime_bound(&root.path().join("runtime")).unwrap();
        let source = journal
            .status()
            .unwrap()
            .unwrap()
            .objects
            .unwrap()
            .source
            .unwrap();
        let foreign = ObjectReceipt::directory(&root.path().join("foreign"))
            .unwrap()
            .0;
        assert!(attempt
            .grants_bound(GrantKind::Source, vec![source.clone(), foreign])
            .is_err());
        assert!(journal
            .status()
            .unwrap()
            .unwrap()
            .objects
            .unwrap()
            .source_grants
            .is_none());
        attempt
            .grants_bound(GrantKind::Source, vec![source.clone()])
            .unwrap();
        assert!(attempt
            .grants_bound(GrantKind::Source, vec![source.clone()])
            .is_err());
        drop(attempt);
        drop(journal);
        let restarted = Journal::open(root.path()).unwrap();
        let record = restarted.status().unwrap().unwrap();
        assert_eq!(record.objects.unwrap().source_grants.unwrap(), vec![source]);
        assert_eq!(record.phase, CleanupPhase::Created);
        assert!(restarted.reserve(&vault, &operation).is_err());
    }
    #[test]
    fn legacy_path_only_records_remain_durable_and_are_not_backfilled_with_current_objects() {
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let (vault, operation) = ids();
        let attempt = journal.reserve(&vault, &operation).unwrap();
        attempt.before_create().unwrap();
        let mut record = serde_json::to_value(journal.status().unwrap().unwrap()).unwrap();
        record.as_object_mut().unwrap().remove("objects");
        journal
            .db
            .lock()
            .unwrap()
            .execute(
                "UPDATE cleanup SET record=?1",
                [serde_json::to_string(&record).unwrap()],
            )
            .unwrap();
        journal
            .db
            .lock()
            .unwrap()
            .execute_batch("PRAGMA user_version=1")
            .unwrap();
        drop(attempt);
        drop(journal);
        let restarted = Journal::open(root.path()).unwrap();
        let record = restarted.status().unwrap().unwrap();
        assert!(record.objects.is_none());
        assert_eq!(record.phase, CleanupPhase::Creating);
        assert!(restarted.reserve(&vault, &operation).is_err());
    }
}
