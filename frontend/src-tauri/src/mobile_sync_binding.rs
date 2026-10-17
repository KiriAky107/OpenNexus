//! Original local association intents. Reading an intent never retries it.
//! Its one-use ticket joins the existing initial-binding transaction.
use crate::{
    mobile_sync_session::{Scope, Selection},
    sync_client::identifier,
    sync_state::Binding,
    workspace::{hash, HostError, Result, Workspace},
};
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Recover original local metadata under the selected native identity without
/// opening its credential payload, starting HTTP, or retrying a confirmation.
pub async fn operation<S: crate::mobile_sync_auth::SecretStore>(
    auth: &mut crate::mobile_sync_auth::Auth<S>,
    lease: &crate::mobile_sync_foreground::Lease,
    expected: &Selection,
    engine: &std::sync::Arc<std::sync::Mutex<Option<crate::mobile_workspace::MobileWorkspace>>>,
    operation: &str,
) -> std::result::Result<Option<Intent>, crate::sync_client::SyncError> {
    let operation = operation.to_owned();
    selected_read(auth, lease, expected, engine, move |ws, scope| {
        ws.mobile_binding_read(scope, &operation)
    })
    .await
}
pub async fn pending<S: crate::mobile_sync_auth::SecretStore>(
    auth: &mut crate::mobile_sync_auth::Auth<S>,
    lease: &crate::mobile_sync_foreground::Lease,
    expected: &Selection,
    engine: &std::sync::Arc<std::sync::Mutex<Option<crate::mobile_workspace::MobileWorkspace>>>,
    after: Option<&str>,
    limit: usize,
) -> std::result::Result<Page, crate::sync_client::SyncError> {
    let after = after.map(str::to_owned);
    selected_read(auth, lease, expected, engine, move |ws, scope| {
        ws.mobile_binding_pending(scope, after.as_deref(), limit)
    })
    .await
}
#[derive(Serialize)]
pub struct Status {
    pub vault_id: String,
    pub binding: Option<Binding>,
    pub original_operation_id: Option<String>,
    pub another_connection: bool,
}
pub async fn status<S: crate::mobile_sync_auth::SecretStore>(
    auth: &mut crate::mobile_sync_auth::Auth<S>,
    lease: &crate::mobile_sync_foreground::Lease,
    expected: &Selection,
    engine: &std::sync::Arc<std::sync::Mutex<Option<crate::mobile_workspace::MobileWorkspace>>>,
) -> std::result::Result<Status, crate::sync_client::SyncError> {
    selected_read(auth, lease, expected, engine, |ws, scope| {
        let current = ws.sync_binding()?;
        let another_connection = current.as_ref().is_some_and(|binding| {
            binding.endpoint != scope.endpoint() || binding.account != scope.account()
        });
        let original_operation_id = match current.as_ref().filter(|_| !another_connection) {
            Some(binding) => ws.mobile_binding_original(scope, &binding.id)?,
            None => None,
        };
        Ok(Status {
            vault_id: ws.vault_id.clone(),
            binding: current.filter(|_| !another_connection),
            original_operation_id,
            another_connection,
        })
    })
    .await
}
async fn selected_read<S: crate::mobile_sync_auth::SecretStore, T: Send + 'static>(
    auth: &mut crate::mobile_sync_auth::Auth<S>,
    lease: &crate::mobile_sync_foreground::Lease,
    expected: &Selection,
    engine: &std::sync::Arc<std::sync::Mutex<Option<crate::mobile_workspace::MobileWorkspace>>>,
    action: impl FnOnce(&Workspace, &Scope) -> Result<T> + Send + 'static,
) -> std::result::Result<T, crate::sync_client::SyncError> {
    use crate::sync_client::SyncError;
    if auth.selection(lease)?.as_ref() != Some(expected) {
        return Err(SyncError::new("SYNC_AUTH_SELECTION_CHANGED"));
    }
    let scope = Scope::new(&expected.endpoint, &expected.account)?;
    let engine = engine.clone();
    let lease = lease.clone();
    tokio::task::spawn_blocking(move || {
        let guard = engine.lock().map_err(|_| SyncError::new("HOST_BUSY"))?;
        lease.with_current(|| {
            let ws = &guard
                .as_ref()
                .ok_or_else(|| SyncError::new("MOBILE_STORAGE_UNAVAILABLE"))?
                .workspace;
            action(ws, &scope).map_err(Into::into)
        })
    })
    .await
    .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))?
}

