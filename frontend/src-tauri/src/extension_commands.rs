//! 只有本地主窗口可以检查 Host 信任或准备的安装。
use super::Host;
use notesagent_host::extension_store::{ExtensionStore, InstallRequest, TrustSetting};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    path::PathBuf,
    sync::Arc,
    sync::Mutex,
    time::{Duration, Instant},
};
use tauri::{State, WebviewWindow};

pub struct Review {
    setting: TrustSetting,
    expected: Option<String>,
    fingerprint: String,
    expires: Instant,
}
#[derive(Default)]
pub struct Reviews(pub Mutex<HashMap<String, Review>>);
fn main_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("EXTENSION_WINDOW_DENIED".into());
    }
    Ok(())
}
fn store<T>(
    host: &Host,
    f: impl FnOnce(&mut ExtensionStore) -> notesagent_host::workspace::Result<T>,
) -> Result<T, String> {
    let mut guard = host.extensions.lock().map_err(|_| "HOST_BUSY")?;
    f(guard.as_mut().ok_or("EXTENSIONS_NOT_READY")?).map_err(|e| e.code)
}
#[tauri::command]
pub fn extension_trust_review(
    window: WebviewWindow,
    host: State<'_, Host>,
    setting: TrustSetting,
) -> Result<Value, String> {
    main_window(&window)?;
    trust_review(&host, setting)
}
fn trust_review(host: &Host, setting: TrustSetting) -> Result<Value, String> {
    let fingerprint = setting.fingerprint().map_err(|e| e.code)?;
    let previous = store(host, |s| {
        s.trust_setting(&setting.source, &setting.namespace, &setting.key_id)
    })?;
    let expected = previous
        .as_ref()
        .map(TrustSetting::fingerprint)
        .transpose()
        .map_err(|e| e.code)?;
    let mut reviews = host.extension_reviews.0.lock().map_err(|_| "HOST_BUSY")?;
    reviews.retain(|_, r| r.expires > Instant::now());
    if reviews.len() >= 64 {
        return Err("EXTENSION_REVIEW_LIMIT".into());
    }
    let id = uuid::Uuid::new_v4().to_string();
    reviews.insert(
        id.clone(),
        Review {
            setting: setting.clone(),
            expected,
            fingerprint: fingerprint.clone(),
            expires: Instant::now() + Duration::from_secs(120),
        },
    );
    Ok(json!({"review_id":id,"fingerprint":fingerprint,"previous":previous,"proposed":setting}))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Confirmation {
    review_id: String,
    fingerprint: String,
}
#[tauri::command]
pub fn extension_trust_confirm(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: Confirmation,
) -> Result<String, String> {
    main_window(&window)?;
    trust_confirm(&host, request)
}
fn trust_confirm(host: &Host, request: Confirmation) -> Result<String, String> {
    let mut reviews = host.extension_reviews.0.lock().map_err(|_| "HOST_BUSY")?;
    let review = reviews
        .get(&request.review_id)
        .ok_or("EXTENSION_REVIEW_EXPIRED")?;
    if review.expires <= Instant::now() {
        reviews.remove(&request.review_id);
        return Err("EXTENSION_REVIEW_EXPIRED".into());
    }
    if review.fingerprint != request.fingerprint {
        return Err("EXTENSION_TRUST_CONFIRMATION".into());
    }
    let revision = store(host, |s| {
        s.confirm_trust(
            &review.setting,
            review.expected.as_deref(),
            &review.fingerprint,
        )
    })?;
    reviews.remove(&request.review_id);
    Ok(revision)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Preview {
    root_key: String,
    vault_id: String,
    configurations: std::collections::BTreeMap<String, Value>,
}
#[tauri::command]
pub async fn extension_install_preview(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: Preview,
) -> Result<Value, String> {
    main_window(&window)?;
    let workspace = host.workspace.clone();
    let extensions = host.extensions.clone();
    tauri::async_runtime::spawn_blocking(move || {
        // 在预览完成前保持工作区绑定不变。
        let workspace = workspace.lock().map_err(|_| "HOST_BUSY")?;
        if workspace.as_ref().ok_or("VAULT_NOT_OPEN")?.vault_id != request.vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let mut store = extensions.lock().map_err(|_| "HOST_BUSY")?;
        let request = InstallRequest {
            root_key: request.root_key,
            vault_id: request.vault_id,
            app_version: env!("CARGO_PKG_VERSION").into(),
            platform: std::env::consts::OS.into(),
            architecture: std::env::consts::ARCH.into(),
            configurations: request.configurations,
        };
        let preview = store
            .as_mut()
            .ok_or("EXTENSIONS_NOT_READY")?
            .installation_preview(&request)
            .map_err(|e| e.code)?;
        serde_json::to_value(preview).map_err(|_| "EXTENSION_PREVIEW_INVALID".into())
    })
    .await
    .map_err(|_| "EXTENSION_PREVIEW_FAILED".to_string())?
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallConfirmation {
    request_id: String,
    operation_id: String,
    fingerprint: String,
    root_key: String,
    vault_id: String,
    configurations: std::collections::BTreeMap<String, Value>,
}

#[tauri::command]
pub async fn extension_install_confirm(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: InstallConfirmation,
) -> Result<Value, String> {
    main_window(&window)?;
    let mut lease = host.extension_requests.claim(&request.request_id)?;
    let checkpoint = lease.checkpoint();
    let workspace = host.workspace.clone();
    let extensions = host.extensions.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let workspace = workspace.lock().map_err(|_| "HOST_BUSY")?;
        if workspace.as_ref().ok_or("VAULT_NOT_OPEN")?.vault_id != request.vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let install = InstallRequest {
            root_key: request.root_key,
            vault_id: request.vault_id,
            app_version: env!("CARGO_PKG_VERSION").into(),
            platform: std::env::consts::OS.into(),
            architecture: std::env::consts::ARCH.into(),
            configurations: request.configurations,
        };
        let mut store = extensions.lock().map_err(|_| "HOST_BUSY")?;
        let receipt = tauri::async_runtime::block_on(lease.run(async {
            checkpoint()?;
            store
                .as_mut()
                .ok_or("EXTENSIONS_NOT_READY")?
                .install_confirmed(&request.operation_id, &install, &request.fingerprint)
                .await
                .map_err(|error| error.code)
        }))?;
        serde_json::to_value(receipt).map_err(|_| "EXTENSION_INSTALL_FAILED".into())
    })
    .await
    .map_err(|_| "EXTENSION_INSTALL_FAILED".to_string())?
}

#[tauri::command]
pub async fn extension_install_rollback(
    window: WebviewWindow,
    host: State<'_, Host>,
    operation_id: String,
    installed_operation_id: String,
    vault_id: String,
) -> Result<Value, String> {
    main_window(&window)?;
    let workspace = host.workspace.clone();
    let extensions = host.extensions.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let workspace = workspace.lock().map_err(|_| "HOST_BUSY")?;
        if workspace.as_ref().ok_or("VAULT_NOT_OPEN")?.vault_id != vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let mut store = extensions.lock().map_err(|_| "HOST_BUSY")?;
        let store = store.as_mut().ok_or("EXTENSIONS_NOT_READY")?;
        let changes = store
            .rollback_changes(&installed_operation_id)
            .map_err(|error| error.code)?;
        let receipt =
            tauri::async_runtime::block_on(store.switch_online(&operation_id, &vault_id, &changes))
                .map_err(|error| error.code)?;
        serde_json::to_value(receipt).map_err(|_| "EXTENSION_ROLLBACK_FAILED".into())
    })
    .await
    .map_err(|_| "EXTENSION_ROLLBACK_FAILED".to_string())?
}

