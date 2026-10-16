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

#[derive(Clone, Debug, Serialize)]
pub struct Rejection {
    pub status: u16,
    pub code: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Selection {
    pub revision: i64,
    pub endpoint: String,
    pub account: String,
    pub operation_id: String,
    pub generation: i64,
}
#[derive(Serialize)]
pub struct Profile {
    pub intent: Intent,
    pub rejected: Option<Rejection>,
    pub selected: bool,
}
#[derive(Serialize)]
pub struct Profiles {
    pub items: Vec<Profile>,
    pub total: u64,
    pub offset: usize,
    pub limit: usize,
    pub selection: Option<Selection>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Choice {
    pub operation_id: String,
    pub expected_revision: Option<i64>,
    pub selection: Selection,
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
fn choice(db: &Connection, id: &str) -> Result<Option<Choice>> {
    operation_id(id)?;
    let row:Option<(String,i64,Option<i64>,i64)>=db.query_row("SELECT target_operation,target_generation,expected_revision,result_revision FROM auth_profile_choices WHERE operation_id=?1",[id],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?))).optional()?;
    row.map(|(target, generation, expected_revision, revision)| {
        let intent = get(db, &target)?.ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))?;
        if intent.generation != generation || revision <= 0 {
            return Err(HostError::new("SYNC_AUTH_INDEX_INVALID"));
        }
        Ok(Choice {
            operation_id: id.into(),
            expected_revision,
            selection: Selection {
                revision,
                endpoint: intent.endpoint,
                account: intent.account,
                operation_id: target,
                generation,
            },
        })
    })
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