const MAX_RECORD: usize = 4096;
const MAX_SAFE_INTEGER: i64 = 9_007_199_254_740_991;

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub operation_id: String,
    pub local_vault: String,
    pub remote_vault: String,
    pub selection: Selection,
    pub preview_fingerprint: String,
}
impl Request {
    fn validate(&self, vault: &str) -> Result<Scope> {
        let scope = Scope::new(&self.selection.endpoint, &self.selection.account)?;
        if self.local_vault != vault
            || !canonical_uuid(&self.operation_id)
            || !canonical_uuid(&self.selection.operation_id)
            || !(1..=MAX_SAFE_INTEGER).contains(&self.selection.revision)
            || !(1..=MAX_SAFE_INTEGER).contains(&self.selection.generation)
            || self.selection.endpoint != scope.endpoint()
            || identifier(&self.remote_vault).is_err()
            || self.preview_fingerprint.len() != 64
            || !self
                .preview_fingerprint
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            || serde_json::to_vec(self).map_err(|_| invalid())?.len() > MAX_RECORD
        {
            return Err(HostError::new("SYNC_BINDING_REQUEST_INVALID"));
        }
        Ok(scope)
    }
    fn fingerprint(&self) -> Result<String> {
        // A versioned tuple keeps this contract independent of struct field order.
        Ok(hash(
            &serde_json::to_vec(&(
                "mobile-initial-binding-v1",
                &self.operation_id,
                &self.local_vault,
                &self.remote_vault,
                self.selection.revision,
                &self.selection.endpoint,
                &self.selection.account,
                &self.selection.operation_id,
                self.selection.generation,
                &self.preview_fingerprint,
            ))
            .map_err(|_| invalid())?,
        ))
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Prepared,
    Bound,
}
#[derive(Serialize)]
pub struct Intent {
    pub request: Request,
    pub phase: Phase,
    pub attempt_revision: i64,
    /// Historical result, distinct from the workspace's current binding.
    pub binding: Option<Binding>,
}
#[derive(Serialize)]
pub struct Page {
    pub items: Vec<Intent>,
    pub next_after: Option<String>,
}

/// Never serialized, cloned, reconstructed from IPC, or issued by a read.
pub struct Ticket {
    request: Request,
    fingerprint: String,
    revision: i64,
}
pub struct Prepared {
    pub intent: Intent,
    pub ticket: Option<Ticket>,
}

fn canonical_uuid(value: &str) -> bool {
    value.len() == 36 && Uuid::parse_str(value).is_ok_and(|id| id.to_string() == value)
}
fn invalid() -> HostError {
    HostError::new("SYNC_BINDING_INTENT_INVALID")
}
fn exists(db: &Connection) -> Result<bool> {
    Ok(db.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_sync_binding_intents')",
        [], |row| row.get(0),
    )?)
}
fn load(db: &Connection, operation: &str, vault: &str) -> Result<Option<Intent>> {
    if !exists(db)? {
        return Ok(None);
    }
    type Row = (
        String,
        String,
        Option<String>,
        String,
        i64,
        Option<String>,
        String,
        bool,
    );
    let row: Option<Row> = db
        .query_row(
            "SELECT endpoint,account,
        CASE WHEN length(CAST(request AS BLOB)) BETWEEN 1 AND 4096 THEN request END,
        state,attempt_revision,
        CASE WHEN length(CAST(binding AS BLOB)) BETWEEN 1 AND 4096 THEN binding END,
        request_hash,binding IS NULL FROM mobile_sync_binding_intents WHERE operation_id=?1",
            [operation],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                    row.get(7)?,
                ))
            },
        )
        .optional()?;
    row.map(
        |(endpoint, account, request, state, revision, binding, fingerprint, binding_null)| {
            let request: Request =
                serde_json::from_str(&request.ok_or_else(invalid)?).map_err(|_| invalid())?;
            let scope = request.validate(vault).map_err(|_| invalid())?;
            if request.operation_id != operation
                || endpoint != scope.endpoint()
                || account != scope.account()
                || request.fingerprint()? != fingerprint
                || !(1..=MAX_SAFE_INTEGER).contains(&revision)
            {
                return Err(invalid());
            }
            let (phase, binding) = match (state.as_str(), binding, binding_null) {
                ("prepared", None, true) => (Phase::Prepared, None),
                ("bound", Some(value), false) => {
                    let binding: Binding = serde_json::from_str(&value).map_err(|_| invalid())?;
                    validate_binding(&request, &binding)?;
                    (Phase::Bound, Some(binding))
                }
                _ => return Err(invalid()),
            };
            Ok(Intent {
                request,
                phase,
                attempt_revision: revision,
                binding,
            })
        },
    )
    .transpose()
}
fn validate_binding(request: &Request, binding: &Binding) -> Result<()> {
    if !canonical_uuid(&binding.id)
        || binding.endpoint != request.selection.endpoint
        || binding.account != request.selection.account
        || binding.remote_vault != request.remote_vault
        || !(0..=MAX_SAFE_INTEGER).contains(&binding.cursor)
    {
        return Err(invalid());
    }
    Ok(())
}
fn same_request(intent: &Intent, request: &Request) -> Result<()> {
    if intent.request != *request {
        return Err(HostError::new("SYNC_OPERATION_MISMATCH"));
    }
    Ok(())
}
fn prepared(intent: Intent, first: bool) -> Result<Prepared> {
    let ticket = if first {
        Some(Ticket {
            fingerprint: intent.request.fingerprint()?,
            request: intent.request.clone(),
            revision: intent.attempt_revision,
        })
    } else {
        None
    };
    Ok(Prepared { intent, ticket })
}

