#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! 预览 Host 只开放本地文件命令；未接通的 AI / 同步 / 凭据能力明确返回不可用。

use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use notesagent_host::core::CoreSupervisor;
use notesagent_host::credentials::CredentialBroker;
use notesagent_host::recent::{RecentVault, RecentVaultStore};
use notesagent_host::workspace::{portable_path_string, Document, Entry, Workspace};
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{Emitter, Manager, State};
use zeroize::Zeroizing;

#[derive(Default)]
struct Host {
    workspace: Mutex<Option<Workspace>>,
    recent: Mutex<Option<RecentVaultStore>>,
    core: Arc<Mutex<Option<CoreSupervisor>>>,
    credentials: Arc<Mutex<Option<CredentialBroker>>>,
    streams: Arc<Mutex<HashMap<String, tauri::async_runtime::JoinHandle<()>>>>,
}

fn info(ws: &Workspace) -> RecentVault {
    RecentVault {
        vault_id: ws.vault_id.clone(),
        path: portable_path_string(&ws.root),
        name: ws
            .root
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into(),
    }
}

fn with_workspace<T>(
    host: &Host,
    f: impl FnOnce(&mut Workspace) -> notesagent_host::workspace::Result<T>,
) -> Result<T, String> {
    let mut guard = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
    let ws = guard.as_mut().ok_or("VAULT_NOT_OPEN")?;
    f(ws).map_err(|e| e.code)
}

#[tauri::command]
fn host_capabilities(host: State<'_, Host>) -> serde_json::Value {
    let ready = host
        .core
        .try_lock()
        .ok()
        .and_then(|mut core| core.as_mut().map(|c| c.available()))
        .unwrap_or(false);
    serde_json::json!({"protocol":1,"workspace":true,"core":ready,"sync":false,"credentials":true,"extensions":false,"release":"preview","product":"OpenNexus"})
}

#[derive(serde::Serialize)]
struct CoreResponse {
    status: u16,
    content_type: String,
    body: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    body_base64: Option<String>,
}

// 后端常规导出上限为 20 MiB；为 PDF 和未来的二进制接口保留余量，同时限制 IPC 内存占用。
const MAX_CORE_RESPONSE_BYTES: usize = 64 * 1024 * 1024;

fn is_json_content_type(content_type: &str) -> bool {
    let media_type = content_type
        .split(';')
        .next()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    media_type == "application/json" || media_type.ends_with("+json")
}

#[cfg(test)]
fn core_url(path: &str) -> Result<String, String> {
    notesagent_host::core::checked_url(8000, path)
}

#[cfg(test)]
mod core_proxy_tests {
    use super::{core_url, is_json_content_type};

    #[test]
    fn only_allows_expected_loopback_paths() {
        assert_eq!(core_url("/health").unwrap(), "http://127.0.0.1:8000/health");
        assert!(core_url("/api/status?verbose=true").is_ok());
        assert!(core_url("https://example.com/api/status").is_err());
        assert!(core_url("/api/../secret").is_err());
    }

    #[test]
    fn desktop_csp_allows_shiki_wasm_without_general_eval() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let csp = config["app"]["security"]["csp"].as_str().unwrap();
        assert!(csp.contains("'wasm-unsafe-eval'"));
        assert!(!csp.split_whitespace().any(|token| token == "'unsafe-eval'"));
    }

    #[test]
    fn only_json_media_types_use_text_ipc_payloads() {
        assert!(is_json_content_type("application/json; charset=utf-8"));
        assert!(is_json_content_type("application/problem+json"));
        assert!(!is_json_content_type("text/html; charset=utf-8"));
        assert!(!is_json_content_type("application/pdf"));
        assert!(!is_json_content_type(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ));
    }
}

