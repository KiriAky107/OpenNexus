//! Device-local Sync sessions. The serialized record never crosses IPC.
use crate::{
    credentials::{CredentialBroker, CredentialId, Scope},
    sync_client::{Session, SyncClient, SyncError},
    workspace::hash,
};
use serde::{Deserialize, Serialize};
use std::{
    future::Future,
    sync::{atomic::Ordering, Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use zeroize::Zeroizing;
pub type Credentials = Arc<Mutex<Option<CredentialBroker>>>;
type Result<T> = std::result::Result<T, SyncError>;
#[derive(Serialize, Deserialize)]
struct SavedSession {
    endpoint: String,
    account: String,
    allow_test_http: bool,
    expires_at: u64,
    session: Session,
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn identity(endpoint: &str, account: &str) -> CredentialId {
    CredentialId {
        scope: Scope::Sync(hash(format!("{endpoint}\n{account}").as_bytes())),
        id: "session".into(),
    }
}
fn broker<T>(
    credentials: &Credentials,
    action: impl FnOnce(&mut CredentialBroker) -> std::result::Result<T, String>,
) -> Result<T> {
    let mut guard = credentials
        .lock()
        .map_err(|_| SyncError::new("HOST_BUSY"))?;
    action(
        guard
            .as_mut()
            .ok_or_else(|| SyncError::new("CREDENTIALS_UNAVAILABLE"))?,
    )
    .map_err(|e| SyncError::new(&e))
}
fn save(credentials: &Credentials, saved: &SavedSession) -> Result<()> {
    let bytes = Zeroizing::new(
        serde_json::to_vec(saved).map_err(|_| SyncError::new("SYNC_SESSION_INVALID"))?,
    );
    broker(credentials, |b| {
        b.put(&identity(&saved.endpoint, &saved.account), bytes)
    })
}
pub fn available(credentials: &Credentials, endpoint: &str, account: &str) -> Result<bool> {
    broker(credentials, |b| {
        b.resolve(
            &identity(endpoint, account).scope,
            &identity(endpoint, account),
        )
        .map(|v| v.is_some())
    })
}
/// Dropping a guarded HTTP future closes the in-flight operation on any lock epoch change.
pub async fn guarded<T>(
    credentials: &Credentials,
    future: impl Future<Output = Result<T>>,
) -> Result<T> {
    let (signal, epoch) = broker(credentials, |b| {
        if b.is_locked() {
            return Err("CREDENTIALS_LOCKED".into());
        }
        let signal = b.lock_signal();
        let epoch = signal.load(Ordering::SeqCst);
        Ok((signal, epoch))
    })?;
    tokio::pin!(future);
    let mut interval = tokio::time::interval(Duration::from_millis(50));
    loop {
        tokio::select! {
            biased;
            _=interval.tick()=>{if signal.load(Ordering::SeqCst)!=epoch {return Err(SyncError::new("CREDENTIALS_LOCKED"));}},
            result=&mut future=>{if signal.load(Ordering::SeqCst)!=epoch {return Err(SyncError::new("CREDENTIALS_LOCKED"));} return result;}
        }
    }
}
pub async fn login(
    credentials: &Credentials,
    endpoint: &str,
    account: &str,
    password: Zeroizing<String>,
    device: &str,
    allow_test_http: bool,
) -> Result<String> {
    if account.is_empty()
        || account.len() > 128
        || account.contains(['\n', '\r'])
        || password.len() > 1024
        || device.is_empty()
        || device.len() > 100
    {
        return Err(SyncError::new("SYNC_LOGIN_INVALID"));
    }
    guarded(credentials, async {
        let public = SyncClient::new(endpoint, Zeroizing::new(String::new()), allow_test_http)?;
        public.handshake().await?;
        let session = public.login(account, password, device).await?;
        let endpoint = public.endpoint().to_owned();
        save(
            credentials,
            &SavedSession {
                endpoint: endpoint.clone(),
                account: account.into(),
                allow_test_http,
                expires_at: now().saturating_add(session.expires_in),
                session,
            },
        )?;
        Ok(endpoint)
    })
    .await
}
/// The caller serializes refreshes with the coordinator gate.
pub async fn client(
    credentials: &Credentials,
    endpoint: &str,
    account: &str,
    force_refresh: bool,
) -> Result<SyncClient> {
    let id = identity(endpoint, account);
    let encoded = broker(credentials, |b| b.resolve(&id.scope, &id))?
        .ok_or_else(|| SyncError::new("SYNC_LOGIN_REQUIRED"))?;
    let mut saved: SavedSession =
        serde_json::from_slice(&encoded).map_err(|_| SyncError::new("SYNC_SESSION_INVALID"))?;
    if saved.endpoint != endpoint || saved.account != account {
        return Err(SyncError::new("SYNC_SESSION_INVALID"));
    }
    if force_refresh || saved.expires_at <= now().saturating_add(30) {
        let public = SyncClient::new(
            endpoint,
            Zeroizing::new(String::new()),
            saved.allow_test_http,
        )?;
        let refreshed = guarded(credentials, public.refresh(&saved.session.refresh_token)).await?;
        if refreshed.device_id != saved.session.device_id {
            return Err(SyncError::new("SYNC_SESSION_INVALID"));
        }
        saved.expires_at = now().saturating_add(refreshed.expires_in);
        saved.session = refreshed;
        save(credentials, &saved)?;
    }
    SyncClient::new(
        endpoint,
        Zeroizing::new(saved.session.access_token.clone()),
        saved.allow_test_http,
    )
}
pub async fn logout(credentials: &Credentials, endpoint: &str, account: &str) -> Result<()> {
    let client = client(credentials, endpoint, account, false).await?;
    // A failed server revocation is reported; the encrypted record remains available for retry.
    guarded(
        credentials,
        client.json(reqwest::Method::DELETE, "sync/v1/auth/sessions", None),
    )
    .await?;
    broker(credentials, |b| b.delete(&identity(endpoint, account)))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn lock_cancels_inflight_and_scopes_do_not_expose_tokens() {
        let root = tempfile::tempdir().unwrap();
        let mut b = CredentialBroker::new(root.path().join("credentials"));
        b.unlock(Zeroizing::new(b"test-password-12345".to_vec()))
            .unwrap();
        let credentials = Arc::new(Mutex::new(Some(b)));
        let saved = SavedSession {
            endpoint: "https://sync.example/".into(),
            account: "account".into(),
            allow_test_http: false,
            expires_at: now() + 900,
            session: Session {
                access_token: "private-access".into(),
                refresh_token: "private-refresh".into(),
                expires_in: 900,
                device_id: "device".into(),
            },
        };
        save(&credentials, &saved).unwrap();
        assert!(available(&credentials, &saved.endpoint, &saved.account).unwrap());
        assert!(!available(&credentials, "https://other.example/", &saved.account).unwrap());
        assert!(!available(&credentials, &saved.endpoint, "other").unwrap());
        let bytes = std::fs::read(root.path().join("credentials")).unwrap();
        assert!(!bytes.windows(14).any(|v| v == b"private-access"));
        let task_credentials = credentials.clone();
        let pending = tokio::spawn(async move {
            guarded(&task_credentials, async {
                tokio::time::sleep(Duration::from_secs(30)).await;
                Ok(())
            })
            .await
        });
        tokio::time::sleep(Duration::from_millis(100)).await;
        credentials.lock().unwrap().as_mut().unwrap().lock();
        let result = tokio::time::timeout(Duration::from_secs(1), pending)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(result.unwrap_err().code, "CREDENTIALS_LOCKED");
        assert_eq!(
            available(&credentials, &saved.endpoint, &saved.account)
                .unwrap_err()
                .code,
            "CREDENTIALS_LOCKED"
        );
    }
}