impl Workspace {
    fn mobile_binding_original(&self, scope: &Scope, binding: &str) -> Result<Option<String>> {
        if !exists(&self.db)? {
            return Ok(None);
        }
        // Cold WebView recovery must also find a confirmation that committed
        // before its result arrived. Match its actual binding id, never the
        // newest-created intent (an older prepared intent may finish later).
        let mut statement=self.db.prepare(
            "SELECT operation_id FROM mobile_sync_binding_intents WHERE endpoint=?1 AND account=?2 AND state='bound'
            AND CASE WHEN length(CAST(binding AS BLOB)) BETWEEN 1 AND 4096 AND json_valid(binding)
            THEN json_extract(binding,'$.id') END=?3 LIMIT 2")?;
        let ids = statement
            .query_map(params![scope.endpoint(), scope.account(), binding], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        if ids.len() > 1 {
            return Err(invalid());
        }
        if let Some(id) = ids.first() {
            let original = load(&self.db, id, &self.vault_id)?.ok_or_else(invalid)?;
            if original
                .binding
                .as_ref()
                .is_none_or(|value| value.id != binding)
            {
                return Err(invalid());
            }
        }
        Ok(ids.into_iter().next())
    }
    pub(crate) fn mobile_binding_read(
        &self,
        scope: &Scope,
        operation: &str,
    ) -> Result<Option<Intent>> {
        if !canonical_uuid(operation) {
            return Err(HostError::new("SYNC_BINDING_REQUEST_INVALID"));
        }
        // Scope is applied before decoding any other account's stored request.
        if !exists(&self.db)? {
            return Ok(None);
        }
        let visible: bool = self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM mobile_sync_binding_intents WHERE operation_id=?1 AND endpoint=?2 AND account=?3)",
            params![operation,scope.endpoint(),scope.account()], |row| row.get(0),
        )?;
        if !visible {
            return Ok(None);
        }
        load(&self.db, operation, &self.vault_id)
    }
    pub(crate) fn mobile_binding_pending(
        &self,
        scope: &Scope,
        after: Option<&str>,
        limit: usize,
    ) -> Result<Page> {
        if !(1..=50).contains(&limit) || after.is_some_and(|id| !canonical_uuid(id)) {
            return Err(HostError::new("SYNC_BINDING_PAGE_INVALID"));
        }
        if !exists(&self.db)? {
            return Ok(Page {
                items: vec![],
                next_after: None,
            });
        }
        let mut statement = self.db.prepare(
            "SELECT operation_id FROM mobile_sync_binding_intents WHERE endpoint=?1 AND account=?2 AND state='prepared' AND operation_id>?3 ORDER BY operation_id LIMIT ?4",
        )?;
        let ids = statement
            .query_map(
                params![
                    scope.endpoint(),
                    scope.account(),
                    after.unwrap_or(""),
                    (limit + 1) as i64
                ],
                |row| row.get::<_, String>(0),
            )?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let next_after = if ids.len() > limit {
            ids.get(limit - 1).cloned()
        } else {
            None
        };
        let items = ids
            .into_iter()
            .take(limit)
            .map(|id| load(&self.db, &id, &self.vault_id)?.ok_or_else(invalid))
            .collect::<Result<Vec<_>>>()?;
        Ok(Page { items, next_after })
    }
    pub(crate) fn mobile_binding_begin(&self, request: &Request) -> Result<Prepared> {
        request.validate(&self.vault_id)?;
        if let Some(intent) = load(&self.db, &request.operation_id, &self.vault_id)? {
            same_request(&intent, request)?;
            return prepared(intent, false);
        }
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        let tx = self.db.unchecked_transaction()?;
        tx.execute_batch(
            "CREATE TABLE IF NOT EXISTS mobile_sync_binding_intents (
            operation_id TEXT PRIMARY KEY,endpoint TEXT NOT NULL,account TEXT NOT NULL,
            request_hash TEXT NOT NULL,request TEXT NOT NULL,state TEXT NOT NULL,
            attempt_revision INTEGER NOT NULL,binding TEXT);
            CREATE INDEX IF NOT EXISTS mobile_sync_binding_scope ON mobile_sync_binding_intents(endpoint,account,state,operation_id);",
        )?;
        tx.execute(
            "INSERT INTO mobile_sync_binding_intents VALUES (?1,?2,?3,?4,?5,'prepared',1,NULL)",
            params![
                request.operation_id,
                request.selection.endpoint,
                request.selection.account,
                request.fingerprint()?,
                serde_json::to_string(request).map_err(|_| invalid())?
            ],
        )?;
        tx.commit()?;
        prepared(
            Intent {
                request: request.clone(),
                phase: Phase::Prepared,
                attempt_revision: 1,
                binding: None,
            },
            true,
        )
    }
    pub(crate) fn mobile_binding_resume(
        &self,
        request: &Request,
        observed_revision: i64,
    ) -> Result<Prepared> {
        request.validate(&self.vault_id)?;
        if !(1..MAX_SAFE_INTEGER).contains(&observed_revision) {
            return Err(HostError::new("SYNC_BINDING_REQUEST_INVALID"));
        }
        let mut intent = load(&self.db, &request.operation_id, &self.vault_id)?
            .ok_or_else(|| HostError::new("SYNC_BINDING_INTENT_NOT_FOUND"))?;
        same_request(&intent, request)?;
        if intent.phase == Phase::Bound || intent.attempt_revision != observed_revision {
            return prepared(intent, false);
        }
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        let changed = self.db.execute(
            "UPDATE mobile_sync_binding_intents SET attempt_revision=attempt_revision+1 WHERE operation_id=?1 AND request_hash=?2 AND state='prepared' AND attempt_revision=?3",
            params![request.operation_id,request.fingerprint()?,observed_revision],
        )?;
        if changed != 1 {
            return Err(HostError::new("SYNC_BINDING_INTENT_CHANGED"));
        }
        intent.attempt_revision += 1;
        prepared(intent, true)
    }
}
impl Ticket {
    pub(crate) fn request(&self) -> &Request {
        &self.request
    }
    pub(crate) fn record(self, tx: &Transaction<'_>, binding: &Binding) -> Result<()> {
        validate_binding(&self.request, binding)?;
        let changed = tx.execute(
            "UPDATE mobile_sync_binding_intents SET state='bound',binding=?4 WHERE operation_id=?1 AND request_hash=?2 AND state='prepared' AND attempt_revision=?3",
            params![self.request.operation_id,self.fingerprint,self.revision,serde_json::to_string(binding).map_err(|_| invalid())?],
        )?;
        if changed != 1 {
            return Err(HostError::new("SYNC_BINDING_INTENT_CHANGED"));
        }
        Ok(())
    }
}

#[cfg(test)]
#[path = "mobile_sync_binding_tests.rs"]
mod tests;
