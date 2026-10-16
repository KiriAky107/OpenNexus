//! Native-only nonsecret authentication intents. A prepared operation authorizes
//! one first send; reopening or repeating its id never authorizes another POST.
//! Tokens/passwords are not accepted here and belong exclusively to the broker.
use crate::{
    sync_client::{canonical_endpoint, identifier},
    workspace::{HostError, Result},
};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::Serialize;
use std::{fs, path::Path};
use uuid::Uuid;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Scope {
    endpoint: String,
    account: String,
}
impl Scope {
    pub fn new(endpoint: &str, account: &str) -> Result<Self> {
        // Bound native metadata before URL parsing and again after IDNA
        // normalization. This also leaves room in the Keystore AAD scope.
        if endpoint.len() > 1024 {
            return Err(HostError::new("SYNC_ENDPOINT_INVALID"));
        }
        if account.is_empty()
            || account.chars().count() > 80
            || account.chars().any(char::is_control)
        {
            return Err(HostError::new("SYNC_LOGIN_INVALID"));
        }
        let endpoint = canonical_endpoint(endpoint, false)
            .map_err(|e| HostError::new(&e.code))?
            .to_string();
        if endpoint.len() > 1024 {
            return Err(HostError::new("SYNC_ENDPOINT_INVALID"));
        }
        Ok(Self {
            endpoint,
            account: account.into(),
        })
    }
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }
    pub fn account(&self) -> &str {
        &self.account
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    LoginPending,
    StorePending,
    Active,
    RefreshPending,
    LogoutPending,
    ErasePending,
    Disconnected,
    Unknown,
}
impl Phase {
    fn name(self) -> &'static str {
        match self {
            Self::LoginPending => "login_pending",
            Self::StorePending => "store_pending",
            Self::Active => "active",
            Self::RefreshPending => "refresh_pending",
            Self::LogoutPending => "logout_pending",
            Self::ErasePending => "erase_pending",
            Self::Disconnected => "disconnected",
            Self::Unknown => "unknown",
        }
    }
    fn parse(value: &str) -> Result<Self> {
        match value {
            "login_pending" => Ok(Self::LoginPending),
            "store_pending" => Ok(Self::StorePending),
            "active" => Ok(Self::Active),
            "refresh_pending" => Ok(Self::RefreshPending),
            "logout_pending" => Ok(Self::LogoutPending),
            "erase_pending" => Ok(Self::ErasePending),
            "disconnected" => Ok(Self::Disconnected),
            "unknown" => Ok(Self::Unknown),
            _ => Err(HostError::new("SYNC_AUTH_INDEX_INVALID")),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Intent {
    pub operation_id: String,
    pub endpoint: String,
    pub account: String,
    pub device_name: String,
    pub kind: String,
    pub phase: Phase,
    pub device_id: Option<String>,
    pub generation: i64,
    /// Only a received successful DELETE response sets this. Local erasure does not.
    pub server_logout_confirmed: bool,
    pub previous_operation: Option<String>,
}
pub struct Prepared {
    pub intent: Intent,
    pub first_send: bool,
}

/// Extracted from a successfully decrypted/read-back native broker payload, never IPC.
#[derive(Clone)]
pub struct StoredIdentity {
    pub endpoint: String,
    pub account: String,
    pub device_id: String,
    pub operation_id: String,
    pub generation: i64,
}
impl StoredIdentity {
    pub(crate) fn matches_intent(&self, intent: &Intent) -> bool {
        self.matches(intent)
    }
    fn matches(&self, intent: &Intent) -> bool {
        self.endpoint == intent.endpoint
            && self.account == intent.account
            && Some(self.device_id.as_str()) == intent.device_id.as_deref()
            && self.operation_id == intent.operation_id
            && self.generation == intent.generation
    }
}

pub struct Journal {
    db: Connection,
}
const COLUMNS:&str="operation_id,endpoint,account,device_name,kind,phase,device_id,generation,server_logout_confirmed,previous_operation";
fn operation_id(value: &str) -> Result<()> {
    if Uuid::parse_str(value).is_ok_and(|id| id.to_string() == value) {
        Ok(())
    } else {
        Err(HostError::new("SYNC_AUTH_INTENT_INVALID"))
    }
}
fn get(db: &Connection, id: &str) -> Result<Option<Intent>> {
    let row = db
        .query_row(
            &format!("SELECT {COLUMNS} FROM auth_intents WHERE operation_id=?1"),
            [id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, Option<String>>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, bool>(8)?,
                    row.get::<_, Option<String>>(9)?,
                ))
            },
        )
        .optional()?;
    row.map(
        |(
            operation_id,
            endpoint,
            account,
            device_name,
            kind,
            phase,
            device_id,
            generation,
            server_logout_confirmed,
            previous_operation,
        )| {
            Ok(Intent {
                operation_id,
                endpoint,
                account,
                device_name,
                kind,
                phase: Phase::parse(&phase)?,
                device_id,
                generation,
                server_logout_confirmed,
                previous_operation,
            })
        },
    )
    .transpose()
}
fn current(db: &Connection, scope: &Scope) -> Result<Option<Intent>> {
    let id: Option<String> = db
        .query_row(
            "SELECT current_operation FROM auth_scopes WHERE endpoint=?1 AND account=?2",
            params![scope.endpoint, scope.account],
            |row| row.get(0),
        )
        .optional()?;
    id.map(|id| get(db, &id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID")))
        .transpose()
}
fn require_current(db: &Connection, id: &str) -> Result<Intent> {
    operation_id(id)?;
    let intent = get(db, id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INTENT_NOT_FOUND"))?;
    let scope = Scope::new(&intent.endpoint, &intent.account)?;
    if current(db, &scope)?.is_none_or(|now| now.operation_id != id) {
        return Err(HostError::new("SYNC_AUTH_IDENTITY_CHANGED"));
    }
    Ok(intent)
}

impl Journal {
    /// The native host supplies its app-private parent, outside the content Vault.
    pub fn open(private_root: &Path) -> Result<Self> {
        fs::create_dir_all(private_root)?;
        let path = private_root.join("mobile-sync-auth.sqlite3");
        for suffix in ["", "-wal", "-shm", "-journal"] {
            let file = path.with_file_name(format!("mobile-sync-auth.sqlite3{suffix}"));
            match fs::symlink_metadata(file) {
                Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
                    return Err(HostError::new("UNSAFE_PATH"))
                }
                Ok(_) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        let db = Connection::open(path)?;
        let version: i64 = db.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version > 1 {
            return Err(HostError::new("SCHEMA_INCOMPATIBLE"));
        }
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS auth_scopes(endpoint TEXT NOT NULL,account TEXT NOT NULL,current_operation TEXT NOT NULL,PRIMARY KEY(endpoint,account));
            CREATE TABLE IF NOT EXISTS auth_intents(operation_id TEXT PRIMARY KEY,endpoint TEXT NOT NULL,account TEXT NOT NULL,
                device_name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('login','refresh','logout','local_disconnect')),
                phase TEXT NOT NULL CHECK(phase IN ('login_pending','store_pending','active','refresh_pending','logout_pending','erase_pending','disconnected','unknown')),
                device_id TEXT,generation INTEGER NOT NULL CHECK(generation>0),server_logout_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(server_logout_confirmed IN (0,1)),previous_operation TEXT);
            PRAGMA user_version=1; COMMIT;")?;
        Ok(Self { db })
    }
    pub fn find(&self, id: &str) -> Result<Option<Intent>> {
        operation_id(id)?;
        get(&self.db, id)
    }
    pub fn current(&self, scope: &Scope) -> Result<Option<Intent>> {
        current(&self.db, scope)
    }

    pub fn prepare_login(
        &mut self,
        id: &str,
        scope: &Scope,
        device_name: &str,
    ) -> Result<Prepared> {
        operation_id(id)?;
        if device_name.is_empty()
            || device_name.chars().count() > 120
            || device_name.chars().any(char::is_control)
        {
            return Err(HostError::new("SYNC_LOGIN_INVALID"));
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(intent) = get(&tx, id)? {
            if intent.kind != "login"
                || intent.endpoint != scope.endpoint
                || intent.account != scope.account
                || intent.device_name != device_name
            {
                return Err(HostError::new("SYNC_AUTH_INTENT_MISMATCH"));
            }
            return Ok(Prepared {
                intent,
                first_send: false,
            });
        }
        let old = current(&tx, scope)?;
        if old
            .as_ref()
            .is_some_and(|old| !matches!(old.phase, Phase::Unknown | Phase::Disconnected))
        {
            return Err(HostError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
        }
        let generation = old.map_or(Ok(1), |old| {
            old.generation
                .checked_add(1)
                .ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))
        })?;
        tx.execute(
            "INSERT INTO auth_intents VALUES (?1,?2,?3,?4,'login','login_pending',NULL,?5,0,NULL)",
            params![id, scope.endpoint, scope.account, device_name, generation],
        )?;
        tx.execute("INSERT INTO auth_scopes VALUES (?1,?2,?3) ON CONFLICT(endpoint,account) DO UPDATE SET current_operation=excluded.current_operation",params![scope.endpoint,scope.account,id])?;
        let intent = get(&tx, id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        tx.commit()?;
        Ok(Prepared {
            intent,
            first_send: true,
        })
    }

    fn prepare_existing(&mut self, id: &str, expected: &str, kind: &str) -> Result<Prepared> {
        operation_id(id)?;
        operation_id(expected)?;
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let previous =
            get(&tx, expected)?.ok_or_else(|| HostError::new("SYNC_AUTH_INTENT_NOT_FOUND"))?;
        if let Some(intent) = get(&tx, id)? {
            if intent.kind != kind
                || intent.endpoint != previous.endpoint
                || intent.account != previous.account
                || intent.previous_operation.as_deref() != Some(expected)
            {
                return Err(HostError::new("SYNC_AUTH_INTENT_MISMATCH"));
            }
            return Ok(Prepared {
                intent,
                first_send: false,
            });
        }
        let previous = require_current(&tx, expected)?;
        if kind != "local_disconnect" && previous.phase != Phase::Active {
            return Err(HostError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
        }
        if previous.device_id.is_none() {
            return Err(HostError::new("SYNC_LOGIN_REQUIRED"));
        }
        let phase = match kind {
            "refresh" => Phase::RefreshPending,
            "logout" => Phase::LogoutPending,
            "local_disconnect" => Phase::ErasePending,
            _ => return Err(HostError::new("SYNC_AUTH_INTENT_INVALID")),
        };
        let generation = previous
            .generation
            .checked_add(1)
            .ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        tx.execute(
            "INSERT INTO auth_intents VALUES (?1,?2,?3,?4,?5,?6,?7,?8,0,?9)",
            params![
                id,
                previous.endpoint,
                previous.account,
                previous.device_name,
                kind,
                phase.name(),
                previous.device_id,
                generation,
                expected
            ],
        )?;
        tx.execute("UPDATE auth_scopes SET current_operation=?1 WHERE endpoint=?2 AND account=?3 AND current_operation=?4",params![id,previous.endpoint,previous.account,expected])?;
        let intent = get(&tx, id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        tx.commit()?;
        Ok(Prepared {
            intent,
            first_send: kind != "local_disconnect",
        })
    }
    pub fn prepare_refresh(&mut self, id: &str, expected: &str) -> Result<Prepared> {
        self.prepare_existing(id, expected, "refresh")
    }
    pub fn prepare_logout(&mut self, id: &str, expected: &str) -> Result<Prepared> {
        self.prepare_existing(id, expected, "logout")
    }
    /// Local-only disconnection never authorizes a server request or claims revocation.
    pub fn prepare_local_disconnect(&mut self, id: &str, expected: &str) -> Result<Prepared> {
        self.prepare_existing(id, expected, "local_disconnect")
    }

    pub fn received_session(&mut self, id: &str, device: &str) -> Result<Intent> {
        identifier(device).map_err(|e| HostError::new(&e.code))?;
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let intent = require_current(&tx, id)?;
        if !matches!(intent.phase, Phase::LoginPending | Phase::RefreshPending)
            || intent
                .device_id
                .as_ref()
                .is_some_and(|before| before != device)
        {
            return Err(HostError::new("SYNC_AUTH_RESPONSE_MISMATCH"));
        }
        tx.execute(
            "UPDATE auth_intents SET device_id=?1,phase='store_pending' WHERE operation_id=?2",
            params![device, id],
        )?;
        let intent = get(&tx, id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        tx.commit()?;
        Ok(intent)
    }
    /// Only a matching decrypted identity may finish an interrupted encrypted save.
    /// Login/refresh response loss cannot be retried using an old session payload.
    pub fn reconcile(&mut self, id: &str, stored: Option<&StoredIdentity>) -> Result<Intent> {
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let intent = require_current(&tx, id)?;
        let phase = if intent.phase == Phase::Disconnected
            || (intent.phase == Phase::ErasePending && stored.is_none())
        {
            Phase::Disconnected
        } else if matches!(intent.kind.as_str(), "login" | "refresh")
            && matches!(
                intent.phase,
                Phase::StorePending | Phase::Active | Phase::Unknown
            )
            && stored.is_some_and(|stored| stored.matches(&intent))
        {
            Phase::Active
        } else if intent.phase == Phase::ErasePending {
            Phase::ErasePending
        } else {
            Phase::Unknown
        };
        tx.execute(
            "UPDATE auth_intents SET phase=?1 WHERE operation_id=?2",
            params![phase.name(), id],
        )?;
        let intent = get(&tx, id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        tx.commit()?;
        Ok(intent)
    }
    pub fn received_logout(&mut self, id: &str) -> Result<Intent> {
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let intent = require_current(&tx, id)?;
        if intent.kind != "logout" || intent.phase != Phase::LogoutPending {
            return Err(HostError::new("SYNC_AUTH_RESPONSE_MISMATCH"));
        }
        tx.execute("UPDATE auth_intents SET phase='erase_pending',server_logout_confirmed=1 WHERE operation_id=?1",[id])?;
        let intent = get(&tx, id)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        tx.commit()?;
        Ok(intent)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const DEVICE: &str = "owned-device-one";
    fn scope() -> Scope {
        Scope::new("https://SYNC.example:443", "reader").unwrap()
    }
    fn identity(intent: &Intent) -> StoredIdentity {
        StoredIdentity {
            endpoint: intent.endpoint.clone(),
            account: intent.account.clone(),
            device_id: intent.device_id.clone().unwrap(),
            operation_id: intent.operation_id.clone(),
            generation: intent.generation,
        }
    }
    fn active(journal: &mut Journal, scope: &Scope) -> Intent {
        let id = Uuid::new_v4().to_string();
        assert!(
            journal
                .prepare_login(&id, scope, "Owned Android 😀")
                .unwrap()
                .first_send
        );
        let saved = journal.received_session(&id, DEVICE).unwrap();
        journal.reconcile(&id, Some(&identity(&saved))).unwrap()
    }

    #[test]
    fn original_login_intent_survives_reopen_without_authorizing_a_second_send() {
        let root = tempfile::tempdir().unwrap();
        let id = Uuid::new_v4().to_string();
        {
            let mut journal = Journal::open(root.path()).unwrap();
            assert!(
                journal
                    .prepare_login(&id, &scope(), "Device")
                    .unwrap()
                    .first_send
            );
        }
        let mut journal = Journal::open(root.path()).unwrap();
        let known = journal.prepare_login(&id, &scope(), "Device").unwrap();
        assert!(!known.first_send);
        assert_eq!(known.intent.phase, Phase::LoginPending);
        assert!(journal
            .prepare_login(&Uuid::new_v4().to_string(), &scope(), "Device")
            .is_err());
        assert!(journal
            .prepare_login(
                &id,
                &Scope::new("https://other.example", "reader").unwrap(),
                "Device"
            )
            .is_err());
        assert_eq!(journal.reconcile(&id, None).unwrap().phase, Phase::Unknown);
        assert!(
            journal
                .prepare_login(&Uuid::new_v4().to_string(), &scope(), "Device")
                .unwrap()
                .first_send
        );
        assert_eq!(journal.find(&id).unwrap().unwrap().phase, Phase::Unknown);
    }
    #[test]
    fn interrupted_encrypted_save_requires_exact_scope_device_generation_and_original_operation() {
        for change in 0..5 {
            let root = tempfile::tempdir().unwrap();
            let id = Uuid::new_v4().to_string();
            let mut journal = Journal::open(root.path()).unwrap();
            journal.prepare_login(&id, &scope(), "Device").unwrap();
            let storing = journal.received_session(&id, DEVICE).unwrap();
            let mut saved = identity(&storing);
            match change {
                0 => {}
                1 => saved.account = "other".into(),
                2 => saved.endpoint = "https://other.example/".into(),
                3 => saved.device_id = "other-device".into(),
                _ => saved.generation += 1,
            }
            drop(journal);
            let mut journal = Journal::open(root.path()).unwrap();
            assert_eq!(
                journal.reconcile(&id, Some(&saved)).unwrap().phase,
                if change == 0 {
                    Phase::Active
                } else {
                    Phase::Unknown
                }
            );
        }
    }
    #[test]
    fn concurrent_indices_and_late_responses_cannot_replace_the_new_current_identity() {
        let root = tempfile::tempdir().unwrap();
        let first = Uuid::new_v4().to_string();
        let second = Uuid::new_v4().to_string();
        let mut one = Journal::open(root.path()).unwrap();
        let mut two = Journal::open(root.path()).unwrap();
        one.prepare_login(&first, &scope(), "First").unwrap();
        assert!(
            !two.prepare_login(&first, &scope(), "First")
                .unwrap()
                .first_send
        );
        one.reconcile(&first, None).unwrap();
        two.prepare_login(&second, &scope(), "Second").unwrap();
        assert_eq!(
            one.received_session(&first, DEVICE).unwrap_err().code,
            "SYNC_AUTH_IDENTITY_CHANGED"
        );
        assert_eq!(two.current(&scope()).unwrap().unwrap().operation_id, second);
        assert_eq!(one.find(&first).unwrap().unwrap().phase, Phase::Unknown);
    }
    #[test]
    fn one_use_refresh_never_replays_an_old_payload_after_a_lost_response() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let before = active(&mut journal, &scope());
        let refresh = Uuid::new_v4().to_string();
        assert!(
            journal
                .prepare_refresh(&refresh, &before.operation_id)
                .unwrap()
                .first_send
        );
        drop(journal);
        let mut journal = Journal::open(root.path()).unwrap();
        assert!(
            !journal
                .prepare_refresh(&refresh, &before.operation_id)
                .unwrap()
                .first_send
        );
        assert!(journal.prepare_refresh(&refresh, &refresh).is_err());
        assert_eq!(
            journal
                .reconcile(&refresh, Some(&identity(&before)))
                .unwrap()
                .phase,
            Phase::Unknown
        );
        assert!(journal
            .prepare_refresh(&Uuid::new_v4().to_string(), &refresh)
            .is_err());
        assert!(journal
            .received_session(&refresh, "another-device")
            .is_err());
    }
    #[test]
    fn confirmed_refresh_can_recover_only_its_new_encrypted_payload() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let before = active(&mut journal, &scope());
        let refresh = Uuid::new_v4().to_string();
        journal
            .prepare_refresh(&refresh, &before.operation_id)
            .unwrap();
        assert!(journal
            .received_session(&refresh, "another-device")
            .is_err());
        let storing = journal.received_session(&refresh, DEVICE).unwrap();
        drop(journal);
        let mut journal = Journal::open(root.path()).unwrap();
        assert_eq!(
            journal
                .reconcile(&refresh, Some(&identity(&storing)))
                .unwrap()
                .phase,
            Phase::Active
        );
        assert!(journal
            .received_session(&before.operation_id, DEVICE)
            .is_err());
    }
    #[test]
    fn logout_unknown_cannot_reactivate_old_secrets_and_local_erasure_is_not_server_revocation() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let before = active(&mut journal, &scope());
        let logout = Uuid::new_v4().to_string();
        journal
            .prepare_logout(&logout, &before.operation_id)
            .unwrap();
        let unknown = journal
            .reconcile(&logout, Some(&identity(&before)))
            .unwrap();
        assert_eq!(unknown.phase, Phase::Unknown);
        assert!(!unknown.server_logout_confirmed);
        let local = Uuid::new_v4().to_string();
        let prepared = journal.prepare_local_disconnect(&local, &logout).unwrap();
        assert!(!prepared.first_send);
        assert_eq!(prepared.intent.phase, Phase::ErasePending);
        assert!(journal.received_logout(&logout).is_err());
        assert_eq!(
            journal
                .reconcile(&local, Some(&identity(&before)))
                .unwrap()
                .phase,
            Phase::ErasePending
        );
        let done = journal.reconcile(&local, None).unwrap();
        assert_eq!(done.phase, Phase::Disconnected);
        assert!(!done.server_logout_confirmed);
    }
    #[test]
    fn remote_logout_receipt_remains_distinct_from_pending_local_secret_removal() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let before = active(&mut journal, &scope());
        let logout = Uuid::new_v4().to_string();
        journal
            .prepare_logout(&logout, &before.operation_id)
            .unwrap();
        let received = journal.received_logout(&logout).unwrap();
        assert!(received.server_logout_confirmed);
        assert_eq!(received.phase, Phase::ErasePending);
        drop(journal);
        let mut journal = Journal::open(root.path()).unwrap();
        let done = journal.reconcile(&logout, None).unwrap();
        assert_eq!(done.phase, Phase::Disconnected);
        assert!(done.server_logout_confirmed);
        let serialized = serde_json::to_string(&done).unwrap();
        assert!(!serialized.contains("token") && !serialized.contains("password"));
    }
    #[test]
    fn endpoint_identity_and_future_schema_refuse_ambiguous_or_unsupported_inputs() {
        assert_eq!(scope().endpoint(), "https://sync.example/");
        for endpoint in [
            "http://sync.example",
            "https://name:secret@sync.example",
            "https://sync.example/path",
            "https://sync.example/?token=value",
        ] {
            assert!(Scope::new(endpoint, "reader").is_err());
        }
        assert!(Scope::new("https://sync.example", "reader\nother").is_err());
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        journal.db.pragma_update(None, "user_version", 2).unwrap();
        drop(journal);
        assert!(Journal::open(root.path()).is_err());
    }
    #[test]
    fn native_auth_scope_bounds_raw_and_normalized_endpoint_without_truncating_identity() {
        assert_eq!(
            Scope::new("https://SYNC.example:443", &"用".repeat(80))
                .unwrap()
                .endpoint(),
            "https://sync.example/"
        );
        assert!(Scope::new("https://sync.example", &"用".repeat(81)).is_err());
        let oversized = format!("https://{}", "a".repeat(1020));
        assert_eq!(
            Scope::new(&oversized, "reader").err().unwrap().code,
            "SYNC_ENDPOINT_INVALID"
        );
        // Non-ASCII labels can grow through IDNA. Always bound the canonical
        // result as well as the original text, rather than shortening a host.
        let idna = format!("https://{}", vec!["一"; 140].join("."));
        assert!(idna.len() < 1024);
        assert_eq!(
            Scope::new(&idna, "reader").err().unwrap().code,
            "SYNC_ENDPOINT_INVALID"
        );
    }
    #[cfg(unix)]
    #[test]
    fn auth_index_refuses_a_symlink_without_touching_the_original_target() {
        let root = tempfile::tempdir().unwrap();
        let original = root.path().join("original");
        fs::write(&original, b"original owned bytes").unwrap();
        std::os::unix::fs::symlink(&original, root.path().join("mobile-sync-auth.sqlite3"))
            .unwrap();
        assert!(Journal::open(root.path()).is_err());
        assert_eq!(fs::read(&original).unwrap(), b"original owned bytes");
    }
}
