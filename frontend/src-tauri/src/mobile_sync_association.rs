//! Typed foreground association commands. Preview is read-only; confirmation
//! persists its original intent before HTTP and never starts a transfer cycle.
use crate::{
    mobile_sync_auth::{Auth, SecretStore},
    mobile_sync_binding::{self, Intent, Request as BindingRequest},
    mobile_sync_foreground::Lease,
    mobile_sync_session::{Scope, Selection},
    mobile_workspace::MobileWorkspace,
    sync_client::{identifier, SyncError},
    sync_initial::PreviewItem,
};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};

type Result<T> = std::result::Result<T, SyncError>;
type Engine = Arc<Mutex<Option<MobileWorkspace>>>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewRequest {
    pub expected: Selection,
    pub local_vault: String,
    pub remote_vault: String,
    pub offset: usize,
    pub limit: usize,
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Status {
        expected: Selection,
    },
    Preview(PreviewRequest),
    Bind {
        request: BindingRequest,
    },
    Resume {
        request: BindingRequest,
        observed_revision: i64,
    },
    Operation {
        expected: Selection,
        operation_id: String,
    },
    Pending {
        expected: Selection,
        after: Option<String>,
        limit: usize,
    },
}
#[derive(Serialize)]
pub struct PreviewPage {
    pub selection: Selection,
    pub local_vault: String,
    pub remote_vault: String,
    pub fingerprint: String,
    pub boundary: i64,
    pub total: usize,
    pub offset: usize,
    pub limit: usize,
    pub items: Vec<PreviewItem>,
}
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Response {
    Status { status: mobile_sync_binding::Status },
    Preview { page: PreviewPage },
    Operation { intent: Option<Intent> },
    Pending { page: mobile_sync_binding::Page },
}
fn selected<S: SecretStore>(auth: &Auth<S>, lease: &Lease, expected: &Selection) -> Result<Scope> {
    if auth.selection(lease)?.as_ref() != Some(expected) {
        return Err(SyncError::new("SYNC_AUTH_SELECTION_CHANGED"));
    }
    Ok(Scope::new(&expected.endpoint, &expected.account)?)
}