#[tauri::command]
pub fn extension_uninstall(
    window: WebviewWindow,
    host: State<'_, Host>,
    operation_id: String,
    slot: String,
    expected_revision: String,
) -> Result<Value, String> {
    main_window(&window)?;
    host.extension_authority.revoke();
    #[cfg(windows)]
    host.extension_instances
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .stop_all_and_join();
    #[cfg(windows)]
    host.extension_endpoints
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .clear();
    let receipt = store(&host, |s| {
        s.uninstall_active(&operation_id, &slot, &expected_revision)
    })?;
    serde_json::to_value(receipt).map_err(|_| "EXTENSION_UNINSTALL_FAILED".into())
}

#[cfg(windows)]
type RuntimeBackend = (
    String,
    Vec<String>,
    BTreeMap<String, notesagent_host::extension_permit::Environment>,
);

#[cfg(windows)]
fn runtime_backend(manifest: &Value) -> Result<RuntimeBackend, String> {
    let backend = manifest.get("backend").unwrap_or(manifest);
    if backend
        .get("type")
        .and_then(Value::as_str)
        .is_some_and(|kind| kind != "mcp")
        || backend.get("transport").and_then(Value::as_str) != Some("stdio")
    {
        return Err("EXTENSION_RUNTIME_UNSUPPORTED".into());
    }
    let entry = backend
        .get("command")
        .and_then(Value::as_str)
        .ok_or("EXTENSION_ENTRY_INVALID")?
        .trim_start_matches("./")
        .to_owned();
    let arguments = backend
        .get("args")
        .and_then(Value::as_array)
        .ok_or("EXTENSION_ENTRY_INVALID")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or("EXTENSION_ENTRY_INVALID".into())
        })
        .collect::<Result<Vec<_>, String>>()?;
    let mut environment = BTreeMap::new();
    if let Some(values) = backend.get("environment") {
        for (name, value) in values.as_object().ok_or("EXTENSION_ENTRY_INVALID")? {
            let value = value.as_str().ok_or("EXTENSION_ENTRY_INVALID")?;
            environment.insert(
                name.clone(),
                notesagent_host::extension_permit::Environment::Literal(value.to_owned()),
            );
        }
    }
    Ok((entry, arguments, environment))
}