/// Authenticated process-local transport; session headers are owned by Rust.
#[tauri::command]
async fn core_request(
    method: String,
    path: String,
    body: Option<serde_json::Value>,
    body_base64: Option<String>,
    content_type: Option<String>,
    idempotency_key: Option<String>,
    host: State<'_, Host>,
) -> Result<CoreResponse, String> {
    let core = host.core.clone();
    let core_path = path.clone();
    let session = tauri::async_runtime::spawn_blocking(move || {
        core.lock()
            .map_err(|_| "HOST_BUSY")?
            .as_mut()
            .ok_or("CORE_UNAVAILABLE")?
            .request_session(&core_path)
    })
    .await
    .map_err(|_| "CORE_UNAVAILABLE")??;
    let method =
        reqwest::Method::from_bytes(method.as_bytes()).map_err(|_| "CORE_METHOD_DENIED")?;
    if !matches!(
        method,
        reqwest::Method::GET
            | reqwest::Method::POST
            | reqwest::Method::PUT
            | reqwest::Method::PATCH
            | reqwest::Method::DELETE
    ) {
        return Err("CORE_METHOD_DENIED".into());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .build()
        .map_err(|_| "CORE_CLIENT_ERROR")?;
    let mut request = client
        .request(method, &session.url)
        .header(
            reqwest::header::AUTHORIZATION,
            session.authorization.as_str(),
        )
        .header("X-Core-Generation", &session.generation);
    if let Some(value) = body {
        request = request.json(&value);
    }
    if let Some(encoded) = body_base64 {
        if encoded.len() > MAX_CORE_RESPONSE_BYTES * 4 / 3 + 4 {
            return Err("CORE_REQUEST_TOO_LARGE".into());
        }
        let bytes = BASE64_STANDARD
            .decode(encoded)
            .map_err(|_| "CORE_BODY_INVALID")?;
        if bytes.len() > MAX_CORE_RESPONSE_BYTES {
            return Err("CORE_REQUEST_TOO_LARGE".into());
        }
        let content_type = content_type
            .as_deref()
            .unwrap_or("application/octet-stream");
        if !matches!(content_type, "application/octet-stream" | "application/zip") {
            return Err("CORE_CONTENT_TYPE_DENIED".into());
        }
        request = request
            .header(reqwest::header::CONTENT_TYPE, content_type)
            .body(bytes);
    }
    if let Some(key) = idempotency_key {
        if key.len() > 128 || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
            return Err("CORE_HEADER_INVALID".into());
        }
        request = request.header("Idempotency-Key", key);
    }
    let mut response = request.send().await.map_err(|_| "CORE_UNAVAILABLE")?;
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_owned();
    if response
        .content_length()
        .is_some_and(|length| length > MAX_CORE_RESPONSE_BYTES as u64)
    {
        return Err("CORE_RESPONSE_TOO_LARGE".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "CORE_RESPONSE_ERROR")? {
        if bytes.len().saturating_add(chunk.len()) > MAX_CORE_RESPONSE_BYTES {
            return Err("CORE_RESPONSE_TOO_LARGE".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    let (body, body_base64) = if is_json_content_type(&content_type) {
        (
            String::from_utf8(bytes.to_vec()).map_err(|_| "CORE_RESPONSE_ERROR")?,
            None,
        )
    } else {
        (String::new(), Some(BASE64_STANDARD.encode(&bytes)))
    };
    Ok(CoreResponse {
        status,
        content_type,
        body,
        body_base64,
    })
}

#[tauri::command]
fn core_stream_cancel(host: State<'_, Host>, request_id: String) -> Result<(), String> {
    if let Some(task) = host
        .streams
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .remove(&request_id)
    {
        task.abort();
    }
    Ok(())
}

#[tauri::command]
fn core_stream(
    host: State<'_, Host>,
    request_id: String,
    path: String,
    method: String,
    body: Option<serde_json::Value>,
    last_event_id: Option<String>,
    channel: tauri::ipc::Channel<serde_json::Value>,
) -> Result<(), String> {
    uuid::Uuid::parse_str(&request_id).map_err(|_| "CORE_REQUEST_ID_INVALID")?;
    if !matches!(method.as_str(), "GET" | "POST") {
        return Err("CORE_METHOD_DENIED".into());
    }
    if body
        .as_ref()
        .is_some_and(|b| b.to_string().len() > 1024 * 1024)
    {
        return Err("CORE_REQUEST_TOO_LARGE".into());
    }
    if last_event_id
        .as_ref()
        .is_some_and(|s| s.len() > 128 || s.contains(['\r', '\n']))
    {
        return Err("CORE_HEADER_INVALID".into());
    }
    let core = host.core.clone();
    let streams = host.streams.clone();
    let mut running = host.streams.lock().map_err(|_| "HOST_BUSY")?;
    if running.len() >= 16 || running.contains_key(&request_id) {
        return Err("CORE_STREAM_LIMIT".into());
    }
    let id = request_id.clone();
    let task = tauri::async_runtime::spawn(async move {
        let result: Result<(), String> = async {
            let session = tauri::async_runtime::spawn_blocking(move || {
                core.lock().map_err(|_| "HOST_BUSY")?.as_mut().ok_or("CORE_UNAVAILABLE")?.request_session(&path)
            }).await.map_err(|_| "CORE_UNAVAILABLE")??;
            let client = reqwest::Client::builder().no_proxy()
                .redirect(reqwest::redirect::Policy::none()).timeout(Duration::from_secs(600))
                .build().map_err(|_| "CORE_CLIENT_ERROR")?;
            let mut request = client.request(reqwest::Method::from_bytes(method.as_bytes()).map_err(|_| "CORE_METHOD_DENIED")?, session.url)
                .header("Authorization", session.authorization.as_str())
                .header("X-Core-Generation", session.generation).header("Accept", "text/event-stream");
            if let Some(body) = body { request = request.json(&body); }
            if let Some(id) = last_event_id { request = request.header("Last-Event-ID", id); }
            let mut response = request.send().await.map_err(|_| "CORE_UNAVAILABLE")?;
            channel.send(serde_json::json!({"kind":"headers","status":response.status().as_u16()})).map_err(|_| "CORE_STREAM_CLOSED")?;
            let mut size = 0usize;
            while let Some(bytes) = response.chunk().await.map_err(|_| "CORE_RESPONSE_ERROR")? {
                size = size.saturating_add(bytes.len());
                if size > MAX_CORE_RESPONSE_BYTES { return Err("CORE_RESPONSE_TOO_LARGE".into()); }
                for chunk in bytes.chunks(16384) {
                    channel.send(serde_json::json!({"kind":"chunk","data":BASE64_STANDARD.encode(chunk)})).map_err(|_| "CORE_STREAM_CLOSED")?;
                }
            }
            Ok(())
        }.await;
        match result {
            Ok(()) => {
                let _ = channel.send(serde_json::json!({"kind":"done"}));
            }
            Err(code) => {
                let _ = channel.send(serde_json::json!({"kind":"error","code":code}));
            }
        }
        if let Ok(mut running) = streams.lock() {
            running.remove(&id);
        }
    });
    running.insert(request_id, task);
    Ok(())
}

#[tauri::command]
fn credentials_status(host: State<'_, Host>) -> Result<serde_json::Value, String> {
    let broker = host
        .credentials
        .try_lock()
        .map_err(|_| "CREDENTIALS_BUSY")?;
    let broker = broker.as_ref().ok_or("HOST_NOT_READY")?;
    Ok(serde_json::json!({"locked":broker.is_locked()}))
}

#[tauri::command]
async fn credentials_unlock(host: State<'_, Host>, password: String) -> Result<(), String> {
    let broker = host.credentials.clone();
    let password = Zeroizing::new(password.into_bytes());
    tauri::async_runtime::spawn_blocking(move || {
        broker
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .as_mut()
            .ok_or("HOST_NOT_READY")?
            .unlock(password)
    })
    .await
    .map_err(|_| "HOST_BUSY")?
}

#[tauri::command]
fn credentials_lock(host: State<'_, Host>) -> Result<(), String> {
    host.credentials
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .as_mut()
        .ok_or("HOST_NOT_READY")?
        .lock();
    Ok(())
}

#[tauri::command]
async fn credentials_import(host: State<'_, Host>) -> Result<Option<usize>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("选择旧版本的 credentials.json（不会删除原文件）")
        .add_filter("Fernet credentials", &["json"])
        .pick_file()
    else {
        return Ok(None);
    };
    if path.file_name().and_then(|n| n.to_str()) != Some("credentials.json") {
        return Err("MIGRATION_SOURCE_INVALID".into());
    }
    let directory = path
        .parent()
        .ok_or("MIGRATION_SOURCE_INVALID")?
        .to_path_buf();
    let broker = host.credentials.clone();
    let environment_key = std::env::var("APP_CREDENTIAL_MASTER_KEY")
        .ok()
        .map(Zeroizing::new);
    tauri::async_runtime::spawn_blocking(move || {
        broker
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .as_mut()
            .ok_or("HOST_NOT_READY")?
            .import_fernet(&directory, environment_key)
            .map(Some)
    })
    .await
    .map_err(|_| "HOST_BUSY")?
}

#[tauri::command]
async fn credentials_change_password(
    host: State<'_, Host>,
    password: String,
) -> Result<(), String> {
    let broker = host.credentials.clone();
    let password = Zeroizing::new(password.into_bytes());
    tauri::async_runtime::spawn_blocking(move || {
        broker
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .as_mut()
            .ok_or("HOST_NOT_READY")?
            .change_password(password)
    })
    .await
    .map_err(|_| "HOST_BUSY")?
}

#[tauri::command]
fn editor_capabilities(app: tauri::AppHandle, metadata_enabled: bool) -> Result<(), String> {
    app.state::<tauri::menu::MenuItem<tauri::Wry>>()
        .set_enabled(metadata_enabled)
        .map_err(|_| "MENU_UNAVAILABLE".into())
}

#[tauri::command]
fn workspace_choose(host: State<'_, Host>) -> Result<Option<RecentVault>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("选择本地 Vault")
        .pick_folder()
    else {
        return Ok(None);
    };
    let mut guard = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
    if guard
        .as_ref()
        .is_some_and(|ws| ws.root == path.canonicalize().unwrap_or_default())
    {
        return Ok(guard.as_ref().map(info));
    }
    let workspace = Workspace::open(&path).map_err(|e| e.code)?;
    let result = info(&workspace);
    host.recent
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .as_mut()
        .ok_or("HOST_NOT_READY")?
        .remember(&result)?;
    *guard = Some(workspace);
    Ok(Some(result))
}

#[tauri::command]
fn workspace_open(host: State<'_, Host>, path: String) -> Result<RecentVault, String> {
    let authorized = host
        .recent
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .as_ref()
        .ok_or("HOST_NOT_READY")?
        .authorized(Path::new(&path))?
        .ok_or("VAULT_NOT_AUTHORIZED")?;
    let mut guard = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
    if guard.as_ref().is_some_and(|ws| {
        Path::new(&authorized.path)
            .canonicalize()
            .is_ok_and(|path| ws.root == path)
    }) {
        return Ok(guard.as_ref().map(info).ok_or("VAULT_NOT_OPEN")?);
    }
    let workspace = Workspace::open(Path::new(&authorized.path)).map_err(|e| e.code)?;
    let result = info(&workspace);
    *guard = Some(workspace);
    Ok(result)
}

#[tauri::command]
fn workspace_recent(host: State<'_, Host>) -> Result<Vec<RecentVault>, String> {
    host.recent
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .as_ref()
        .ok_or("HOST_NOT_READY")?
        .list()
}

#[tauri::command]
fn workspace_revoke(host: State<'_, Host>) -> Result<(), String> {
    let mut workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
    if let Some(active) = workspace.as_ref() {
        host.recent
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .as_mut()
            .ok_or("HOST_NOT_READY")?
            .revoke(&active.root)?;
    }
    *workspace = None;
    Ok(())
}

#[tauri::command]
fn workspace_tree(host: State<'_, Host>) -> Result<Vec<Entry>, String> {
    with_workspace(&host, |ws| ws.scan())
}
#[tauri::command]
fn workspace_read(host: State<'_, Host>, path: String) -> Result<Document, String> {
    with_workspace(&host, |ws| ws.read(&path))
}
#[tauri::command]
fn workspace_write(
    host: State<'_, Host>,
    path: String,
    expected: String,
    content: String,
) -> Result<Entry, String> {
    with_workspace(&host, |ws| {
        ws.write(&path, &expected, content.as_bytes(), "local")
    })
}
#[tauri::command]
fn workspace_rename(
    host: State<'_, Host>,
    path: String,
    destination: String,
    expected: String,
) -> Result<Entry, String> {
    with_workspace(&host, |ws| ws.rename(&path, &destination, &expected))
}
#[tauri::command]
fn workspace_delete(host: State<'_, Host>, path: String, expected: String) -> Result<(), String> {
    with_workspace(&host, |ws| ws.delete(&path, &expected))
}
#[tauri::command]
fn workspace_mkdir(host: State<'_, Host>, path: String) -> Result<(), String> {
    with_workspace(&host, |ws| ws.mkdir(&path))
}

fn main() {
    tauri::Builder::default()
        .manage(Host::default())
        .setup(|app| {
            use tauri::menu::{Menu, MenuItem, Submenu};
            let import = MenuItem::with_id(
                app,
                "editor.import-note-properties",
                "元数据 / YAML Front Matter…",
                false,
                Some("CmdOrCtrl+Alt+P"),
            )?;
            let paragraph = Submenu::with_items(app, "段落", true, &[&import])?;
            app.manage(import);
            app.set_menu(Menu::with_items(app, &[&paragraph])?)?;
            let state_path = app.path().app_data_dir()?.join("host-state.sqlite3");
            *app.state::<Host>()
                .recent
                .lock()
                .map_err(|_| std::io::Error::other("HOST_BUSY"))? =
                Some(RecentVaultStore::open(&state_path).map_err(std::io::Error::other)?);
            let credential_state = app.state::<Host>().credentials.clone();
            *credential_state
                .lock()
                .map_err(|_| std::io::Error::other("HOST_BUSY"))? = Some(CredentialBroker::new(
                app.path().app_data_dir()?.join("credentials/stronghold.v1"),
            ));
            let data_dir = app.path().app_data_dir()?.join("core-data");
            // Debug builds use this worktree's interpreter; release builds only use bundled Core.
            let core = if cfg!(debug_assertions) {
                let backend = Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("../../backend")
                    .canonicalize()?;
                let python = backend.join(if cfg!(windows) {
                    ".venv/Scripts/python.exe"
                } else {
                    ".venv/bin/python"
                });
                CoreSupervisor::new(
                    python,
                    vec!["-m".into(), "app.sidecar".into()],
                    backend,
                    data_dir,
                )
            } else {
                let root = app.path().resource_dir()?.join("core");
                CoreSupervisor::new(
                    root.join(if cfg!(windows) {
                        "opennexus-core.exe"
                    } else {
                        "opennexus-core"
                    }),
                    vec![],
                    root,
                    data_dir,
                )
                .with_bundle_manifest(
                    include_str!(concat!(env!("OUT_DIR"), "/core-manifest.json")).to_owned(),
                )
            };
            let core = core.with_broker(Arc::new(move |request| {
                credential_state
                    .lock()
                    .map_err(|_| "HOST_BUSY")?
                    .as_mut()
                    .ok_or("HOST_NOT_READY")?
                    .dispatch(request)
            }));
            *app.state::<Host>()
                .core
                .lock()
                .map_err(|_| std::io::Error::other("HOST_BUSY"))? = Some(core);
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if let Ok(mut core) = handle.state::<Host>().core.lock() {
                    if let Some(core) = core.as_mut() {
                        let _ = core.start();
                    }
                }
            });
            Ok(())
        })
        .on_menu_event(|app, event| {
            // 主窗口独占预览 Vault；扩展窗口没有命令 capability。
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.emit("editor-command", event.id().as_ref());
            }
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.emit("host-close-requested", ());
            }
        })
        .invoke_handler(tauri::generate_handler![
            host_capabilities,
            credentials_status,
            credentials_unlock,
            credentials_lock,
            credentials_change_password,
            credentials_import,
            core_request,
            core_stream,
            core_stream_cancel,
            editor_capabilities,
            workspace_choose,
            workspace_open,
            workspace_recent,
            workspace_revoke,
            workspace_tree,
            workspace_read,
            workspace_write,
            workspace_rename,
            workspace_delete,
            workspace_mkdir
        ])
        .build(tauri::generate_context!())
        .expect("桌面 Host 启动失败")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Ok(mut broker) = app.state::<Host>().credentials.lock() {
                    broker.take();
                }
                if let Ok(mut core) = app.state::<Host>().core.lock() {
                    core.take();
                }
            }
        });
}
