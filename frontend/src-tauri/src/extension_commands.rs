//! Only the local main window may review Host trust or prepared installations.
use super::Host;
use notesagent_host::extension_store::{ExtensionStore, InstallRequest, TrustSetting};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
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
        // Keep the workspace binding stable until this preview finishes.
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

#[cfg(test)]
mod tests {
    use super::*;
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