#[cfg(windows)]
fn rollback_pending(host: &Host, operation: Option<&str>) {
    if let Some(operation) = operation {
        let _ = store(host, |store| store.finish_installation(operation, false));
    }
}

#[cfg(windows)]
#[tauri::command]
pub async fn extension_enable(
    window: WebviewWindow,
    host: State<'_, Host>,
    slot: String,
    vault_id: String,
    install_operation_id: Option<String>,
) -> Result<Value, String> {
    main_window(&window)?;
    let workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
    if workspace.as_ref().ok_or("VAULT_NOT_OPEN")?.vault_id != vault_id {
        return Err("VAULT_CHANGED".into());
    }
    drop(workspace);
    let runtime = store(&host, |store| {
        store.runtime_package(&slot, &vault_id, install_operation_id.as_deref())
    })?;
    let (entry, arguments, environment) = match runtime_backend(&runtime.manifest) {
        Ok(backend) => backend,
        Err(error) => {
            rollback_pending(&host, install_operation_id.as_deref());
            return Err(error);
        }
    };
    if !runtime.inventory.files.contains_key(&entry) {
        rollback_pending(&host, install_operation_id.as_deref());
        return Err("EXTENSION_ENTRY_INVALID".into());
    }
    let expires_at_ms = u64::try_from(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| "EXTENSION_CLOCK_INVALID")?
            .as_millis(),
    )
    .map_err(|_| "EXTENSION_CLOCK_INVALID")?
        + 8 * 60 * 60 * 1000;
    let claims = notesagent_host::extension_permit::Claims {
        kind: if runtime.release.kind == "plugin" {
            notesagent_host::extension_permit::ExecutionKind::Plugin
        } else {
            notesagent_host::extension_permit::ExecutionKind::Mcp
        },
        source: runtime.source.clone(),
        namespace: runtime.release.namespace.clone(),
        package_id: runtime.release.package_id.clone(),
        version: runtime.release.version.clone(),
        archive_sha256: runtime.release.sha256.clone(),
        tree_sha256: runtime.active.target.tree_sha256.clone(),
        signer_sha256: runtime.signer_sha256,
        entry,
        arguments,
        environment,
        permissions: runtime
            .release
            .permissions
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>(),
        vault_id: vault_id.clone(),
        platform: std::env::consts::OS.into(),
        policy_version: "1".into(),
        expires_at_ms,
    };
    let permit = match host
        .extension_authority
        .issue(&claims, expires_at_ms - 8 * 60 * 60 * 1000)
    {
        Ok(permit) => permit,
        Err(error) => {
            rollback_pending(&host, install_operation_id.as_deref());
            return Err(error.code);
        }
    };
    let extensions = Arc::clone(&host.extensions);
    let check_slot = slot.clone();
    let check_vault = vault_id.clone();
    let check_revision = runtime.active.revision.clone();
    let check_package = runtime.active.target.package_key.clone();
    let check_operation = install_operation_id.clone();
    let Some(system_root) = std::env::var_os("SystemRoot") else {
        rollback_pending(&host, install_operation_id.as_deref());
        return Err("SYSTEM_ROOT_MISSING".into());
    };
    let spec = notesagent_host::extension_instance::LaunchSpec {
        package: runtime.package,
        inventory: runtime.inventory,
        claims,
        permit,
        authority: Arc::clone(&host.extension_authority),
        credentials: Arc::clone(&host.credentials),
        vault_id,
        policy_version: "1".into(),
        system_root: PathBuf::from(system_root),
        before_resume: Box::new(move |_| {
            let store = extensions
                .lock()
                .map_err(|_| notesagent_host::workspace::HostError::new("HOST_BUSY"))?;
            let current = store
                .as_ref()
                .ok_or_else(|| notesagent_host::workspace::HostError::new("EXTENSIONS_NOT_READY"))?
                .runtime_package(&check_slot, &check_vault, check_operation.as_deref())?;
            if current.active.revision != check_revision
                || current.active.target.package_key != check_package
            {
                return Err(notesagent_host::workspace::HostError::new(
                    "EXTENSION_INSTALL_CONFLICT",
                ));
            }
            Ok(())
        }),
    };
    let endpoint = unsafe {
        host.extension_instances
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .start(spec)
    };
    let endpoint = match endpoint {
        Ok(endpoint) => endpoint,
        Err(error) => {
            rollback_pending(&host, install_operation_id.as_deref());
            return Err(error.code);
        }
    };
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        let snapshot = endpoint.snapshot();
        if snapshot.status == notesagent_host::extension_instance::Status::Ready {
            if let Some(operation) = &install_operation_id {
                store(&host, |store| store.finish_installation(operation, true))?;
            }
            host.extension_endpoints
                .lock()
                .map_err(|_| "HOST_BUSY")?
                .insert(slot, endpoint);
            return serde_json::to_value(snapshot).map_err(|_| "EXTENSION_INSTANCE_INVALID".into());
        }
        if snapshot.status == notesagent_host::extension_instance::Status::Failed
            || Instant::now() >= deadline
        {
            endpoint.stop();
            rollback_pending(&host, install_operation_id.as_deref());
            return Err(snapshot
                .error
                .unwrap_or_else(|| "EXTENSION_START_TIMEOUT".into()));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(windows)]
