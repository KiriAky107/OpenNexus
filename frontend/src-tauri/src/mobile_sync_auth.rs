//! Native authentication over the existing Sync v1 transport and durable intents.
//! Only State is serializable for IPC; decrypted records and sessions stay native.
use crate::{
    mobile_sync_foreground::Lease,
    mobile_sync_session::{
        Choice, Intent, Journal, Phase, Profiles, Rejection, Scope, Selection, StoredIdentity,
    },
    sync_client::{identifier, Session, SyncClient, SyncError},
};
use serde::{Deserialize, Serialize};
use std::{future::Future, path::Path};
use zeroize::Zeroizing;

type Result<T> = std::result::Result<T, SyncError>;
const MAX_RECORD: usize = 64 * 1024;

/// Android implements this with its application-scoped Keystore/JNI broker.
/// No implementation may return these bytes through a WebView command.
pub trait SecretStore: Send + Sync {
    fn read(
        &self,
        scope: &Scope,
        device: &str,
    ) -> crate::workspace::Result<Option<Zeroizing<Vec<u8>>>>;
    fn put(
        &self,
        scope: &Scope,
        device: &str,
        value: Zeroizing<Vec<u8>>,
    ) -> crate::workspace::Result<()>;
    fn remove(&self, scope: &Scope, device: &str) -> crate::workspace::Result<()>;
}

pub struct Reply {
    pub session: Session,
    pub must_change_credentials: bool,
}
impl Reply {
    fn parse(value: serde_json::Value) -> Result<Self> {
        let must_change_credentials = value
            .get("must_change_credentials")
            .and_then(serde_json::Value::as_bool)
            .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        let session =
            serde_json::from_value(value).map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        Ok(Self {
            session,
            must_change_credentials,
        })
    }
}

pub trait Transport: Send + Sync {
    fn login(
        &self,
        scope: &Scope,
        password: Zeroizing<String>,
        device_name: &str,
    ) -> impl Future<Output = Result<Reply>> + Send;
    fn refresh(
        &self,
        scope: &Scope,
        session: &Session,
    ) -> impl Future<Output = Result<Reply>> + Send;
    fn logout(&self, scope: &Scope, session: &Session) -> impl Future<Output = Result<()>> + Send;
}

/// The same HTTPS validation, bounded requests and no-redirect policy as desktop.
/// Missing/invalid bootstrap state is refused rather than presented as ready.
pub struct HttpsTransport;
impl Transport for HttpsTransport {
    async fn login(
        &self,
        scope: &Scope,
        password: Zeroizing<String>,
        device_name: &str,
    ) -> Result<Reply> {
        let client = SyncClient::new(scope.endpoint(), Zeroizing::new(String::new()), false)?;
        client.handshake().await?;
        Reply::parse(client.json(reqwest::Method::POST,"sync/v1/auth/sessions",
            Some(serde_json::json!({"username":scope.account(),"password":password.as_str(),"device_name":device_name}))).await?)
    }
    async fn refresh(&self, scope: &Scope, session: &Session) -> Result<Reply> {
        let client = SyncClient::new(scope.endpoint(), Zeroizing::new(String::new()), false)?;
        Reply::parse(
            client
                .json(
                    reqwest::Method::POST,
                    "sync/v1/auth/refresh",
                    Some(serde_json::json!({"refresh_token":session.refresh_token})),
                )
                .await?,
        )
    }
    async fn logout(&self, scope: &Scope, session: &Session) -> Result<()> {
        SyncClient::new(
            scope.endpoint(),
            Zeroizing::new(session.access_token.clone()),
            false,
        )?
        .json(reqwest::Method::DELETE, "sync/v1/auth/sessions", None)
        .await
        .map(|_| ())
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema: u8,
    endpoint: String,
    account: String,
    operation_id: String,
    generation: i64,
    expires_at: u64,
    must_change_credentials: bool,
    session: Session,
}
impl Record {
    fn identity(&self) -> StoredIdentity {
        StoredIdentity {
            endpoint: self.endpoint.clone(),
            account: self.account.clone(),
            device_id: self.session.device_id.clone(),
            operation_id: self.operation_id.clone(),
            generation: self.generation,
        }
    }
    fn validate(&self, scope: &Scope, device: &str) -> Result<()> {
        let valid_token = |value: &str| {
            !value.is_empty() && value.len() <= 4096 && value.bytes().all(|b| b.is_ascii_graphic())
        };
        if self.schema != 1
            || self.endpoint != scope.endpoint()
            || self.account != scope.account()
            || self.session.device_id != device
            || self.generation <= 0
            || !uuid::Uuid::parse_str(&self.operation_id)
                .is_ok_and(|id| id.to_string() == self.operation_id)
            || !valid_token(&self.session.access_token)
            || !valid_token(&self.session.refresh_token)
            || !(1..=86400).contains(&self.session.expires_in)
            || self.expires_at < self.session.expires_in
            || self.expires_at > i64::MAX as u64
        {
            return Err(SyncError::new("SYNC_SESSION_INVALID"));
        }
        identifier(device)?;
        Ok(())
    }
}

#[derive(Serialize)]
pub struct State {
    pub intent: Option<Intent>,
    pub authenticated: bool,
    pub expired: bool,
    pub credential_change_required: bool,
    pub expires_at: Option<u64>,
    pub rejected: Option<Rejection>,
}
fn state(
    intent: Option<Intent>,
    record: Option<&Record>,
    rejected: Option<Rejection>,
    now: u64,
) -> State {
    let active = intent.as_ref().is_some_and(|i| i.phase == Phase::Active);
    let record = record.filter(|_| active);
    let expired = record.is_some_and(|r| r.expires_at <= now);
    State {
        authenticated: active && record.is_some() && !expired,
        expired,
        credential_change_required: record.is_some_and(|r| r.must_change_credentials),
        expires_at: record.map(|r| r.expires_at),
        rejected,
        intent,
    }
}

pub struct Login<'a> {
    pub operation_id: &'a str,
    pub scope: &'a Scope,
    pub device_name: &'a str,
    pub password: Zeroizing<String>,
}
pub struct Existing<'a> {
    pub operation_id: &'a str,
    pub previous_operation: &'a str,
    pub scope: &'a Scope,
}

/// The Host owns one async command gate; methods are exclusive and do not
/// retain a plaintext session between calls. Cancel identity before waiting
/// for that gate on logout or account switch.
pub struct Auth<S: SecretStore> {
    journal: Journal,
    store: S,
}

