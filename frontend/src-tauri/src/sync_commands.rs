//! Main-window commands. Every ongoing run is bound to one Workspace and one account.
use super::{with_workspace, Host};
use notesagent_host::{
    sync_auth,
    sync_client::{SyncError, WorkspaceAccess},
    sync_state::Binding,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::State;
use zeroize::Zeroizing;
#[derive(Default)]
pub struct Runtime {
    gate: tokio::sync::Mutex<()>,
    epoch: AtomicU64,
    status: Mutex<HashMap<String, Progress>>,
}
#[derive(Default)]
struct Progress {
    running: bool,
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
        // Verify account ownership before creating the durable binding.
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
        ))
    })?;
    let (optional_scope, vault_id, binding, paused, conflicts, pending, attempts, retry) = snapshot;
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
        json!({"optional_scope":optional_scope,"vault_id":vault_id,"binding":binding,"paused":paused,"pending":pending,"conflicts":conflicts,"credential_state":credential_state,"running":status.is_some_and(|s|s.running),"error":retry.error,"retry_in":retry.retry_at.map(|_| retry.remaining(now())),"failures":retry.failures,"halted":retry.halted,"attempts":attempts}),
    )
}
#[tauri::command]
pub fn sync_resolve(
    host: State<'_, Host>,
    binding_id: String,
    sequence: i64,
    choice: String,
    destination: String,
    expected: String,
) -> Result<(), String> {
    with_workspace(&host, |ws| {
        ws.sync_resolve(&binding_id, sequence, &choice, &destination, &expected)
    })
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
    host.sync
        .status
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .entry(binding.id.clone())
        .or_default()
        .running = true;
    let result = cycle(host, &binding).await;
    host.sync
        .status
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .entry(binding.id.clone())
        .or_default()
        .running = false;
    match result {
        Ok(()) => with_workspace(host, |ws| ws.sync_retry_clear(&binding.id)),
        Err(error) => {
            if !matches!(
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
                })?;
            }
            Err(error.code)
        }
    }
}
fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        .min(i64::MAX as u64) as i64
}

async fn cycle(host: &Host, binding: &Binding) -> Result<(), SyncError> {
    let epoch = host.sync.epoch.load(Ordering::SeqCst);
    let work = sync_auth::authenticated(
        &host.credentials,
        &binding.endpoint,
        &binding.account,
        |client| async move {
            client.handshake().await?;
            host.workspace.access(|ws| ws.sync_discover(&binding.id))?;
            for _ in 0..10 {
                if client.pull_page(&host.workspace, binding).await? == 0 {
                    break;
                }
            }
            // Finish the fixed incoming window before freezing any new remote base.
            if host
                .workspace
                .access(|ws| ws.sync_boundary(&binding.id))?
                .is_some()
            {
                return Ok(());
            }
            for _ in 0..20 {
                if !client.push_one(&host.workspace, binding).await? {
                    break;
                }
            }
            client.pull_page(&host.workspace, binding).await?;
            Ok(())
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