#[tauri::command]
pub fn extension_instance_status(
    window: WebviewWindow,
    host: State<'_, Host>,
    slot: String,
) -> Result<Value, String> {
    main_window(&window)?;
    let endpoints = host.extension_endpoints.lock().map_err(|_| "HOST_BUSY")?;
    let endpoint = endpoints.get(&slot).ok_or("EXTENSION_INSTANCE_NOT_READY")?;
    serde_json::to_value(endpoint.snapshot()).map_err(|_| "EXTENSION_INSTANCE_INVALID".into())
}

#[cfg(windows)]
#[tauri::command]
pub fn extension_disable(
    window: WebviewWindow,
    host: State<'_, Host>,
    slot: String,
) -> Result<(), String> {
    main_window(&window)?;
    let endpoint = host
        .extension_endpoints
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .remove(&slot)
        .ok_or("EXTENSION_INSTANCE_NOT_READY")?;
    endpoint.stop();
    Ok(())
}

#[cfg(windows)]
#[tauri::command]
pub async fn extension_call_review(
    window: WebviewWindow,
    host: State<'_, Host>,
    slot: String,
    tool: String,
    arguments: Value,
) -> Result<Value, String> {
    main_window(&window)?;
    let endpoint = host
        .extension_endpoints
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .get(&slot)
        .cloned()
        .ok_or("EXTENSION_INSTANCE_NOT_READY")?;
    tauri::async_runtime::spawn_blocking(move || {
        let review = endpoint
            .review(tool, arguments)
            .map_err(|error| error.code)?
            .wait(Duration::from_secs(5))
            .map_err(|error| error.code)?;
        serde_json::to_value(review).map_err(|_| "EXTENSION_CALL_REVIEW_INVALID".into())
    })
    .await
    .map_err(|_| "EXTENSION_CALL_REVIEW_INVALID".to_string())?
}