/// Created only after the selected native record and session have been checked.
/// It cannot be constructed from a WebView request or deserialized metadata.
pub struct AuthenticatedDevice {
    scope: Scope,
    device: String,
    lease: Lease,
}
impl AuthenticatedDevice {
    pub(crate) fn scope(&self) -> &Scope {
        &self.scope
    }
    pub(crate) fn device(&self) -> &str {
        &self.device
    }
    pub fn workspace(
        self,
        engine: std::sync::Arc<std::sync::Mutex<Option<crate::mobile_workspace::MobileWorkspace>>>,
    ) -> crate::mobile_sync_foreground::ScopedWorkspace {
        crate::mobile_sync_foreground::ScopedWorkspace::authenticated(
            engine,
            self.lease.clone(),
            self,
        )
    }
}
impl<S: SecretStore> Auth<S> {
    pub fn open(private_root: &Path, store: S) -> Result<Self> {
        Ok(Self {
            journal: Journal::open(private_root)?,
            store,
        })
    }
    pub fn profiles(&self, lease: &Lease, offset: usize, limit: usize) -> Result<Profiles> {
        lease.with_current(|| Ok(self.journal.profiles(offset, limit)?))
    }
    pub fn selection(&self, lease: &Lease) -> Result<Option<Selection>> {
        lease.with_current(|| Ok(self.journal.selection()?))
    }
    pub fn choice(&self, lease: &Lease, id: &str) -> Result<Option<Choice>> {
        lease.with_current(|| Ok(self.journal.choice(id)?))
    }
    /// The Host invalidates old leases before waiting for the command gate.
    /// Returning an old choice receipt never changes the current selection.
    pub fn choose_profile(
        &mut self,
        lease: &Lease,
        operation: &str,
        target: &str,
        generation: i64,
        expected_revision: Option<i64>,
    ) -> Result<Choice> {
        lease.with_current(|| {
            Ok(self
                .journal
                .choose(operation, target, generation, expected_revision)?)
        })
    }
    pub fn recover_selected(&mut self, lease: &Lease, now: u64) -> Result<State> {
        let selected = lease.with_current(|| Ok(self.journal.selected()?))?;
        match selected {
            Some(intent) => {
                self.recover(lease, &Scope::new(&intent.endpoint, &intent.account)?, now)
            }
            None => self.status(None, None, now),
        }
    }
    fn status(&self, intent: Option<Intent>, record: Option<&Record>, now: u64) -> Result<State> {
        let rejected = intent
            .as_ref()
            .map(|intent| self.journal.rejection(&intent.operation_id))
            .transpose()?
            .flatten();
        Ok(state(intent, record, rejected, now))
    }
    fn failed<T>(&mut self, lease: &Lease, id: &str, error: SyncError) -> Result<T> {
        lease.with_current(|| {
            self.journal
                .record_rejection(id, error.status, &error.code)?;
            Ok(())
        })?;
        Err(error)
    }
    fn load(&self, intent: &Intent) -> Result<Option<Record>> {
        let Some(device) = intent.device_id.as_deref() else {
            return Ok(None);
        };
        let scope = Scope::new(&intent.endpoint, &intent.account)?;
        let Some(bytes) = self.store.read(&scope, device)? else {
            return Ok(None);
        };
        if bytes.len() > MAX_RECORD {
            return Err(SyncError::new("SYNC_SESSION_INVALID"));
        }
        let record: Record =
            serde_json::from_slice(&bytes).map_err(|_| SyncError::new("SYNC_SESSION_INVALID"))?;
        record.validate(&scope, device)?;
        Ok(Some(record))
    }
    fn expected(&self, scope: &Scope, id: &str) -> Result<Intent> {
        let intent = self
            .journal
            .find(id)?
            .ok_or_else(|| SyncError::new("SYNC_AUTH_INTENT_NOT_FOUND"))?;
        if intent.endpoint != scope.endpoint() || intent.account != scope.account() {
            return Err(SyncError::new("SYNC_AUTH_IDENTITY_CHANGED"));
        }
        Ok(intent)
    }
    fn recover_expected(
        &mut self,
        lease: &Lease,
        scope: &Scope,
        id: &str,
        now: u64,
    ) -> Result<State> {
        let current = self.journal.current(scope)?;
        if current.as_ref().is_none_or(|i| i.operation_id != id) {
            return Err(SyncError::new("SYNC_AUTH_IDENTITY_CHANGED"));
        }
        self.recover(lease, scope, now)
    }
    /// Read the original operation metadata without sending, activating or
    /// replacing anything. Historical success is not a currently usable login.
    pub fn operation(&self, lease: &Lease, scope: &Scope, id: &str, now: u64) -> Result<State> {
        lease.with_current(|| self.status(Some(self.expected(scope, id)?), None, now))
    }
    pub fn recover(&mut self, lease: &Lease, scope: &Scope, now: u64) -> Result<State> {
        lease.with_current(|| {
            let Some(intent) = self.journal.current(scope)? else {
                return self.status(None, None, now);
            };
            self.journal.require_selected(&intent)?;
            let record = self.load(&intent)?;
            let identity = record.as_ref().map(Record::identity);
            let reconciled = self
                .journal
                .reconcile(&intent.operation_id, identity.as_ref())?;
            self.status(Some(reconciled), record.as_ref(), now)
        })
    }
    /// Borrow the exact active native identity for one guarded foreground task.
    /// Plaintext session bytes never become the task's argument or result.
    pub async fn authorized<T: Send, F, Fut>(
        &mut self,
        lease: &Lease,
        scope: &Scope,
        expected_operation: &str,
        now: u64,
        action: F,
    ) -> Result<T>
    where
        F: FnOnce(SyncClient) -> Fut + Send,
        Fut: Future<Output = Result<T>> + Send,
    {
        self.authorized_device(
            lease,
            scope,
            expected_operation,
            now,
            move |client, _device| action(client),
        )
        .await
    }
    pub async fn authorized_device<T: Send, F, Fut>(
        &mut self,
        lease: &Lease,
        scope: &Scope,
        expected_operation: &str,
        now: u64,
        action: F,
    ) -> Result<T>
    where
        F: FnOnce(SyncClient, AuthenticatedDevice) -> Fut + Send,
        Fut: Future<Output = Result<T>> + Send,
    {
        let (client, device) = lease.with_current(|| {
            let intent = self
                .journal
                .current(scope)?
                .filter(|intent| {
                    intent.operation_id == expected_operation && intent.phase == Phase::Active
                })
                .ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
            self.journal.require_selected(&intent)?;
            let record = self
                .load(&intent)?
                .ok_or_else(|| SyncError::new("SYNC_LOGIN_REQUIRED"))?;
            if !record.identity().matches_intent(&intent) {
                return Err(SyncError::new("SYNC_AUTH_IDENTITY_CHANGED"));
            }
            if record.must_change_credentials {
                return Err(SyncError::new("SYNC_CREDENTIAL_CHANGE_REQUIRED"));
            }
            if record.expires_at <= now {
                return Err(SyncError::new("SYNC_SESSION_EXPIRED"));
            }
            let client = SyncClient::new(
                scope.endpoint(),
                Zeroizing::new(record.session.access_token.clone()),
                false,
            )?;
            Ok((
                client,
                AuthenticatedDevice {
                    scope: scope.clone(),
                    device: record.session.device_id.clone(),
                    lease: lease.clone(),
                },
            ))
        })?;
        lease.guard(action(client, device)).await
    }
    fn accept(&mut self, lease: &Lease, id: &str, reply: Reply, now: u64) -> Result<State> {
        lease.with_current(|| {
            let intent = self
                .journal
                .find(id)?
                .ok_or_else(|| SyncError::new("SYNC_AUTH_INTENT_NOT_FOUND"))?;
            let scope = Scope::new(&intent.endpoint, &intent.account)?;
            let record = Record {
                schema: 1,
                endpoint: intent.endpoint,
                account: intent.account,
                operation_id: id.into(),
                generation: intent.generation,
                expires_at: now
                    .checked_add(reply.session.expires_in)
                    .ok_or_else(|| SyncError::new("SYNC_TIME_INVALID"))?,
                must_change_credentials: reply.must_change_credentials,
                session: reply.session,
            };
            record.validate(&scope, &record.session.device_id)?;
            self.journal
                .received_session(id, &record.session.device_id)?;
            let bytes = Zeroizing::new(
                serde_json::to_vec(&record).map_err(|_| SyncError::new("SYNC_SESSION_INVALID"))?,
            );
            if bytes.len() > MAX_RECORD {
                return Err(SyncError::new("SYNC_SESSION_INVALID"));
            }
            self.store
                .put(&scope, &record.session.device_id, bytes.clone())?;
            let readback = self
                .store
                .read(&scope, &record.session.device_id)?
                .ok_or_else(|| SyncError::new("SYNC_CREDENTIALS_UNAVAILABLE"))?;
            if *readback != *bytes {
                return Err(SyncError::new("SYNC_CREDENTIALS_UNAVAILABLE"));
            }
            let ready = self.journal.reconcile(id, Some(&record.identity()))?;
            if ready.phase != Phase::Active {
                return Err(SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
            }
            self.status(Some(ready), Some(&record), now)
        })
    }
    pub async fn login(
        &mut self,
        lease: &Lease,
        request: Login<'_>,
        transport: &impl Transport,
        now: u64,
    ) -> Result<State> {
        if request.password.len() > 1024 || !(12..=256).contains(&request.password.chars().count())
        {
            return Err(SyncError::new("SYNC_LOGIN_INVALID"));
        }
        let prepared = lease.with_current(|| {
            Ok(self.journal.prepare_login(
                request.operation_id,
                request.scope,
                request.device_name,
            )?)
        })?;
        if !prepared.first_send {
            return self.recover_expected(lease, request.scope, request.operation_id, now);
        }
        let reply = match lease
            .guard(transport.login(request.scope, request.password, request.device_name))
            .await
        {
            Ok(reply) => reply,
            Err(error) => return self.failed(lease, request.operation_id, error),
        };
        self.accept(lease, request.operation_id, reply, now)
    }
    pub async fn refresh(
        &mut self,
        lease: &Lease,
        request: Existing<'_>,
        transport: &impl Transport,
        now: u64,
    ) -> Result<State> {
        let previous = self.expected(request.scope, request.previous_operation)?;
        let prepared = lease.with_current(|| {
            Ok(self
                .journal
                .prepare_refresh(request.operation_id, request.previous_operation)?)
        })?;
        if !prepared.first_send {
            return self.recover_expected(lease, request.scope, request.operation_id, now);
        }
        let record = self
            .load(&previous)?
            .ok_or_else(|| SyncError::new("SYNC_LOGIN_REQUIRED"))?;
        if !record.identity().matches_intent(&previous) {
            return Err(SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
        }
        let reply = match lease
            .guard(transport.refresh(request.scope, &record.session))
            .await
        {
            Ok(reply) => reply,
            Err(error) => return self.failed(lease, request.operation_id, error),
        };
        self.accept(lease, request.operation_id, reply, now)
    }
    pub async fn logout(
        &mut self,
        lease: &Lease,
        request: Existing<'_>,
        transport: &impl Transport,
        now: u64,
    ) -> Result<State> {
        let previous = self.expected(request.scope, request.previous_operation)?;
        let prepared = lease.with_current(|| {
            Ok(self
                .journal
                .prepare_logout(request.operation_id, request.previous_operation)?)
        })?;
        if !prepared.first_send {
            if prepared.intent.phase == Phase::ErasePending
                && prepared.intent.server_logout_confirmed
            {
                return self.erase(lease, request.scope, request.operation_id, now);
            }
            return self.recover_expected(lease, request.scope, request.operation_id, now);
        }
        let record = self
            .load(&previous)?
            .ok_or_else(|| SyncError::new("SYNC_LOGIN_REQUIRED"))?;
        if !record.identity().matches_intent(&previous) {
            return Err(SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
        }
        if let Err(error) = lease
            .guard(transport.logout(request.scope, &record.session))
            .await
        {
            return self.failed(lease, request.operation_id, error);
        }
        lease.with_current(|| {
            self.journal.received_logout(request.operation_id)?;
            Ok(())
        })?;
        self.erase(lease, request.scope, request.operation_id, now)
    }
    pub fn local_disconnect(
        &mut self,
        lease: &Lease,
        request: Existing<'_>,
        now: u64,
    ) -> Result<State> {
        self.expected(request.scope, request.previous_operation)?;
        lease.with_current(|| {
            self.journal
                .prepare_local_disconnect(request.operation_id, request.previous_operation)?;
            Ok(())
        })?;
        self.erase(lease, request.scope, request.operation_id, now)
    }
    /// Explicitly retry only the local part of the exact selected original
    /// disconnect. No transport call, replacement intent or revocation claim.
    pub fn resume_erasure(
        &mut self,
        lease: &Lease,
        scope: &Scope,
        operation: &str,
        now: u64,
    ) -> Result<State> {
        lease.with_current(|| {
            let intent = self.expected(scope, operation)?;
            self.journal.require_selected(&intent)?;
            if !matches!(intent.phase, Phase::ErasePending | Phase::Disconnected)
                || !(intent.kind == "local_disconnect"
                    || intent.kind == "logout" && intent.server_logout_confirmed)
            {
                return Err(SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
            }
            Ok(())
        })?;
        self.erase(lease, scope, operation, now)
    }
    fn erase(&mut self, lease: &Lease, scope: &Scope, id: &str, now: u64) -> Result<State> {
        lease.with_current(|| {
            let current = self
                .journal
                .current(scope)?
                .ok_or_else(|| SyncError::new("SYNC_LOGIN_REQUIRED"))?;
            if current.operation_id != id
                || !matches!(current.phase, Phase::ErasePending | Phase::Disconnected)
            {
                return Err(SyncError::new("SYNC_AUTH_IDENTITY_CHANGED"));
            }
            // A repeated original disconnect may belong to another profile.
            // Refuse it before deleting credentials, not only during the final
            // journal reconciliation after the irreversible local removal.
            self.journal.require_selected(&current)?;
            if current.phase != Phase::Disconnected {
                let device = current
                    .device_id
                    .as_deref()
                    .ok_or_else(|| SyncError::new("SYNC_SESSION_INVALID"))?;
                self.store.remove(scope, device)?;
                if self.store.read(scope, device)?.is_some() {
                    return Err(SyncError::new("SYNC_CREDENTIALS_UNAVAILABLE"));
                }
            }
            let done = self.journal.reconcile(id, None)?;
            self.status(Some(done), None, now)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{mobile_sync_foreground::Foreground, workspace::HostError};
    use std::{
        collections::{BTreeMap, VecDeque},
        sync::{
            atomic::{AtomicBool, AtomicUsize, Ordering},
            Arc, Mutex,
        },
    };
    fn scope() -> Scope {
        Scope::new("https://sync.example", "owned-reader").unwrap()
    }
    fn id() -> String {
        uuid::Uuid::new_v4().to_string()
    }
    fn lease() -> (Foreground, Lease) {
        let foreground = Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        (foreground, lease)
    }
    fn reply(device: &str, token: &str) -> Reply {
        Reply {
            session: Session {
                access_token: format!("owned-access-{token}"),
                refresh_token: format!("owned-refresh-{token}"),
                expires_in: 900,
                device_id: device.into(),
            },
            must_change_credentials: false,
        }
    }
    fn login<'a>(id: &'a str, scope: &'a Scope) -> Login<'a> {
        Login {
            operation_id: id,
            scope,
            device_name: "Owned phone",
            password: Zeroizing::new("owned-password-never-persist".into()),
        }
    }
    fn existing<'a>(
        operation_id: &'a str,
        previous_operation: &'a str,
        scope: &'a Scope,
    ) -> Existing<'a> {
        Existing {
            operation_id,
            previous_operation,
            scope,
        }
    }
    type Records = BTreeMap<(String, String, String), Zeroizing<Vec<u8>>>;
    #[derive(Clone, Default)]
    struct MemoryStore {
        records: Arc<Mutex<Records>>,
        puts: Arc<AtomicUsize>,
        removes: Arc<AtomicUsize>,
        fail_after_put: Arc<AtomicBool>,
        fail_read: Arc<AtomicBool>,
        fail_remove: Arc<AtomicBool>,
    }
    impl MemoryStore {
        fn key(scope: &Scope, device: &str) -> (String, String, String) {
            (
                scope.endpoint().into(),
                scope.account().into(),
                device.into(),
            )
        }
    }
    impl SecretStore for MemoryStore {
        fn read(
            &self,
            scope: &Scope,
            device: &str,
        ) -> crate::workspace::Result<Option<Zeroizing<Vec<u8>>>> {
            if self.fail_read.swap(false, Ordering::SeqCst) {
                return Err(HostError::new("SYNC_CREDENTIALS_UNAVAILABLE"));
            }
            Ok(self
                .records
                .lock()
                .unwrap()
                .get(&Self::key(scope, device))
                .cloned())
        }
        fn put(
            &self,
            scope: &Scope,
            device: &str,
            value: Zeroizing<Vec<u8>>,
        ) -> crate::workspace::Result<()> {
            self.puts.fetch_add(1, Ordering::SeqCst);
            self.records
                .lock()
                .unwrap()
                .insert(Self::key(scope, device), value);
            if self.fail_after_put.swap(false, Ordering::SeqCst) {
                self.fail_read.store(true, Ordering::SeqCst);
            }
            Ok(())
        }
        fn remove(&self, scope: &Scope, device: &str) -> crate::workspace::Result<()> {
            self.removes.fetch_add(1, Ordering::SeqCst);
            if self.fail_remove.swap(false, Ordering::SeqCst) {
                return Err(HostError::new("SYNC_CREDENTIALS_UNAVAILABLE"));
            }
            self.records
                .lock()
                .unwrap()
                .remove(&Self::key(scope, device));
            Ok(())
        }
    }
    #[derive(Default)]
    struct Remote {
        replies: Mutex<VecDeque<Result<Reply>>>,
        logins: AtomicUsize,
        refreshes: AtomicUsize,
        logouts: AtomicUsize,
        fail_logout: AtomicBool,
        stop_on_login: Mutex<Option<Foreground>>,
    }
    impl Remote {
        fn next(&self) -> Result<Reply> {
            self.replies
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or_else(|| Err(SyncError::new("UNEXPECTED_SYNTHETIC_REQUEST")))
        }
    }
    impl Transport for Remote {
        async fn login(
            &self,
            _scope: &Scope,
            _password: Zeroizing<String>,
            _device_name: &str,
        ) -> Result<Reply> {
            self.logins.fetch_add(1, Ordering::SeqCst);
            if let Some(foreground) = self.stop_on_login.lock().unwrap().take() {
                foreground.on_stop();
            }
            self.next()
        }
        async fn refresh(&self, _scope: &Scope, _session: &Session) -> Result<Reply> {
            self.refreshes.fetch_add(1, Ordering::SeqCst);
            self.next()
        }
        async fn logout(&self, _scope: &Scope, _session: &Session) -> Result<()> {
            self.logouts.fetch_add(1, Ordering::SeqCst);
            if self.fail_logout.load(Ordering::SeqCst) {
                Err(SyncError::new("SYNC_NETWORK_ERROR"))
            } else {
                Ok(())
            }
        }
    }

    #[tokio::test]
    async fn cancelling_native_auth_view_does_not_decrypt_or_mutate_profile_receipts() {
        use crate::mobile_sync_commands::{execute, Request, Response};
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "original")));
        let (owner, active) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&active, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        let selected = auth.selection(&active).unwrap();
        let puts = store.puts.load(Ordering::SeqCst);
        let removes = store.removes.load(Ordering::SeqCst);
        store.fail_read.store(true, Ordering::SeqCst);
        assert!(Request::Cancel {}.invalidates_identity());
        // This is the ordering used by the native wrapper before its gate.
        owner.invalidate_identity();
        let cancelled = owner.lease().unwrap();
        assert!(active.check().is_err());
        assert!(matches!(
            execute(&mut auth, &cancelled, Request::Cancel {}, &remote, 101)
                .await
                .unwrap(),
            Response::Cancelled
        ));
        assert_eq!(auth.selection(&cancelled).unwrap(), selected);
        assert_eq!(store.puts.load(Ordering::SeqCst), puts);
        assert_eq!(store.removes.load(Ordering::SeqCst), removes);
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
        assert_eq!(
            auth.recover_selected(&cancelled, 102).err().unwrap().code,
            "SYNC_CREDENTIALS_UNAVAILABLE"
        );
        assert!(
            auth.recover_selected(&cancelled, 103)
                .unwrap()
                .authenticated
        );
    }

    #[tokio::test]
    async fn native_command_frozen_selection_refuses_stale_login_and_replayed_receipt_never_switches_back(
    ) {
        use crate::mobile_sync_commands::{execute, Request, Response};
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .extend([Ok(reply("device-a", "a")), Ok(reply("device-b", "b"))]);
        let (owner, first) = lease();
        let mut auth = Auth::open(root.path(), store).unwrap();
        let a_id = id();
        let b_id = id();
        let stale_id = id();
        let login_request =
            |operation: &str, account: &str, expected: Option<Selection>| -> Request {
                serde_json::from_value(
                    serde_json::json!({"action":"login","operation_id":operation,
                "endpoint":"https://sync.example","account":account,"device_name":"Owned phone",
                "password":"owned-original-password", "expected":expected}),
                )
                .unwrap()
            };
        let a = execute(
            &mut auth,
            &first,
            login_request(&a_id, "reader-a", None),
            &remote,
            100,
        )
        .await
        .unwrap();
        let Response::Current { state, .. } = a else {
            panic!("Expected current connection")
        };
        assert!(state.authenticated);
        let revision_a = auth.selection(&first).unwrap().unwrap();
        owner.invalidate_identity();
        let second = owner.lease().unwrap();
        execute(
            &mut auth,
            &second,
            login_request(&b_id, "reader-b", Some(revision_a.clone())),
            &remote,
            101,
        )
        .await
        .unwrap();
        let revision_b = auth.selection(&second).unwrap();
        let error = execute(
            &mut auth,
            &second,
            login_request(&stale_id, "reader-c", Some(revision_a)),
            &remote,
            102,
        )
        .await
        .err()
        .unwrap();
        assert_eq!(error.code, "SYNC_AUTH_SELECTION_CHANGED");
        assert!(auth.journal.find(&stale_id).unwrap().is_none());
        let replay = execute(
            &mut auth,
            &second,
            login_request(&a_id, "reader-a", None),
            &remote,
            103,
        )
        .await
        .unwrap();
        let Response::Operation { state } = replay else {
            panic!("Expected historical receipt")
        };
        assert!(!state.authenticated);
        assert_eq!(state.intent.unwrap().operation_id, a_id);
        assert_eq!(auth.selection(&second).unwrap(), revision_b);
        assert_eq!(remote.logins.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn native_command_lost_refresh_only_reads_original_receipt_without_replaying_rotation() {
        use crate::mobile_sync_commands::{execute, Request, Response};
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().extend([
            Ok(reply("device-a", "original")),
            Err(SyncError::new("SYNC_NETWORK_ERROR")),
        ]);
        let (_owner, active) = lease();
        let scope = scope();
        let login_id = id();
        let rotation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&active, login(&login_id, &scope), &remote, 100)
            .await
            .unwrap();
        let original = store.read(&scope, "device-a").unwrap().unwrap();
        let frozen = auth.selection(&active).unwrap().unwrap();
        let value = serde_json::json!({"action":"refresh","operation_id":rotation,"previous_operation":login_id,
            "endpoint":scope.endpoint(),"account":scope.account(),"expected":frozen});
        assert!(execute(
            &mut auth,
            &active,
            serde_json::from_value::<Request>(value.clone()).unwrap(),
            &remote,
            101
        )
        .await
        .is_err());
        let replay = execute(
            &mut auth,
            &active,
            serde_json::from_value(value).unwrap(),
            &remote,
            102,
        )
        .await
        .unwrap();
        let Response::Operation { state } = replay else {
            panic!("Expected original receipt")
        };
        assert!(!state.authenticated);
        assert!(!state.expired);
        assert_eq!(state.intent.unwrap().operation_id, rotation);
        assert_eq!(remote.refreshes.load(Ordering::SeqCst), 1);
        assert_eq!(*store.read(&scope, "device-a").unwrap().unwrap(), *original);
    }

    #[tokio::test]
    async fn native_command_local_erasure_retry_requires_current_frozen_profile_and_never_calls_logout(
    ) {
        use crate::mobile_sync_commands::{execute, Request, Response};
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "original")));
        let (owner, active) = lease();
        let scope = scope();
        let login_id = id();
        let disconnect = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&active, login(&login_id, &scope), &remote, 100)
            .await
            .unwrap();
        let original = store.read(&scope, "device-a").unwrap().unwrap();
        let frozen = auth.selection(&active).unwrap().unwrap();
        let command = serde_json::json!({"action":"disconnect","operation_id":disconnect,"previous_operation":login_id,
            "endpoint":scope.endpoint(),"account":scope.account(),"expected":frozen});
        store.fail_remove.store(true, Ordering::SeqCst);
        assert!(execute(
            &mut auth,
            &active,
            serde_json::from_value::<Request>(command.clone()).unwrap(),
            &remote,
            101
        )
        .await
        .is_err());
        let removals = store.removes.load(Ordering::SeqCst);
        let replay = execute(
            &mut auth,
            &active,
            serde_json::from_value(command).unwrap(),
            &remote,
            102,
        )
        .await
        .unwrap();
        assert!(matches!(replay, Response::Operation { .. }));
        assert_eq!(store.removes.load(Ordering::SeqCst), removals);
        assert_eq!(*store.read(&scope, "device-a").unwrap().unwrap(), *original);
        let selected = auth.selection(&active).unwrap().unwrap();
        let stale = serde_json::json!({"action":"resume_erasure","operation_id":disconnect,"endpoint":scope.endpoint(),
            "account":scope.account(),"expected":frozen});
        assert_eq!(
            execute(
                &mut auth,
                &active,
                serde_json::from_value::<Request>(stale).unwrap(),
                &remote,
                103
            )
            .await
            .err()
            .unwrap()
            .code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        owner.invalidate_identity();
        let current = owner.lease().unwrap();
        let resume = serde_json::json!({"action":"resume_erasure","operation_id":disconnect,"endpoint":scope.endpoint(),
            "account":scope.account(),"expected":selected});
        let result = execute(
            &mut auth,
            &current,
            serde_json::from_value(resume).unwrap(),
            &remote,
            104,
        )
        .await
        .unwrap();
        let Response::Current { state, .. } = result else {
            panic!("Expected current disconnect state")
        };
        assert_eq!(state.intent.unwrap().phase, Phase::Disconnected);
        assert!(!state.authenticated);
        assert!(store.read(&scope, "device-a").unwrap().is_none());
        assert_eq!(remote.logouts.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn queued_native_command_loses_its_lease_before_any_journal_or_transport_work() {
        use crate::mobile_sync_commands::{execute, Request};
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        let (owner, queued) = lease();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        let request = serde_json::from_value::<Request>(
            serde_json::json!({"action":"login","operation_id":operation,
            "endpoint":"https://sync.example","account":"reader","device_name":"Owned phone",
            "password":"owned-original-password","expected":null}),
        )
        .unwrap();
        owner.invalidate_identity();
        assert_eq!(
            execute(&mut auth, &queued, request, &remote, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
        assert!(auth.journal.find(&operation).unwrap().is_none());
        assert_eq!(remote.logins.load(Ordering::SeqCst), 0);
        assert_eq!(store.puts.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn retrying_old_erasure_cannot_remove_an_unselected_profile_before_reconciliation() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .extend([Ok(reply("device-a", "a")), Ok(reply("device-b", "b"))]);
        let (owner, first) = lease();
        let a = scope();
        let b = Scope::new("https://other.example", "reader").unwrap();
        let a_id = id();
        let b_id = id();
        let disconnect = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&first, login(&a_id, &a), &remote, 100)
            .await
            .unwrap();
        store.fail_remove.store(true, Ordering::SeqCst);
        assert!(auth
            .local_disconnect(&first, existing(&disconnect, &a_id, &a), 101)
            .is_err());
        assert_eq!(
            auth.journal.find(&disconnect).unwrap().unwrap().phase,
            Phase::ErasePending
        );
        owner.invalidate_identity();
        let second = owner.lease().unwrap();
        auth.login(&second, login(&b_id, &b), &remote, 102)
            .await
            .unwrap();
        let original_a = store.read(&a, "device-a").unwrap().unwrap();
        let original_b = store.read(&b, "device-b").unwrap().unwrap();
        let removals = store.removes.load(Ordering::SeqCst);
        assert_eq!(
            auth.local_disconnect(&second, existing(&disconnect, &a_id, &a), 103)
                .err()
                .unwrap()
                .code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        assert_eq!(store.removes.load(Ordering::SeqCst), removals);
        assert_eq!(*store.read(&a, "device-a").unwrap().unwrap(), *original_a);
        assert_eq!(*store.read(&b, "device-b").unwrap().unwrap(), *original_b);
        let revision = auth.selection(&second).unwrap().unwrap().revision;
        owner.invalidate_identity();
        let selected = owner.lease().unwrap();
        auth.choose_profile(&selected, &id(), &disconnect, 2, Some(revision))
            .unwrap();
        assert_eq!(
            auth.local_disconnect(&selected, existing(&disconnect, &a_id, &a), 104)
                .unwrap()
                .intent
                .unwrap()
                .phase,
            Phase::Disconnected
        );
        assert!(store.read(&a, "device-a").unwrap().is_none());
        assert_eq!(*store.read(&b, "device-b").unwrap().unwrap(), *original_b);
        assert_eq!(remote.logins.load(Ordering::SeqCst), 2);
        assert_eq!(remote.logouts.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn cold_start_before_association_recovers_native_selected_scope_without_another_login() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Err(SyncError::new("SYNC_NETWORK_ERROR")));
        let (_owner, first) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        assert!(auth
            .login(&first, login(&operation, &scope), &remote, 100)
            .await
            .is_err());
        drop(auth);
        let (_cold_owner, cold) = lease();
        let mut reopened = Auth::open(root.path(), store).unwrap();
        let recovered = reopened.recover_selected(&cold, 101).unwrap();
        assert!(!recovered.authenticated);
        let original = recovered.intent.unwrap();
        assert_eq!(original.operation_id, operation);
        assert_eq!(original.endpoint, scope.endpoint());
        assert_eq!(original.account, scope.account());
        assert_eq!(original.phase, Phase::Unknown);
        assert_eq!(reopened.profiles(&cold, 0, 20).unwrap().total, 1);
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn profile_reads_never_decrypt_and_nonselected_scope_is_rejected_before_secret_access() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .extend([Ok(reply("device-a", "a")), Ok(reply("device-b", "b"))]);
        let (owner, a_lease) = lease();
        let a = scope();
        let b = Scope::new("https://other.example", "reader").unwrap();
        let a_id = id();
        let b_id = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&a_lease, login(&a_id, &a), &remote, 100)
            .await
            .unwrap();
        owner.invalidate_identity();
        let b_lease = owner.lease().unwrap();
        auth.login(&b_lease, login(&b_id, &b), &remote, 101)
            .await
            .unwrap();
        store.fail_read.store(true, Ordering::SeqCst);
        assert_eq!(auth.profiles(&b_lease, 0, 20).unwrap().total, 2);
        auth.selection(&b_lease).unwrap();
        assert_eq!(
            auth.recover(&b_lease, &a, 102).err().unwrap().code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        assert_eq!(
            auth.authorized(&b_lease, &a, &a_id, 102, |_| async { Ok(()) })
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        assert_eq!(
            auth.recover_selected(&b_lease, 102).err().unwrap().code,
            "SYNC_CREDENTIALS_UNAVAILABLE"
        );
        assert_eq!(remote.logins.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn replayed_old_profile_choice_returns_original_receipt_without_reactivating_that_account(
    ) {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .extend([Ok(reply("device-a", "a")), Ok(reply("device-b", "b"))]);
        let (owner, lease_a) = lease();
        let a = scope();
        let b = Scope::new("https://other.example", "reader").unwrap();
        let a_id = id();
        let b_id = id();
        let mut auth = Auth::open(root.path(), store).unwrap();
        auth.login(&lease_a, login(&a_id, &a), &remote, 100)
            .await
            .unwrap();
        owner.invalidate_identity();
        let active = owner.lease().unwrap();
        auth.login(&active, login(&b_id, &b), &remote, 101)
            .await
            .unwrap();
        owner.invalidate_identity();
        let active = owner.lease().unwrap();
        let choose_a = id();
        let receipt = auth
            .choose_profile(&active, &choose_a, &a_id, 1, Some(2))
            .unwrap();
        assert_eq!(receipt.selection.revision, 3);
        assert_eq!(
            auth.recover_selected(&active, 102)
                .unwrap()
                .intent
                .unwrap()
                .operation_id,
            a_id
        );
        owner.invalidate_identity();
        let active = owner.lease().unwrap();
        auth.choose_profile(&active, &id(), &b_id, 1, Some(3))
            .unwrap();
        let old = auth
            .choose_profile(&active, &choose_a, &a_id, 1, Some(2))
            .unwrap();
        assert_eq!(old.selection.revision, 3);
        assert_eq!(
            auth.choice(&active, &choose_a).unwrap().unwrap().selection,
            receipt.selection
        );
        assert_eq!(
            auth.recover_selected(&active, 103)
                .unwrap()
                .intent
                .unwrap()
                .operation_id,
            b_id
        );
        assert_eq!(auth.selection(&active).unwrap().unwrap().revision, 4);
        assert_eq!(remote.logins.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn login_readback_and_reopen_keep_original_intent_and_never_serialize_or_repost_secrets()
    {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "first")));
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        let status = auth
            .login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        assert!(status.authenticated);
        assert_eq!(status.expires_at, Some(1000));
        assert_eq!(status.intent.unwrap().operation_id, operation);
        assert!(
            auth.login(&lease, login(&operation, &scope), &remote, 101)
                .await
                .unwrap()
                .authenticated
        );
        drop(auth);
        let mut reopened = Auth::open(root.path(), store.clone()).unwrap();
        let status = reopened.recover(&lease, &scope, 102).unwrap();
        let output = serde_json::to_string(&status).unwrap();
        for secret in [
            "access_token",
            "refresh_token",
            "owned-access-first",
            "owned-refresh-first",
            "owned-password-never-persist",
        ] {
            assert!(!output.contains(secret));
            for file in std::fs::read_dir(root.path()).unwrap() {
                let bytes = std::fs::read(file.unwrap().path()).unwrap();
                assert!(!bytes
                    .windows(secret.len())
                    .any(|part| part == secret.as_bytes()));
            }
        }
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
        assert_eq!(store.puts.load(Ordering::SeqCst), 1);
        assert!(
            !reopened
                .recover(&lease, &scope, 1000)
                .unwrap()
                .authenticated
        );
    }

    #[tokio::test]
    async fn encrypted_write_then_interrupted_readback_recovers_without_another_login() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        store.fail_after_put.store(true, Ordering::SeqCst);
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "first")));
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        assert_eq!(
            auth.login(&lease, login(&operation, &scope), &remote, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_CREDENTIALS_UNAVAILABLE"
        );
        assert_eq!(
            auth.journal.find(&operation).unwrap().unwrap().phase,
            Phase::StorePending
        );
        drop(auth);
        let mut reopened = Auth::open(root.path(), store.clone()).unwrap();
        let done = reopened.recover(&lease, &scope, 101).unwrap();
        assert!(done.authenticated);
        assert_eq!(done.intent.unwrap().operation_id, operation);
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
        assert_eq!(store.puts.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn lost_login_response_requires_explicit_new_intent_and_does_not_automatically_create_devices(
    ) {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().extend([
            Err(SyncError::new("SYNC_NETWORK_ERROR")),
            Ok(reply("device-b", "new")),
        ]);
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store).unwrap();
        assert!(auth
            .login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .is_err());
        assert_eq!(
            auth.recover(&lease, &scope, 101)
                .unwrap()
                .intent
                .unwrap()
                .phase,
            Phase::Unknown
        );
        assert!(
            !auth
                .login(&lease, login(&operation, &scope), &remote, 102)
                .await
                .unwrap()
                .authenticated
        );
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
        assert!(
            auth.login(&lease, login(&id(), &scope), &remote, 103)
                .await
                .unwrap()
                .authenticated
        );
        assert_eq!(remote.logins.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn lost_one_use_refresh_retains_original_bytes_but_cannot_activate_or_repost_old_tokens()
    {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().extend([
            Ok(reply("device-a", "old")),
            Err(SyncError::new("SYNC_NETWORK_ERROR")),
        ]);
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let rotation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        let original = store.read(&scope, "device-a").unwrap().unwrap();
        assert!(auth
            .refresh(
                &lease,
                existing(&rotation, &operation, &scope),
                &remote,
                1001
            )
            .await
            .is_err());
        assert_eq!(
            auth.recover(&lease, &scope, 1002)
                .unwrap()
                .intent
                .unwrap()
                .phase,
            Phase::Unknown
        );
        assert!(
            !auth
                .refresh(
                    &lease,
                    existing(&rotation, &operation, &scope),
                    &remote,
                    1003
                )
                .await
                .unwrap()
                .authenticated
        );
        assert_eq!(remote.refreshes.load(Ordering::SeqCst), 1);
        assert_eq!(*store.read(&scope, "device-a").unwrap().unwrap(), *original);
    }

    #[tokio::test]
    async fn interrupted_refresh_save_recovers_only_the_new_original_rotation_and_same_device() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().extend([
            Ok(reply("device-a", "old")),
            Ok(reply("device-a", "rotated")),
        ]);
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let rotation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        store.fail_after_put.store(true, Ordering::SeqCst);
        assert!(auth
            .refresh(
                &lease,
                existing(&rotation, &operation, &scope),
                &remote,
                900
            )
            .await
            .is_err());
        drop(auth);
        let mut recovered = Auth::open(root.path(), store.clone()).unwrap();
        let done = recovered.recover(&lease, &scope, 901).unwrap();
        assert!(done.authenticated);
        assert_eq!(done.intent.unwrap().operation_id, rotation);
        assert_eq!(done.expires_at, Some(1800));
        assert_eq!(remote.refreshes.load(Ordering::SeqCst), 1);
        assert_eq!(store.puts.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn confirmed_logout_and_erase_failure_keep_the_receipt_and_only_retry_local_erasure() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "old")));
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let logout = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        store.fail_remove.store(true, Ordering::SeqCst);
        assert!(auth
            .logout(&lease, existing(&logout, &operation, &scope), &remote, 200)
            .await
            .is_err());
        let pending = auth.journal.find(&logout).unwrap().unwrap();
        assert!(pending.server_logout_confirmed);
        assert_eq!(pending.phase, Phase::ErasePending);
        let done = auth
            .logout(&lease, existing(&logout, &operation, &scope), &remote, 201)
            .await
            .unwrap()
            .intent
            .unwrap();
        assert!(done.server_logout_confirmed);
        assert_eq!(done.phase, Phase::Disconnected);
        auth.logout(&lease, existing(&logout, &operation, &scope), &remote, 202)
            .await
            .unwrap();
        assert_eq!(remote.logouts.load(Ordering::SeqCst), 1);
        assert_eq!(store.removes.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn lost_logout_response_and_local_disconnect_never_claim_server_revocation() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "old")));
        remote.fail_logout.store(true, Ordering::SeqCst);
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let logout = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        assert!(auth
            .logout(&lease, existing(&logout, &operation, &scope), &remote, 200)
            .await
            .is_err());
        let unknown = auth.recover(&lease, &scope, 201).unwrap().intent.unwrap();
        assert_eq!(unknown.phase, Phase::Unknown);
        assert!(!unknown.server_logout_confirmed);
        let done = auth
            .local_disconnect(&lease, existing(&id(), &logout, &scope), 202)
            .unwrap()
            .intent
            .unwrap();
        assert!(!done.server_logout_confirmed);
        assert_eq!(done.phase, Phase::Disconnected);
        assert!(store.read(&scope, "device-a").unwrap().is_none());
        assert_eq!(remote.logouts.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn background_response_cannot_save_or_activate_a_session_and_stale_login_never_sends() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "late")));
        let (foreground, lease) = lease();
        *remote.stop_on_login.lock().unwrap() = Some(foreground.clone());
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        assert_eq!(
            auth.login(&lease, login(&operation, &scope), &remote, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
        assert_eq!(store.puts.load(Ordering::SeqCst), 0);
        foreground.on_start();
        let current = foreground.lease().unwrap();
        assert!(!auth.recover(&current, &scope, 101).unwrap().authenticated);
        let late = id();
        assert!(auth
            .login(&lease, login(&late, &scope), &remote, 102)
            .await
            .is_err());
        assert!(auth.journal.find(&late).unwrap().is_none());
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn malformed_or_other_identity_payload_is_retained_and_never_used_as_a_fallback() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "old")));
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        let key = MemoryStore::key(&scope, "device-a");
        let mut record: serde_json::Value =
            serde_json::from_slice(&store.read(&scope, "device-a").unwrap().unwrap()).unwrap();
        record["account"] = serde_json::json!("other-owner");
        let bad = Zeroizing::new(serde_json::to_vec(&record).unwrap());
        store.records.lock().unwrap().insert(key, bad.clone());
        assert_eq!(
            auth.recover(&lease, &scope, 101).err().unwrap().code,
            "SYNC_SESSION_INVALID"
        );
        assert_eq!(*store.read(&scope, "device-a").unwrap().unwrap(), *bad);
        assert_eq!(store.removes.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn bootstrap_requirement_is_explicit_and_missing_flag_or_changed_refresh_device_is_refused(
    ) {
        for value in [
            serde_json::json!({}),
            serde_json::json!({"must_change_credentials":"false"}),
        ] {
            assert!(Reply::parse(value).is_err());
        }
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        let mut bootstrap = reply("device-a", "bootstrap");
        bootstrap.must_change_credentials = true;
        remote
            .replies
            .lock()
            .unwrap()
            .extend([Ok(bootstrap), Ok(reply("different-device", "bad"))]);
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let rotation = id();
        let mut auth = Auth::open(root.path(), store).unwrap();
        let state = auth
            .login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        assert!(state.credential_change_required);
        assert_eq!(
            auth.refresh(
                &lease,
                existing(&rotation, &operation, &scope),
                &remote,
                200
            )
            .await
            .err()
            .unwrap()
            .code,
            "SYNC_AUTH_RESPONSE_MISMATCH"
        );
        assert!(!auth.recover(&lease, &scope, 201).unwrap().authenticated);
    }

    #[tokio::test]
    async fn authorized_native_work_refuses_bootstrap_expiry_wrong_operation_and_late_identity() {
        let value = serde_json::json!({"access_token":"owned-wire-access","refresh_token":"owned-wire-refresh",
            "expires_in":900,"device_id":"device-a","must_change_credentials":true});
        assert!(Reply::parse(value).unwrap().must_change_credentials);
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        let mut bootstrap = reply("device-a", "bootstrap");
        bootstrap.must_change_credentials = true;
        remote
            .replies
            .lock()
            .unwrap()
            .extend([Ok(bootstrap), Ok(reply("device-a", "normal"))]);
        let (foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let rotation = id();
        let mut auth = Auth::open(root.path(), store).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        let calls = AtomicUsize::new(0);
        assert_eq!(
            auth.authorized(&lease, &scope, &operation, 101, |_| async {
                calls.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
            .await
            .err()
            .unwrap()
            .code,
            "SYNC_CREDENTIAL_CHANGE_REQUIRED"
        );
        auth.refresh(
            &lease,
            existing(&rotation, &operation, &scope),
            &remote,
            200,
        )
        .await
        .unwrap();
        assert!(auth
            .authorized(&lease, &scope, &operation, 201, |_| async {
                calls.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
            .await
            .is_err());
        assert_eq!(
            auth.authorized(&lease, &scope, &rotation, 1100, |_| async {
                calls.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
            .await
            .err()
            .unwrap()
            .code,
            "SYNC_SESSION_EXPIRED"
        );
        assert_eq!(calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            auth.authorized(&lease, &scope, &rotation, 201, |client| async move {
                Ok(client.endpoint().to_owned())
            })
            .await
            .unwrap(),
            scope.endpoint()
        );
        let late = auth
            .authorized(&lease, &scope, &rotation, 202, |_| async {
                foreground.invalidate_identity();
                Ok(())
            })
            .await;
        assert_eq!(late.err().unwrap().code, "SYNC_FOREGROUND_CHANGED");
    }

    #[tokio::test]
    async fn native_initial_preview_uses_the_original_device_scope_and_refuses_other_vaults_or_stale_leases(
    ) {
        use crate::sync_client::WorkspaceAccess;
        let root = tempfile::tempdir().unwrap();
        let remote = Remote::default();
        remote
            .replies
            .lock()
            .unwrap()
            .push_back(Ok(reply("device-a", "first")));
        let (foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), MemoryStore::default()).unwrap();
        auth.login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .unwrap();
        let engine = Arc::new(Mutex::new(Some(
            crate::mobile_workspace::MobileWorkspace::open(root.path()).unwrap(),
        )));
        let snapshot = crate::sync_initial::Snapshot {
            boundary: 0,
            items: vec![],
        };
        let (vault, fingerprint) = engine
            .access(|ws| {
                ws.write("original.md", "", b"original\r\n", "local")?;
                Ok((
                    ws.vault_id.clone(),
                    ws.sync_preview(scope.endpoint(), "remote", scope.account(), &snapshot)?
                        .fingerprint,
                ))
            })
            .unwrap();
        let owned = engine.clone();
        let port = auth
            .authorized_device(
                &lease,
                &scope,
                &operation,
                101,
                move |_, device| async move { Ok(device.workspace(owned)) },
            )
            .await
            .unwrap();
        assert_eq!(
            port.initial_preview(&vault, "remote", &snapshot)
                .unwrap()
                .fingerprint,
            fingerprint
        );
        assert_eq!(
            port.initial_preview("different-vault", "remote", &snapshot)
                .err()
                .unwrap()
                .code,
            "VAULT_CHANGED"
        );
        assert_eq!(
            lease
                .workspace(engine.clone())
                .initial_preview(&vault, "remote", &snapshot)
                .err()
                .unwrap()
                .code,
            "SYNC_AUTH_RECONCILIATION_REQUIRED"
        );
        foreground.on_stop();
        assert_eq!(
            port.initial_preview(&vault, "remote", &snapshot)
                .err()
                .unwrap()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
        engine
            .access(|ws| {
                assert!(ws.sync_binding()?.is_none());
                assert_eq!(ws.read("original.md")?.content.as_bytes(), b"original\r\n");
                Ok(())
            })
            .unwrap();
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn authenticated_device_port_is_bound_to_its_original_native_lease_and_issuing_device() {
        use crate::sync_client::WorkspaceAccess;
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().extend([
            Ok(reply("device-a", "first")),
            Ok(reply("device-b", "second")),
        ]);
        let (foreground, lease) = lease();
        let scope = scope();
        let first = id();
        let second = id();
        let mut auth = Auth::open(root.path(), store).unwrap();
        auth.login(&lease, login(&first, &scope), &remote, 100)
            .await
            .unwrap();
        let engine = Arc::new(Mutex::new(Some(
            crate::mobile_workspace::MobileWorkspace::open(root.path()).unwrap(),
        )));
        let (binding, job) = engine
            .access(|ws| {
                ws.write("original.md", "", b"retained-original", "local")?;
                let binding =
                    ws.sync_bind_empty(scope.endpoint(), "owned-vault", scope.account())?;
                let job = ws.sync_next(&binding.id)?.unwrap();
                Ok((binding, job))
            })
            .unwrap();
        let first_engine = engine.clone();
        let original_port = auth
            .authorized_device(&lease, &scope, &first, 101, move |_, device| async move {
                Ok(device.workspace(first_engine))
            })
            .await
            .unwrap();
        original_port.start_attempt(&job).unwrap();
        foreground.invalidate_identity();
        let current = foreground.lease().unwrap();
        let disconnect = id();
        auth.local_disconnect(&current, existing(&disconnect, &first, &scope), 102)
            .unwrap();
        auth.login(&current, login(&second, &scope), &remote, 103)
            .await
            .unwrap();
        assert_eq!(
            original_port.start_attempt(&job).unwrap_err().code,
            "SYNC_FOREGROUND_CHANGED"
        );
        let next_engine = engine.clone();
        let next = auth
            .authorized_device(
                &current,
                &scope,
                &second,
                104,
                move |_, device| async move { Ok(device.workspace(next_engine)) },
            )
            .await
            .unwrap();
        assert_eq!(
            next.start_attempt(&job).unwrap_err().code,
            "SYNC_OPERATION_DEVICE_CHANGED"
        );
        assert_eq!(
            engine.access(|ws| ws.sync_attempts(&binding.id)).unwrap()[0]["attempts"],
            1
        );
        assert_eq!(
            engine
                .access(|ws| ws.sync_next(&binding.id))
                .unwrap()
                .unwrap()
                .operation_id,
            job.operation_id
        );
        assert_eq!(
            engine.access(|ws| ws.read("original.md")).unwrap().content,
            "retained-original"
        );
    }

    #[tokio::test]
    async fn explicit_auth_rejection_survives_reopen_and_original_operation_query_without_reposting(
    ) {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().push_back(Err(SyncError {
            status: 401,
            code: "LOGIN_FAILED".into(),
            retry_after: None,
        }));
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store.clone()).unwrap();
        assert_eq!(
            auth.login(&lease, login(&operation, &scope), &remote, 100)
                .await
                .err()
                .unwrap()
                .code,
            "LOGIN_FAILED"
        );
        drop(auth);
        let mut reopened = Auth::open(root.path(), store.clone()).unwrap();
        let status = reopened.recover(&lease, &scope, 101).unwrap();
        assert!(!status.authenticated);
        let rejection = status.rejected.unwrap();
        assert_eq!(rejection.code, "LOGIN_FAILED");
        assert_eq!(rejection.status, 401);
        let receipt = reopened.operation(&lease, &scope, &operation, 102).unwrap();
        assert!(!receipt.authenticated);
        assert_eq!(receipt.rejected.unwrap().code, "LOGIN_FAILED");
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
        assert!(reopened
            .operation(
                &lease,
                &Scope::new("https://other.example", scope.account()).unwrap(),
                &operation,
                102
            )
            .is_err());
        assert_eq!(store.puts.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn proxy_or_server_5xx_is_uncertain_even_if_it_contains_a_known_auth_error_code() {
        let root = tempfile::tempdir().unwrap();
        let store = MemoryStore::default();
        let remote = Remote::default();
        remote.replies.lock().unwrap().push_back(Err(SyncError {
            status: 503,
            code: "LOGIN_FAILED".into(),
            retry_after: None,
        }));
        let (_foreground, lease) = lease();
        let scope = scope();
        let operation = id();
        let mut auth = Auth::open(root.path(), store).unwrap();
        assert!(auth
            .login(&lease, login(&operation, &scope), &remote, 100)
            .await
            .is_err());
        let status = auth.recover(&lease, &scope, 101).unwrap();
        assert!(status.rejected.is_none());
        assert_eq!(status.intent.unwrap().phase, Phase::Unknown);
        assert!(
            !auth
                .login(&lease, login(&operation, &scope), &remote, 102)
                .await
                .unwrap()
                .authenticated
        );
        assert_eq!(remote.logins.load(Ordering::SeqCst), 1);
    }
}
