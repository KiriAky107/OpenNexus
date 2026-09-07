#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! 预览 Host 只开放本地文件命令；未接通的 AI / 同步 / 凭据能力明确返回不可用。

use notesagent_host::recent::{RecentVault, RecentVaultStore};
use notesagent_host::workspace::{portable_path_string, Document, Entry, Workspace};
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Emitter, Manager, State};

#[derive(Default)]
struct Host {
    workspace: Mutex<Option<Workspace>>,
    recent: Mutex<Option<RecentVaultStore>>,
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
fn host_capabilities() -> serde_json::Value {
    serde_json::json!({"protocol":1,"workspace":true,"core":true,"sync":false,"credentials":false,"extensions":false,"release":"preview"})
}

#[derive(serde::Serialize)]
struct CoreResponse {
    status: u16,
    content_type: String,
    body: String,
}

fn core_url(path: &str) -> Result<String, String> {
    if (!path.starts_with("/api/") && path != "/api" && path != "/health")
        || path.contains("..")
        || path.contains(['\r', '\n'])
    {
        return Err("CORE_PATH_DENIED".into());
    }
    Ok(format!("http://127.0.0.1:8000{path}"))
}

#[cfg(test)]
mod core_proxy_tests {
    use super::core_url;

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
}

/// 预览版只代理固定回环地址，避免 WebView CORS 与任意地址转发。
#[tauri::command]
async fn core_request(
    method: String,
    path: String,
    body: Option<serde_json::Value>,
    authorization: Option<String>,
) -> Result<CoreResponse, String> {
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
        .build()
        .map_err(|_| "CORE_CLIENT_ERROR")?;
    let mut request = client.request(method, core_url(&path)?);
    if let Some(value) = body {
        request = request.json(&value);
    }
    if let Some(value) = authorization {
        request = request.header(reqwest::header::AUTHORIZATION, value);
    }
    let response = request.send().await.map_err(|_| "CORE_UNAVAILABLE")?;
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let body = response.text().await.map_err(|_| "CORE_RESPONSE_ERROR")?;
    Ok(CoreResponse {
        status,
        content_type,
        body,
    })
}

#[tauri::command]
fn editor_capabilities(app: tauri::AppHandle, import_enabled: bool) -> Result<(), String> {
    app.state::<tauri::menu::MenuItem<tauri::Wry>>()
        .set_enabled(import_enabled)
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
                "导入为笔记属性…",
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
            core_request,
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
        .run(tauri::generate_context!())
        .expect("桌面 Host 启动失败");
}