#[cfg(windows)]
#[tauri::command]
pub async fn extension_call_confirm(
    window: WebviewWindow,
    host: State<'_, Host>,
    slot: String,
    review_id: String,
) -> Result<Value, String> {
    main_window(&window)?;
    let endpoint = host
        .extension_endpoints
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .get(&slot)
        .cloned()
        .ok_or("EXTENSION_INSTANCE_NOT_READY")?;
    tauri::async_runtime::spawn_blocking(move || {
        endpoint
            .invoke_confirmed(review_id)
            .map_err(|error| error.code)?
            .wait(Duration::from_secs(65))
            .map_err(|error| error.code)
    })
    .await
    .map_err(|_| "EXTENSION_CALL_FAILED".to_string())?
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StageRequest {
    request_id: String,
    operation_id: String,
    source: String,
    release: notesagent_host::extension_package::Release,
}
#[tauri::command]
pub async fn extension_stage(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: StageRequest,
) -> Result<Value, String> {
    main_window(&window)?;
    let mut lease = host.extension_requests.claim(&request.request_id)?;
    let checkpoint = lease.checkpoint();
    let extensions = host.extensions.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut store = loop {
            checkpoint()?;
            match extensions.try_lock() {
                Ok(guard) => break guard,
                Err(std::sync::TryLockError::Poisoned(_)) => return Err("HOST_BUSY".into()),
                Err(std::sync::TryLockError::WouldBlock) => {
                    std::thread::sleep(Duration::from_millis(10))
                }
            }
        };
        let store = store.as_mut().ok_or("EXTENSIONS_NOT_READY")?;
        let receipt = tauri::async_runtime::block_on(lease.run(async {
            checkpoint()?;
            store
                .stage_online_checked(
                    &request.operation_id,
                    &request.source,
                    &request.release,
                    || {
                        checkpoint()
                            .map_err(|code| notesagent_host::workspace::HostError::new(&code))
                    },
                )
                .await
                .map_err(|e| e.code)
        }))?;
        serde_json::to_value(receipt).map_err(|_| "EXTENSION_STAGE_FAILED".into())
    })
    .await
    .map_err(|_| "EXTENSION_STAGE_FAILED".to_string())?
}

#[tauri::command]
pub fn extension_stage_prepare(
    window: WebviewWindow,
    host: State<'_, Host>,
) -> Result<String, String> {
    main_window(&window)?;
    host.extension_requests.prepare(60_000)
}
#[tauri::command]
pub fn extension_stage_cancel(
    window: WebviewWindow,
    host: State<'_, Host>,
    request_id: String,
) -> Result<(), String> {
    main_window(&window)?;
    host.extension_requests.cancel(&request_id)
}
#[tauri::command]
pub fn extension_stage_status(
    window: WebviewWindow,
    host: State<'_, Host>,
    operation_id: String,
) -> Result<Value, String> {
    main_window(&window)?;
    let receipt = store(&host, |s| s.stage_receipt(&operation_id))?;
    serde_json::to_value(receipt).map_err(|_| "EXTENSION_STATUS_FAILED".into())
}

#[tauri::command]
pub async fn extension_staged(
    window: WebviewWindow,
    host: State<'_, Host>,
    offset: u32,
    limit: u32,
) -> Result<Value, String> {
    main_window(&window)?;
    let extensions = host.extensions.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let store = extensions.lock().map_err(|_| "HOST_BUSY")?;
        let items = store
            .as_ref()
            .ok_or("EXTENSIONS_NOT_READY")?
            .staged(offset, limit)
            .map_err(|e| e.code)?;
        serde_json::to_value(items).map_err(|_| "EXTENSION_LIST_FAILED".into())
    })
    .await
    .map_err(|_| "EXTENSION_LIST_FAILED".to_string())?
}