fn selection(db: &Connection) -> Result<Option<Selection>> {
    let value=db.query_row("SELECT revision,endpoint,account,operation_id,generation FROM auth_selection WHERE singleton=1",[],|row| {
        Ok(Selection{revision:row.get(0)?,endpoint:row.get(1)?,account:row.get(2)?,operation_id:row.get(3)?,generation:row.get(4)?})
    }).optional()?;
    if let Some(value) = &value {
        let scope = Scope::new(&value.endpoint, &value.account)?;
        operation_id(&value.operation_id)?;
        if value.revision <= 0 || value.generation <= 0 || scope.endpoint() != value.endpoint {
            return Err(HostError::new("SYNC_AUTH_INDEX_INVALID"));
        }
    }
    Ok(value)
}
fn require_selected(db: &Connection, intent: &Intent) -> Result<()> {
    if selection(db)?.is_none_or(|s| {
        s.operation_id != intent.operation_id
            || s.generation != intent.generation
            || s.endpoint != intent.endpoint
            || s.account != intent.account
    }) {
        return Err(HostError::new("SYNC_AUTH_SELECTION_CHANGED"));
    }
    Ok(())
}
fn select_current(db: &Connection, intent: &Intent) -> Result<Selection> {
    let revision = selection(db)?.map_or(Ok(1), |s| {
        s.revision
            .checked_add(1)
            .ok_or_else(|| HostError::new("SYNC_AUTH_INDEX_INVALID"))
    })?;
    db.execute("INSERT INTO auth_selection VALUES (1,?1,?2,?3,?4,?5) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,endpoint=excluded.endpoint,account=excluded.account,operation_id=excluded.operation_id,generation=excluded.generation",
        params![revision,intent.endpoint,intent.account,intent.operation_id,intent.generation])?;
    Ok(Selection {
        revision,
        endpoint: intent.endpoint.clone(),
        account: intent.account.clone(),
        operation_id: intent.operation_id.clone(),
        generation: intent.generation,
    })
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
            CREATE TABLE IF NOT EXISTS auth_rejections(operation_id TEXT PRIMARY KEY,status INTEGER NOT NULL CHECK(status BETWEEN 400 AND 499),code TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS auth_selection(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>0),endpoint TEXT NOT NULL,account TEXT NOT NULL,operation_id TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>0));
            CREATE TABLE IF NOT EXISTS auth_profile_choices(operation_id TEXT PRIMARY KEY,target_operation TEXT NOT NULL,target_generation INTEGER NOT NULL CHECK(target_generation>0),expected_revision INTEGER,result_revision INTEGER NOT NULL CHECK(result_revision>0));
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
    /// Nonsecret raw selection stamp. A legacy/stale stamp is not activation.
    pub fn selection(&self) -> Result<Option<Selection>> {
        selection(&self.db)
    }
    pub fn selected(&self) -> Result<Option<Intent>> {
        let Some(selected) = selection(&self.db)? else {
            return Ok(None);
        };
        let intent = require_current(&self.db, &selected.operation_id)?;
        require_selected(&self.db, &intent)?;
        Ok(Some(intent))
    }
    pub fn require_selected(&self, intent: &Intent) -> Result<()> {
        require_selected(&self.db, intent)
    }
    /// Explicit local selection has no remote request or secret read. Both CAS
    /// stamps are required so an old page or an A/B/A switch cannot win later.
    pub fn choice(&self, id: &str) -> Result<Option<Choice>> {
        choice(&self.db, id)
    }
    pub fn choose(
        &mut self,
        operation: &str,
        id: &str,
        generation: i64,
        expected_revision: Option<i64>,
    ) -> Result<Choice> {
        operation_id(operation)?;
        operation_id(id)?;
        if generation <= 0 || expected_revision.is_some_and(|v| v <= 0) {
            return Err(HostError::new("SYNC_AUTH_SELECTION_INVALID"));
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(saved) = choice(&tx, operation)? {
            if saved.selection.operation_id != id
                || saved.selection.generation != generation
                || saved.expected_revision != expected_revision
            {
                return Err(HostError::new("SYNC_AUTH_INTENT_MISMATCH"));
            }
            return Ok(saved);
        }
        let before = selection(&tx)?;
        if before.as_ref().map(|s| s.revision) != expected_revision {
            return Err(HostError::new("SYNC_AUTH_SELECTION_CHANGED"));
        }
        let intent = require_current(&tx, id)?;
        if intent.generation != generation {
            return Err(HostError::new("SYNC_AUTH_IDENTITY_CHANGED"));
        }
        let selected = if let Some(before) = before.filter(|s| {
            s.operation_id == id
                && s.generation == generation
                && s.endpoint == intent.endpoint
                && s.account == intent.account
        }) {
            before
        } else {
            select_current(&tx, &intent)?
        };
        tx.execute(
            "INSERT INTO auth_profile_choices VALUES (?1,?2,?3,?4,?5)",
            params![
                operation,
                id,
                generation,
                expected_revision,
                selected.revision
            ],
        )?;
        tx.commit()?;
        Ok(Choice {
            operation_id: operation.into(),
            expected_revision,
            selection: selected,
        })
    }
    pub fn profiles(&self, offset: usize, limit: usize) -> Result<Profiles> {
        if offset > 10000 || !(1..=50).contains(&limit) {
            return Err(HostError::new("SYNC_AUTH_PROFILE_PAGE_INVALID"));
        }
        let tx = self.db.unchecked_transaction()?;
        let selected = selection(&tx)?;
        let total = tx.query_row("SELECT COUNT(*) FROM auth_scopes", [], |row| row.get(0))?;
        let ids = {
            let mut statement=tx.prepare("SELECT current_operation FROM auth_scopes ORDER BY endpoint,account LIMIT ?1 OFFSET ?2")?;
            let rows =
                statement.query_map(params![limit, offset], |row| row.get::<_, String>(0))?;
            rows.collect::<std::result::Result<Vec<_>, _>>()?
        };
        let mut items = Vec::with_capacity(ids.len());
        for id in ids {
            let intent = require_current(&tx, &id)?;
            let rejected = tx
                .query_row(
                    "SELECT status,code FROM auth_rejections WHERE operation_id=?1",
                    [&id],
                    |row| {
                        Ok(Rejection {
                            status: row.get(0)?,
                            code: row.get(1)?,
                        })
                    },
                )
                .optional()?;
            let current = selected.as_ref().is_some_and(|s| {
                s.operation_id == id
                    && s.generation == intent.generation
                    && s.endpoint == intent.endpoint
                    && s.account == intent.account
            });
            items.push(Profile {
                intent,
                rejected,
                selected: current,
            });
        }
        tx.commit()?;
        Ok(Profiles {
            items,
            total,
            offset,
            limit,
            selection: selected,
        })
    }
    pub fn rejection(&self, id: &str) -> Result<Option<Rejection>> {
        operation_id(id)?;
        Ok(self
            .db
            .query_row(
                "SELECT status,code FROM auth_rejections WHERE operation_id=?1",
                [id],
                |row| {
                    Ok(Rejection {
                        status: row.get(0)?,
                        code: row.get(1)?,
                    })
                },
            )
            .optional()?)
    }
    /// Only documented pre-mutation Sync v1 authentication rejections qualify.
    /// Network/5xx/invalid-success responses remain uncertain, without a receipt.
    pub fn record_rejection(&mut self, id: &str, status: u16, code: &str) -> Result<bool> {
        if !(400..500).contains(&status) {
            return Ok(false);
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let intent = require_current(&tx, id)?;
        let known = matches!(
            (intent.kind.as_str(), status, code),
            ("login", 401, "LOGIN_FAILED")
                | ("login", 429, "RATE_LIMITED")
                | ("login", 422, "INVALID_REQUEST")
                | ("refresh", 401, "SESSION_EXPIRED")
                | ("refresh", 422, "INVALID_REQUEST")
                | ("logout", 401, "SESSION_EXPIRED")
        );
        if !known {
            return Ok(false);
        }
        let old: Option<(u16, String)> = tx
            .query_row(
                "SELECT status,code FROM auth_rejections WHERE operation_id=?1",
                [id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        if let Some((old_status, old_code)) = old {
            if old_status != status || old_code != code {
                return Err(HostError::new("SYNC_AUTH_INTENT_MISMATCH"));
            }
            return Ok(true);
        }
        if !matches!(
            intent.phase,
            Phase::LoginPending | Phase::RefreshPending | Phase::LogoutPending
        ) {
            return Err(HostError::new("SYNC_AUTH_RESPONSE_MISMATCH"));
        }
        tx.execute(
            "INSERT INTO auth_rejections VALUES (?1,?2,?3)",
            params![id, status, code],
        )?;
        tx.execute(
            "UPDATE auth_intents SET phase='unknown' WHERE operation_id=?1",
            [id],
        )?;
        tx.commit()?;
        Ok(true)
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
        select_current(&tx, &intent)?;
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
        if selection(&tx)?.is_some() {
            require_selected(&tx, &previous)?;
        }
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
        select_current(&tx, &intent)?;
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
        require_selected(&tx, &intent)?;
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
        require_selected(&tx, &intent)?;
        let phase = if intent.phase == Phase::Disconnected
            || (intent.phase == Phase::ErasePending && stored.is_none())
        {
            Phase::Disconnected
        } else if matches!(intent.kind.as_str(), "login" | "refresh")
            && !tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM auth_rejections WHERE operation_id=?1)",
                [id],
                |row| row.get::<_, bool>(0),
            )?
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
        require_selected(&tx, &intent)?;
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
    #[test]
    fn native_selection_recovers_pending_original_scope_after_cold_open_without_guessing_a_profile()
    {
        let root = tempfile::tempdir().unwrap();
        let id = Uuid::new_v4().to_string();
        let mut journal = Journal::open(root.path()).unwrap();
        assert!(
            journal
                .prepare_login(&id, &scope(), "Owned phone")
                .unwrap()
                .first_send
        );
        let selected = journal.selection().unwrap().unwrap();
        assert_eq!(selected.operation_id, id);
        assert_eq!(selected.revision, 1);
        assert!(
            !journal
                .prepare_login(&id, &scope(), "Owned phone")
                .unwrap()
                .first_send
        );
        assert_eq!(journal.selection().unwrap().unwrap(), selected);
        drop(journal);
        let reopened = Journal::open(root.path()).unwrap();
        let pending = reopened.selected().unwrap().unwrap();
        assert_eq!(pending.phase, Phase::LoginPending);
        assert_eq!(pending.endpoint, scope().endpoint());
        assert_eq!(pending.account, scope().account());
        assert_eq!(pending.operation_id, id);
        assert_eq!(reopened.selection().unwrap().unwrap(), selected);
    }
    #[test]
    fn profile_pages_are_bounded_deterministic_readonly_metadata_with_one_current_selection() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        for n in 0..51 {
            let scope = Scope::new("https://sync.example", &format!("reader-{n:02}")).unwrap();
            journal
                .prepare_login(&Uuid::new_v4().to_string(), &scope, "Owned phone")
                .unwrap();
        }
        let changes = journal
            .db
            .query_row("SELECT total_changes()", [], |r| r.get::<_, i64>(0))
            .unwrap();
        let selected = journal.selection().unwrap();
        let first = journal.profiles(0, 20).unwrap();
        let second = journal.profiles(20, 20).unwrap();
        let last = journal.profiles(40, 20).unwrap();
        assert_eq!(
            (
                first.total,
                first.items.len(),
                second.items.len(),
                last.items.len()
            ),
            (51, 20, 20, 11)
        );
        assert_eq!(first.items[0].intent.account, "reader-00");
        assert_eq!(second.items[0].intent.account, "reader-20");
        assert_eq!(
            first
                .items
                .iter()
                .chain(second.items.iter())
                .chain(last.items.iter())
                .filter(|x| x.selected)
                .count(),
            1
        );
        assert!(last.items.last().unwrap().selected);
        assert!(journal.profiles(51, 20).unwrap().items.is_empty());
        for (offset, limit) in [(10001, 20), (0, 0), (0, 51)] {
            assert!(journal.profiles(offset, limit).is_err());
        }
        assert_eq!(journal.selection().unwrap(), selected);
        assert_eq!(
            journal
                .db
                .query_row("SELECT total_changes()", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            changes
        );
        let bytes = serde_json::to_string(&first).unwrap();
        assert!(!bytes.contains("password") && !bytes.contains("token"));
    }
    #[test]
    fn original_choice_receipt_survives_later_selection_and_stale_aba_confirmation_cannot_win() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let a = Uuid::new_v4().to_string();
        let b = Uuid::new_v4().to_string();
        let choose_a = Uuid::new_v4().to_string();
        journal.prepare_login(&a, &scope(), "A").unwrap();
        journal
            .prepare_login(
                &b,
                &Scope::new("https://other.example", "reader").unwrap(),
                "B",
            )
            .unwrap();
        let original = journal.choose(&choose_a, &a, 1, Some(2)).unwrap();
        assert_eq!(original.selection.revision, 3);
        journal
            .choose(&Uuid::new_v4().to_string(), &b, 1, Some(3))
            .unwrap();
        let current = journal.selection().unwrap().unwrap();
        assert_eq!(current.operation_id, b);
        assert_eq!(current.revision, 4);
        let replay = journal.choose(&choose_a, &a, 1, Some(2)).unwrap();
        assert_eq!(
            serde_json::to_value(&replay).unwrap(),
            serde_json::to_value(&original).unwrap()
        );
        assert_eq!(journal.selection().unwrap().unwrap(), current);
        assert!(journal.choose(&choose_a, &b, 1, Some(2)).is_err());
        assert!(journal
            .choose(&Uuid::new_v4().to_string(), &a, 1, Some(2))
            .is_err());
        journal
            .choose(&Uuid::new_v4().to_string(), &a, 1, Some(4))
            .unwrap();
        assert!(journal
            .choose(&Uuid::new_v4().to_string(), &b, 1, Some(3))
            .is_err());
        assert_eq!(journal.selected().unwrap().unwrap().operation_id, a);
        drop(journal);
        let reopened = Journal::open(root.path()).unwrap();
        assert_eq!(
            serde_json::to_value(reopened.choice(&choose_a).unwrap().unwrap()).unwrap(),
            serde_json::to_value(original).unwrap()
        );
    }
    #[test]
    fn obsolete_profile_generation_and_wrong_cas_leave_no_choice_receipt_or_selection_change() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let old = Uuid::new_v4().to_string();
        let new = Uuid::new_v4().to_string();
        journal.prepare_login(&old, &scope(), "Old").unwrap();
        journal.reconcile(&old, None).unwrap();
        journal.prepare_login(&new, &scope(), "New").unwrap();
        let before = journal.selection().unwrap();
        for (target, generation, revision) in
            [(&old, 1, Some(2)), (&new, 1, Some(2)), (&new, 2, Some(1))]
        {
            let operation = Uuid::new_v4().to_string();
            assert!(journal
                .choose(&operation, target, generation, revision)
                .is_err());
            assert!(journal.choice(&operation).unwrap().is_none());
            assert_eq!(journal.selection().unwrap(), before);
        }
    }
    #[test]
    fn legacy_auth_database_without_selection_requires_explicit_choice_and_preserves_original_rows()
    {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let id = Uuid::new_v4().to_string();
        journal.prepare_login(&id, &scope(), "Original").unwrap();
        let received = journal.received_session(&id, "device").unwrap();
        journal.reconcile(&id, Some(&identity(&received))).unwrap();
        let before = serde_json::to_value(journal.find(&id).unwrap().unwrap()).unwrap();
        journal
            .db
            .execute_batch("DROP TABLE auth_selection; DROP TABLE auth_profile_choices;")
            .unwrap();
        drop(journal);
        let mut reopened = Journal::open(root.path()).unwrap();
        assert!(reopened.selected().unwrap().is_none());
        assert_eq!(reopened.profiles(0, 20).unwrap().total, 1);
        assert!(reopened.reconcile(&id, Some(&identity(&received))).is_err());
        assert_eq!(
            serde_json::to_value(reopened.find(&id).unwrap().unwrap()).unwrap(),
            before
        );
        let chosen = reopened
            .choose(&Uuid::new_v4().to_string(), &id, 1, None)
            .unwrap();
        assert_eq!(chosen.selection.revision, 1);
        assert_eq!(
            reopened
                .reconcile(&id, Some(&identity(&received)))
                .unwrap()
                .phase,
            Phase::Active
        );
        assert_eq!(
            serde_json::to_value(reopened.find(&id).unwrap().unwrap()).unwrap(),
            before
        );
    }
    #[test]
    fn nonselected_account_cannot_accept_session_metadata_reconcile_or_prepare_refresh() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let a = Uuid::new_v4().to_string();
        let b = Uuid::new_v4().to_string();
        journal.prepare_login(&a, &scope(), "A").unwrap();
        let received = journal.received_session(&a, "device-a").unwrap();
        journal.reconcile(&a, Some(&identity(&received))).unwrap();
        journal
            .prepare_login(
                &b,
                &Scope::new("https://other.example", "reader").unwrap(),
                "B",
            )
            .unwrap();
        assert_eq!(
            journal
                .reconcile(&a, Some(&identity(&received)))
                .err()
                .unwrap()
                .code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        let refresh = Uuid::new_v4().to_string();
        assert!(journal.prepare_refresh(&refresh, &a).is_err());
        assert!(journal.find(&refresh).unwrap().is_none());
        assert_eq!(journal.selected().unwrap().unwrap().operation_id, b);
    }
    #[test]
    fn selection_revision_exhaustion_rolls_back_the_entire_new_login_intent_before_send() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let a = Uuid::new_v4().to_string();
        journal.prepare_login(&a, &scope(), "A").unwrap();
        journal
            .db
            .execute("UPDATE auth_selection SET revision=?1", [i64::MAX])
            .unwrap();
        let b = Uuid::new_v4().to_string();
        let other = Scope::new("https://other.example", "reader").unwrap();
        assert_eq!(
            journal.prepare_login(&b, &other, "B").err().unwrap().code,
            "SYNC_AUTH_INDEX_INVALID"
        );
        assert!(journal.find(&b).unwrap().is_none());
        assert!(journal.current(&other).unwrap().is_none());
        assert_eq!(journal.selection().unwrap().unwrap().revision, i64::MAX);
        assert_eq!(journal.selected().unwrap().unwrap().operation_id, a);
    }
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
    #[test]
    fn known_refresh_rejection_is_immutable_and_cannot_be_reactivated_by_matching_local_metadata() {
        let root = tempfile::tempdir().unwrap();
        let mut journal = Journal::open(root.path()).unwrap();
        let login = Uuid::new_v4().to_string();
        journal.prepare_login(&login, &scope(), "Owned").unwrap();
        let before = journal.received_session(&login, "device").unwrap();
        let saved = identity(&before);
        journal.reconcile(&login, Some(&saved)).unwrap();
        let refresh = Uuid::new_v4().to_string();
        let pending = journal.prepare_refresh(&refresh, &login).unwrap().intent;
        assert!(!journal
            .record_rejection(&refresh, 503, "SESSION_EXPIRED")
            .unwrap());
        assert!(journal.rejection(&refresh).unwrap().is_none());
        assert!(journal
            .record_rejection(&refresh, 401, "SESSION_EXPIRED")
            .unwrap());
        assert!(journal
            .record_rejection(&refresh, 401, "SESSION_EXPIRED")
            .unwrap());
        let forged_matching = identity(&pending);
        assert_eq!(
            journal
                .reconcile(&refresh, Some(&forged_matching))
                .unwrap()
                .phase,
            Phase::Unknown
        );
        drop(journal);
        let journal = Journal::open(root.path()).unwrap();
        assert_eq!(
            journal.rejection(&refresh).unwrap().unwrap().code,
            "SESSION_EXPIRED"
        );
        assert_eq!(
            journal.find(&refresh).unwrap().unwrap().phase,
            Phase::Unknown
        );
    }
    #[test]
    fn optional_rejection_metadata_preserves_existing_v1_intents_and_active_identity_on_open() {
        let root = tempfile::tempdir().unwrap();
        let login = Uuid::new_v4().to_string();
        let mut journal = Journal::open(root.path()).unwrap();
        journal
            .prepare_login(&login, &scope(), "Owned original device")
            .unwrap();
        let received = journal.received_session(&login, "device").unwrap();
        journal
            .reconcile(&login, Some(&identity(&received)))
            .unwrap();
        let original = serde_json::to_value(journal.find(&login).unwrap().unwrap()).unwrap();
        // Model the already-created native v1 database from the previous
        // journal source, which had no optional rejection metadata table.
        journal
            .db
            .execute_batch("DROP TABLE auth_rejections;")
            .unwrap();
        drop(journal);
        let mut reopened = Journal::open(root.path()).unwrap();
        assert_eq!(
            serde_json::to_value(reopened.find(&login).unwrap().unwrap()).unwrap(),
            original
        );
        assert_eq!(
            reopened.current(&scope()).unwrap().unwrap().operation_id,
            login
        );
        assert!(reopened.rejection(&login).unwrap().is_none());
        let refresh = Uuid::new_v4().to_string();
        assert!(
            reopened
                .prepare_refresh(&refresh, &login)
                .unwrap()
                .first_send
        );
        assert!(reopened
            .record_rejection(&refresh, 401, "SESSION_EXPIRED")
            .unwrap());
        assert_eq!(
            serde_json::to_value(reopened.find(&login).unwrap().unwrap()).unwrap(),
            original
        );
        assert_eq!(
            reopened
                .db
                .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            1
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
