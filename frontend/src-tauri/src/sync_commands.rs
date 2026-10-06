//! 主窗口命令。每次正在进行的运行都绑定到一个工作区和一个帐户。
use super::{with_workspace, Host};
use notesagent_host::{
    sync_auth,
    sync_client::{SyncError, WorkspaceAccess},
    sync_progress::{Activity, Event, Phase},
    sync_state::Binding,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{Manager, State, WebviewWindow};
use zeroize::Zeroizing;
#[derive(Default)]
pub struct Runtime {
    gate: tokio::sync::Mutex<()>,
    epoch: AtomicU64,
    serial: AtomicU64,
    status: Arc<Mutex<HashMap<String, Activity>>>,
}
impl Runtime {
    pub fn cancel(&self) {
        self.epoch.fetch_add(1, Ordering::SeqCst);
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Login {
    endpoint: String,
    account: String,
    password: String,
    device_name: String,
    allow_test_http: bool,
}
#[derive(Serialize)]
pub struct Connection {
    endpoint: String,
    account: String,
}
#[tauri::command]
pub async fn sync_login(host: State<'_, Host>, request: Login) -> Result<Connection, String> {
    let _guard = host.sync.gate.lock().await;
    let endpoint = sync_auth::login(
        &host.credentials,
        &request.endpoint,
        &request.account,
        Zeroizing::new(request.password),
        &request.device_name,
        request.allow_test_http,
    )
    .await
    .map_err(|e| e.code)?;
    with_workspace(&host, |ws| {
        if let Some(binding) = ws.sync_binding()? {
            if binding.endpoint == endpoint && binding.account == request.account {
                ws.sync_retry_clear(&binding.id)?;
            }
        }
        Ok(())
    })
    .or_else(|error| {
        if error == "VAULT_NOT_OPEN" {
            Ok(())
        } else {
            Err(error)
        }
    })?;
    host.sync.status.lock().map_err(|_| "HOST_BUSY")?.clear();
    Ok(Connection {
        endpoint,
        account: request.account,
    })
}
#[tauri::command]
pub async fn sync_vaults(
    host: State<'_, Host>,
    endpoint: String,
    account: String,
) -> Result<Value, String> {
    let _guard = host.sync.gate.lock().await;
    let client = sync_auth::client(&host.credentials, &endpoint, &account, false)
        .await
        .map_err(|e| e.code)?;
    sync_auth::guarded(
        &host.credentials,
        client.json(reqwest::Method::GET, "sync/v1/vaults", None),
    )
    .await
    .map_err(|e| e.code)
}
#[tauri::command]
pub async fn sync_create_vault(
    host: State<'_, Host>,
    endpoint: String,
    account: String,
    name: String,
) -> Result<Value, String> {
    if name.trim().is_empty() || name.len() > 100 {
        return Err("SYNC_NAME_INVALID".into());
    }
    let _guard = host.sync.gate.lock().await;
    let client = sync_auth::client(&host.credentials, &endpoint, &account, false)
        .await
        .map_err(|e| e.code)?;
    sync_auth::guarded(
        &host.credentials,
        client.json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":name})),
        ),
    )
    .await
    .map_err(|e| e.code)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bind {
    vault_id: String,
    endpoint: String,
    account: String,
    remote_vault: String,
    mode: String,
    #[serde(default)]
    fingerprint: Option<String>,
}
#[tauri::command]
pub async fn sync_preview(
    host: State<'_, Host>,
    request: Bind,
) -> Result<notesagent_host::sync_initial::Preview, String> {
    let _guard = host.sync.gate.lock().await;
    let client = sync_auth::client(
        &host.credentials,
        &request.endpoint,
        &request.account,
        false,
    )
    .await
    .map_err(|e| e.code)?;
    let snapshot = sync_auth::guarded(&host.credentials, client.snapshot(&request.remote_vault))
        .await
        .map_err(|e| e.code)?;
    with_workspace(&host, |ws| {
        if ws.vault_id != request.vault_id {
            return Err(notesagent_host::workspace::HostError::new("VAULT_CHANGED"));
        }
        ws.sync_preview(
            &request.endpoint,
            &request.remote_vault,
            &request.account,
            &snapshot,
        )
    })
}
#[tauri::command]
pub async fn sync_bind(host: State<'_, Host>, request: Bind) -> Result<Binding, String> {
    let _guard = host.sync.gate.lock().await;
    let client = sync_auth::client(
        &host.credentials,
        &request.endpoint,
        &request.account,
        false,
    )
    .await
    .map_err(|e| e.code)?;
    if request.mode == "merge" {
        let snapshot =
            sync_auth::guarded(&host.credentials, client.snapshot(&request.remote_vault))
                .await
                .map_err(|e| e.code)?;
        return with_workspace(&host, |ws| {
            if ws.vault_id != request.vault_id {
                return Err(notesagent_host::workspace::HostError::new("VAULT_CHANGED"));
            }
            ws.sync_bind_initial(
                &request.endpoint,
                &request.remote_vault,
                &request.account,
                &snapshot,
                request.fingerprint.as_deref().unwrap_or(""),
            )
        });
    }
    if request.mode == "upload" {
        sync_auth::guarded(
            &host.credentials,
            client.verify_empty(&request.remote_vault),
        )
        .await
        .map_err(|e| e.code)?;
    } else if request.mode == "download" {
        // 创建持久绑定前验证账号所有权。
        let vaults = sync_auth::guarded(
            &host.credentials,
            client.json(reqwest::Method::GET, "sync/v1/vaults", None),
        )
        .await
        .map_err(|e| e.code)?;
        if !vaults["items"]
            .as_array()
            .is_some_and(|items| items.iter().any(|v| v["id"] == request.remote_vault))
        {
            return Err("SYNC_VAULT_DENIED".into());
        }
    } else {
        return Err("SYNC_RECONCILIATION_REQUIRED".into());
    }
    with_workspace(&host, |ws| {
        if ws.vault_id != request.vault_id {
            return Err(notesagent_host::workspace::HostError::new("VAULT_CHANGED"));
        }
        if request.mode == "upload" {
            ws.sync_bind_empty(&request.endpoint, &request.remote_vault, &request.account)
        } else {
            ws.sync_bind_download(&request.endpoint, &request.remote_vault, &request.account)
        }
    })
}
#[tauri::command]
pub fn sync_unbind(host: State<'_, Host>, binding_id: String) -> Result<(), String> {
    host.sync.cancel();
    with_workspace(&host, |ws| ws.sync_unbind(&binding_id))
}
#[tauri::command]
pub fn sync_pause(host: State<'_, Host>, binding_id: String, paused: bool) -> Result<(), String> {
    host.sync.cancel();
    with_workspace(&host, |ws| {
        ws.sync_pause(&binding_id, paused)?;
        if !paused {
            ws.sync_retry_clear(&binding_id)?;
        }
        Ok(())
    })?;
    host.sync
        .status
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .remove(&binding_id);
    Ok(())
}
#[tauri::command]
pub fn sync_set_scope(
    host: State<'_, Host>,
    vault_id: String,
    scope: notesagent_host::sync_scope::OptionalScope,
) -> Result<(), String> {
    with_workspace(&host, |ws| {
        if ws.vault_id != vault_id {
            return Err(notesagent_host::workspace::HostError::new(
                "VAULT_PERMISSION_CHANGED",
            ));
        }
        ws.sync_set_optional_scope(scope)
    })
}
#[tauri::command]
pub fn sync_status(host: State<'_, Host>) -> Result<Value, String> {
    let snapshot = with_workspace(&host, |ws| {
        let binding = ws.sync_binding()?;
        let paused = binding
            .as_ref()
            .map(|b| ws.sync_paused(&b.id))
            .transpose()?
            .unwrap_or(false);
        let conflicts = binding
            .as_ref()
            .map(|b| ws.sync_conflicts(&b.id))
            .transpose()?
            .unwrap_or_default();
        Ok((
            ws.sync_optional_scope()?,
            ws.vault_id.clone(),
            binding.clone(),
            paused,
            conflicts,
            ws.pending_count()?,
            binding
                .as_ref()
                .map(|b| ws.sync_attempts(&b.id))
                .transpose()?
                .unwrap_or_default(),
            binding
                .as_ref()
                .map(|b| ws.sync_retry(&b.id))
                .transpose()?
                .unwrap_or_default(),
            binding
                .as_ref()
                .map(|b| ws.sync_last_cycle_success(&b.id))
                .transpose()?
                .flatten(),
        ))
    })?;
    let (
        optional_scope,
        vault_id,
        binding,
        paused,
        conflicts,
        pending,
        attempts,
        retry,
        last_cycle_success_at,
    ) = snapshot;
    let credential_state = if let Some(b) = &binding {
        sync_auth::available(&host.credentials, &b.endpoint, &b.account)
            .map(|exists| {
                if exists {
                    "ready"
                } else {
                    "SYNC_LOGIN_REQUIRED"
                }
                .to_owned()
            })
            .unwrap_or_else(|e| e.code)
    } else {
        "unbound".into()
    };
    let statuses = host.sync.status.lock().map_err(|_| "HOST_BUSY")?;
    let status = binding.as_ref().and_then(|b| statuses.get(&b.id));
    Ok(
        json!({"optional_scope":optional_scope,"vault_id":vault_id,"binding":binding,"paused":paused,"pending":pending,"conflicts":conflicts,"credential_state":credential_state,"running":status.is_some_and(|s|s.running),"activity":status,"last_cycle_success_at":last_cycle_success_at,"error":retry.error,"retry_in":retry.retry_at.map(|_| retry.remaining(now())),"failures":retry.failures,"halted":retry.halted,"attempts":attempts}),
    )
}
#[tauri::command]
pub async fn sync_resolve(
    window: WebviewWindow,
    app: tauri::AppHandle,
    request: ResolveConflict,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("SYNC_MAIN_WINDOW_REQUIRED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        with_workspace(&app.state::<Host>(), |ws| {
            if ws.vault_id != request.vault_id {
                return Err(notesagent_host::workspace::HostError::new(
                    "VAULT_PERMISSION_CHANGED",
                ));
            }
            ws.sync_resolve_reviewed(
                &request.binding_id,
                request.sequence,
                &request.choice,
                &request.destination,
                &request.expected,
                &request.fingerprint,
            )
        })
    })
    .await
    .map_err(|_| "HOST_BUSY".to_owned())?
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResolveConflict {
    vault_id: String,
    binding_id: String,
    sequence: i64,
    choice: String,
    destination: String,
    expected: String,
    fingerprint: String,
}
#[tauri::command]
pub async fn sync_conflict_review(
    window: WebviewWindow,
    app: tauri::AppHandle,
    vault_id: String,
    binding_id: String,
    sequence: i64,
) -> Result<notesagent_host::sync_review::ConflictReview, String> {
    if window.label() != "main" {
        return Err("SYNC_MAIN_WINDOW_REQUIRED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        with_workspace(&app.state::<Host>(), |ws| {
            if ws.vault_id != vault_id {
                return Err(notesagent_host::workspace::HostError::new(
                    "VAULT_PERMISSION_CHANGED",
                ));
            }
            ws.sync_conflict_review(&binding_id, sequence)
        })
    })
    .await
    .map_err(|_| "HOST_BUSY".to_owned())?
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AccountRequest {
    vault_id: String,
    binding_id: String,
}
fn account_binding(host: &Host, vault_id: &str, binding_id: &str) -> Result<Binding, String> {
    with_workspace(host, |ws| {
        if ws.vault_id != vault_id {
            return Err(notesagent_host::workspace::HostError::new(
                "VAULT_PERMISSION_CHANGED",
            ));
        }
        let binding = ws
            .sync_binding()?
            .ok_or_else(|| notesagent_host::workspace::HostError::new("SYNC_NOT_BOUND"))?;
        if binding.id != binding_id {
            return Err(notesagent_host::workspace::HostError::new(
                "SYNC_BINDING_CHANGED",
            ));
        }
        Ok(binding)
    })
}
fn account_error(code: &str) -> SyncError {
    SyncError {
        code: code.into(),
        status: 0,
        retry_after: None,
    }
}
#[tauri::command]
pub async fn sync_account_status(
    window: WebviewWindow,
    app: tauri::AppHandle,
    request: AccountRequest,
) -> Result<Value, String> {
    if window.label() != "main" {
        return Err("SYNC_MAIN_WINDOW_REQUIRED".into());
    }
    let host = app.state::<Host>();
    let _guard = host.sync.gate.try_lock().map_err(|_| "SYNC_BUSY")?;
    let binding = account_binding(&host, &request.vault_id, &request.binding_id)?;
    let remote_vault = &binding.remote_vault;
    let details = sync_auth::authenticated(
        &host.credentials,
        &binding.endpoint,
        &binding.account,
        |client| async move { client.account_details(remote_vault).await },
    )
    .await
    .map_err(|error| error.code)?;
    account_binding(&host, &request.vault_id, &request.binding_id)?;
    let current_device_id =
        sync_auth::device_id(&host.credentials, &binding.endpoint, &binding.account)
            .map_err(|error| error.code)?;
    Ok(
        json!({"vault_id":request.vault_id,"binding_id":request.binding_id,"current_device_id":current_device_id,"vault":details.vault,"devices":details.devices,"checked_at":now()}),
    )
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceRequest {
    vault_id: String,
    binding_id: String,
    device_id: String,
}
#[tauri::command]
pub async fn sync_revoke_device(
    window: WebviewWindow,
    app: tauri::AppHandle,
    request: DeviceRequest,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("SYNC_MAIN_WINDOW_REQUIRED".into());
    }
    let host = app.state::<Host>();
    let _guard = host.sync.gate.try_lock().map_err(|_| "SYNC_BUSY")?;
    let binding = account_binding(&host, &request.vault_id, &request.binding_id)?;
    let current = sync_auth::device_id(&host.credentials, &binding.endpoint, &binding.account)
        .map_err(|error| error.code)?;
    if current == request.device_id {
        return Err("SYNC_CURRENT_DEVICE".into());
    }
    let host_ref = &*host;
    let request_ref = &request;
    let remote_vault = &binding.remote_vault;
    sync_auth::authenticated(
        &host.credentials,
        &binding.endpoint,
        &binding.account,
        |client| async move {
            account_binding(host_ref, &request_ref.vault_id, &request_ref.binding_id)
                .map_err(|code| account_error(&code))?;
            let details = client.account_details(remote_vault).await?;
            if !details
                .devices
                .iter()
                .any(|device| device.id == request_ref.device_id && !device.revoked)
            {
                return Err(account_error("SYNC_DEVICE_NOT_FOUND"));
            }
            account_binding(host_ref, &request_ref.vault_id, &request_ref.binding_id)
                .map_err(|code| account_error(&code))?;
            client.revoke_device(&request_ref.device_id).await
        },
    )
    .await
    .map_err(|error| error.code)?;
    account_binding(&host, &request.vault_id, &request.binding_id).map(|_| ())
}
#[tauri::command]
pub async fn sync_logout(
    host: State<'_, Host>,
    endpoint: String,
    account: String,
) -> Result<(), String> {
    host.sync.cancel();
    let _guard = host.sync.gate.lock().await;
    sync_auth::logout(&host.credentials, &endpoint, &account)
        .await
        .map_err(|e| e.code)
}
#[tauri::command]
pub async fn sync_run(host: State<'_, Host>) -> Result<(), String> {
    run(&host, true).await
}
pub async fn run(host: &Host, manual: bool) -> Result<(), String> {
    let Ok(_guard) = host.sync.gate.try_lock() else {
        return if manual {
            Err("SYNC_BUSY".into())
        } else {
            Ok(())
        };
    };
    let binding = with_workspace(host, |ws| ws.sync_binding())?.ok_or("SYNC_NOT_BOUND")?;
    if with_workspace(host, |ws| ws.sync_paused(&binding.id))? {
        return Err("SYNC_PAUSED".into());
    }
    let retry = with_workspace(host, |ws| ws.sync_retry(&binding.id))?;
    if !manual && (retry.halted || retry.remaining(now()) > 0) {
        return Ok(());
    }
    let epoch = host.sync.epoch.load(Ordering::SeqCst);
    let generation = host
        .sync
        .serial
        .fetch_add(1, Ordering::SeqCst)
        .wrapping_add(1);
    {
        let mut statuses = host.sync.status.lock().map_err(|_| "HOST_BUSY")?;
        statuses.clear();
        statuses.insert(binding.id.clone(), Activity::start(generation, now()));
    }
    let result = cycle(host, &binding, epoch, generation).await;
    let outcome = match result {
        Ok(()) => with_workspace(host, |ws| {
            ws.sync_cycle_succeeded(&binding.id, now())?;
            ws.sync_retry_clear(&binding.id)
        }),
        Err(error) => {
            let persisted = if !matches!(
                error.code.as_str(),
                "SYNC_CANCELLED" | "SYNC_BINDING_CHANGED" | "VAULT_CHANGED"
            ) {
                with_workspace(host, |ws| {
                    ws.sync_retry_fail(
                        &binding.id,
                        &error.code,
                        error.status,
                        error.retry_after,
                        now(),
                    )
                })
            } else {
                Ok(())
            };
            Err(persisted.err().unwrap_or(error.code))
        }
    };
    if let Some(activity) = host
        .sync
        .status
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .get_mut(&binding.id)
    {
        activity.finish(
            generation,
            now(),
            outcome.as_ref().err().map(String::as_str),
        );
    }
    outcome
}
fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        .min(i64::MAX as u64) as i64
}