#[tauri::command]
pub fn extension_trust_confirm_group(
    window: WebviewWindow,
    host: State<'_, Host>,
    requests: Vec<Confirmation>,
) -> Result<Vec<String>, String> {
    main_window(&window)?;
    trust_confirm_group(&host, requests)
}
fn trust_confirm_group(host: &Host, requests: Vec<Confirmation>) -> Result<Vec<String>, String> {
    if requests.is_empty() || requests.len() > 64 {
        return Err("EXTENSION_TRUST_INVALID".into());
    }
    let mut reviews = host.extension_reviews.0.lock().map_err(|_| "HOST_BUSY")?;
    let mut ids = std::collections::BTreeSet::new();
    let mut proposals = Vec::new();
    for request in &requests {
        if !ids.insert(&request.review_id) {
            return Err("EXTENSION_TRUST_INVALID".into());
        }
        let review = reviews
            .get(&request.review_id)
            .ok_or("EXTENSION_REVIEW_EXPIRED")?;
        if review.expires <= Instant::now() {
            return Err("EXTENSION_REVIEW_EXPIRED".into());
        }
        if review.fingerprint != request.fingerprint {
            return Err("EXTENSION_TRUST_CONFIRMATION".into());
        }
        proposals.push((
            review.setting.clone(),
            review.expected.clone(),
            review.fingerprint.clone(),
        ));
    }
    let revisions = store(host, |s| s.confirm_trust_group(&proposals))?;
    for request in requests {
        reviews.remove(&request.review_id);
    }
    Ok(revisions)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn failed_group_does_not_consume_valid_review_then_success_consumes_all() {
        let temp = tempfile::tempdir().unwrap();
        let host = Host::default();
        *host.extensions.lock().unwrap() = Some(ExtensionStore::open(temp.path()).unwrap());
        let mut setting = TrustSetting {
            source: "https://catalog.example/".into(),
            source_id: "catalog".into(),
            namespace: "examples".into(),
            key_id: "one".into(),
            public_key: ed25519_dalek::SigningKey::from_bytes(&[7; 32])
                .verifying_key()
                .to_bytes(),
            enabled: true,
        };
        let first = trust_review(&host, setting.clone()).unwrap();
        setting.key_id = "two".into();
        let second = trust_review(&host, setting).unwrap();
        let input = |value: &Value| Confirmation {
            review_id: value["review_id"].as_str().unwrap().into(),
            fingerprint: value["fingerprint"].as_str().unwrap().into(),
        };
        let mut wrong = input(&second);
        wrong.fingerprint = "wrong".into();
        assert!(trust_confirm_group(&host, vec![input(&first), wrong]).is_err());
        assert_eq!(host.extension_reviews.0.lock().unwrap().len(), 2);
        assert!(store(&host, |s| s.trust_setting(
            "https://catalog.example/",
            "examples",
            "one"
        ))
        .unwrap()
        .is_none());
        trust_confirm_group(&host, vec![input(&first), input(&second)]).unwrap();
        assert!(host.extension_reviews.0.lock().unwrap().is_empty());
    }
    #[test]
    fn review_nonce_expiry_consumption_and_stale_setting_are_enforced() {
        let temp = tempfile::tempdir().unwrap();
        let host = Host::default();
        *host.extensions.lock().unwrap() = Some(ExtensionStore::open(temp.path()).unwrap());
        let setting = TrustSetting {
            source: "https://catalog.example/".into(),
            source_id: "catalog".into(),
            namespace: "examples".into(),
            key_id: "key".into(),
            public_key: ed25519_dalek::SigningKey::from_bytes(&[7; 32])
                .verifying_key()
                .to_bytes(),
            enabled: true,
        };
        let review = trust_review(&host, setting.clone()).unwrap();
        let id = review["review_id"].as_str().unwrap();
        let fingerprint = review["fingerprint"].as_str().unwrap();
        assert!(trust_confirm(
            &host,
            Confirmation {
                review_id: id.into(),
                fingerprint: "wrong".into()
            }
        )
        .is_err());
        trust_confirm(
            &host,
            Confirmation {
                review_id: id.into(),
                fingerprint: fingerprint.into(),
            },
        )
        .unwrap();
        assert!(trust_confirm(
            &host,
            Confirmation {
                review_id: id.into(),
                fingerprint: fingerprint.into()
            }
        )
        .is_err());
        let mut changed = setting.clone();
        changed.enabled = false;
        let review = trust_review(&host, changed).unwrap();
        let id = review["review_id"].as_str().unwrap();
        host.extension_reviews
            .0
            .lock()
            .unwrap()
            .get_mut(id)
            .unwrap()
            .expires = Instant::now() - Duration::from_secs(1);
        assert!(trust_confirm(
            &host,
            Confirmation {
                review_id: id.into(),
                fingerprint: review["fingerprint"].as_str().unwrap().into()
            }
        )
        .is_err());
        assert!(host.extension_reviews.0.lock().unwrap().get(id).is_none());
        let mut changed = setting.clone();
        changed.public_key = ed25519_dalek::SigningKey::from_bytes(&[8; 32])
            .verifying_key()
            .to_bytes();
        let review = trust_review(&host, changed).unwrap();
        let mut concurrent = setting.clone();
        concurrent.enabled = false;
        store(&host, |s| {
            s.confirm_trust(
                &concurrent,
                Some(&setting.fingerprint()?),
                &concurrent.fingerprint()?,
            )
        })
        .unwrap();
        assert!(trust_confirm(
            &host,
            Confirmation {
                review_id: review["review_id"].as_str().unwrap().into(),
                fingerprint: review["fingerprint"].as_str().unwrap().into()
            }
        )
        .is_err());
    }
}