pub async fn execute<S: SecretStore>(
    auth: &mut Auth<S>,
    lease: &Lease,
    engine: Engine,
    request: Request,
    now: u64,
) -> Result<Response> {
    lease.check()?;
    match request {
        Request::Status { expected } => Ok(Response::Status {
            status: mobile_sync_binding::status(auth, lease, &expected, &engine).await?,
        }),
        Request::Operation {
            expected,
            operation_id,
        } => Ok(Response::Operation {
            intent: mobile_sync_binding::operation(auth, lease, &expected, &engine, &operation_id)
                .await?,
        }),
        Request::Pending {
            expected,
            after,
            limit,
        } => Ok(Response::Pending {
            page: mobile_sync_binding::pending(
                auth,
                lease,
                &expected,
                &engine,
                after.as_deref(),
                limit,
            )
            .await?,
        }),
        Request::Preview(request) => {
            if !(1..=100).contains(&request.limit)
                || request.offset as u64 > 9_007_199_254_740_991_u64
            {
                return Err(SyncError::new("SYNC_PREVIEW_PAGE_INVALID"));
            }
            identifier(&request.local_vault)?;
            identifier(&request.remote_vault)?;
            let scope = selected(auth, lease, &request.expected)?;
            let operation = request.expected.operation_id.clone();
            auth.authorized_device(
                lease,
                &scope,
                &operation,
                now,
                move |client, device| async move {
                    let port = device.workspace(engine);
                    let local_vault = request.local_vault.clone();
                    let port = tokio::task::spawn_blocking(move || {
                        port.check_initial_target(&local_vault)?;
                        Ok::<_, SyncError>(port)
                    })
                    .await
                    .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))??;
                    client.handshake().await?;
                    let snapshot = client.snapshot(&request.remote_vault).await?;
                    let preview = tokio::task::spawn_blocking(move || {
                        port.initial_preview(&request.local_vault, &request.remote_vault, &snapshot)
                            .map(|preview| (request, preview))
                    })
                    .await
                    .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))??;
                    let (request, preview) = preview;
                    let total = preview.items.len();
                    Ok(Response::Preview {
                        page: PreviewPage {
                            selection: request.expected,
                            local_vault: request.local_vault,
                            remote_vault: request.remote_vault,
                            fingerprint: preview.fingerprint,
                            boundary: preview.boundary,
                            total,
                            offset: request.offset,
                            limit: request.limit,
                            items: preview
                                .items
                                .into_iter()
                                .skip(request.offset)
                                .take(request.limit)
                                .collect(),
                        },
                    })
                },
            )
            .await
        }
        Request::Bind { request } => confirm(auth, lease, engine, request, None, now).await,
        Request::Resume {
            request,
            observed_revision,
        } => confirm(auth, lease, engine, request, Some(observed_revision), now).await,
    }
}
async fn confirm<S: SecretStore>(
    auth: &mut Auth<S>,
    lease: &Lease,
    engine: Engine,
    request: BindingRequest,
    resume: Option<i64>,
    now: u64,
) -> Result<Response> {
    let scope = selected(auth, lease, &request.selection)?;
    let operation = request.selection.operation_id.clone();
    auth.authorized_device(
        lease,
        &scope,
        &operation,
        now,
        move |client, device| async move {
            let port = device.workspace(engine);
            let original = request.clone();
            let (port, prepared) = tokio::task::spawn_blocking(move || {
                let prepared = match resume {
                    Some(revision) => port.resume_initial_binding(&original, revision)?,
                    None => port.begin_initial_binding(&original)?,
                };
                Ok::<_, SyncError>((port, prepared))
            })
            .await
            .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))??;
            let Some(ticket) = prepared.ticket else {
                // Replaying a confirmation only reads its original local result.
                return Ok(Response::Operation {
                    intent: Some(prepared.intent),
                });
            };
            client.handshake().await?;
            let snapshot = client.snapshot(&request.remote_vault).await?;
            let operation = request.operation_id.clone();
            let intent = tokio::task::spawn_blocking(move || {
                port.commit_initial_binding(ticket, &snapshot)?;
                port.initial_binding_operation(&operation)?
                    .ok_or_else(|| SyncError::new("SYNC_BINDING_INTENT_NOT_FOUND"))
            })
            .await
            .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))??;
            Ok(Response::Operation {
                intent: Some(intent),
            })
        },
    )
    .await
}
pub fn public_error(error: SyncError) -> String {
    match error.code.as_str() {
        "SYNC_ALREADY_BOUND"
        | "SYNC_PREVIEW_CHANGED"
        | "SYNC_PREVIEW_PAGE_INVALID"
        | "SYNC_BINDING_REQUEST_INVALID"
        | "SYNC_BINDING_PAGE_INVALID"
        | "SYNC_BINDING_INTENT_INVALID"
        | "SYNC_BINDING_INTENT_NOT_FOUND"
        | "SYNC_BINDING_INTENT_CHANGED"
        | "SYNC_OPERATION_MISMATCH"
        | "SYNC_WORKER_INTERRUPTED"
        | "VAULT_CHANGED"
        | "HOST_BUSY"
        | "MOBILE_STORAGE_UNAVAILABLE"
        | "QUOTA_EXCEEDED"
        | "FILESYSTEM_ERROR"
        | "DATABASE_ERROR" => error.code,
        _ => crate::mobile_sync_commands::public_error(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use zeroize::Zeroizing;
    struct NoSecrets;
    impl SecretStore for NoSecrets {
        fn read(&self, _: &Scope, _: &str) -> crate::workspace::Result<Option<Zeroizing<Vec<u8>>>> {
            panic!("Metadata or rejected request read secrets")
        }
        fn put(&self, _: &Scope, _: &str, _: Zeroizing<Vec<u8>>) -> crate::workspace::Result<()> {
            panic!("Unexpected secret write")
        }
        fn remove(&self, _: &Scope, _: &str) -> crate::workspace::Result<()> {
            panic!("Unexpected secret removal")
        }
    }
    fn selection() -> Selection {
        Selection {
            revision: 1,
            endpoint: "https://sync.example/".into(),
            account: "owned-a".into(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            generation: 1,
        }
    }
    #[test]
    fn sync_association_contract_rejects_general_http_secrets_and_unknown_fields() {
        let selection = serde_json::to_value(selection()).unwrap();
        for value in [
            serde_json::json!({"action":"http","url":"https://other.example"}),
            serde_json::json!({"action":"status","expected":selection,"access_token":"not accepted"}),
            serde_json::json!({"action":"preview","expected":selection,"local_vault":"local","remote_vault":"remote","offset":0,"limit":20,"snapshot":{}}),
        ] {
            assert!(serde_json::from_value::<Request>(value).is_err());
        }
        assert_eq!(
            public_error(SyncError::new("PRIVATE_PROVIDER_BODY")),
            "SYNC_CONNECTION_FAILED"
        );
    }
    #[tokio::test]
    async fn sync_association_stale_selected_identity_and_invalid_pages_refuse_before_secret_or_network_access(
    ) {
        let root = tempfile::tempdir().unwrap();
        let mut auth = Auth::open(root.path(), NoSecrets).unwrap();
        let foreground = crate::mobile_sync_foreground::Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        let engine = Arc::new(Mutex::new(None));
        let request = Request::Preview(PreviewRequest {
            expected: selection(),
            local_vault: "local".into(),
            remote_vault: "remote".into(),
            offset: 0,
            limit: 20,
        });
        assert_eq!(
            execute(&mut auth, &lease, engine.clone(), request, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        let request = Request::Preview(PreviewRequest {
            expected: selection(),
            local_vault: "local".into(),
            remote_vault: "remote".into(),
            offset: 0,
            limit: 101,
        });
        assert_eq!(
            execute(&mut auth, &lease, engine.clone(), request, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_PREVIEW_PAGE_INVALID"
        );
        assert!(engine.lock().unwrap().is_none());
    }
}