async fn cycle(
    host: &Host,
    binding: &Binding,
    epoch: u64,
    generation: u64,
) -> Result<(), SyncError> {
    let statuses = host.sync.status.clone();
    let binding_id = binding.id.clone();
    let report: Arc<dyn Fn(Event) + Send + Sync> = Arc::new(move |event| {
        if let Ok(mut statuses) = statuses.lock() {
            if let Some(activity) = statuses.get_mut(&binding_id) {
                activity.observe(generation, event);
            }
        }
    });
    let work = sync_auth::authenticated(
        &host.credentials,
        &binding.endpoint,
        &binding.account,
        |client| {
            let report = report.clone();
            async move {
                let client = client.with_progress(report.clone());
                client.handshake().await?;
                report(Event::Phase(Phase::Discover));
                host.workspace.access(|ws| ws.sync_discover(&binding.id))?;
                report(Event::Phase(Phase::Pull));
                for _ in 0..10 {
                    if client.pull_page(&host.workspace, binding).await? == 0 {
                        break;
                    }
                }
                // 在冻结任何新的远程基地之前完成固定的传入窗口。
                if host
                    .workspace
                    .access(|ws| ws.sync_boundary(&binding.id))?
                    .is_some()
                {
                    return Ok(());
                }
                report(Event::Phase(Phase::Push));
                for _ in 0..20 {
                    if !client.push_one(&host.workspace, binding).await? {
                        break;
                    }
                }
                report(Event::Phase(Phase::Pull));
                client.pull_page(&host.workspace, binding).await?;
                Ok(())
            }
        },
    );
    tokio::pin!(work);
    let mut tick = tokio::time::interval(Duration::from_millis(50));
    loop {
        tokio::select! {biased;
            _=tick.tick()=>{
                if host.sync.epoch.load(Ordering::SeqCst)!=epoch {return Err(SyncError{code:"SYNC_CANCELLED".into(),status:0,retry_after:None});}
                host.workspace.access(|ws|ws.sync_paused(&binding.id).map(|_|()))?;
            },
            result=&mut work=>return result,
        }
    }
}
