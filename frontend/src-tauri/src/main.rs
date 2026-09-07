#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! 预览 Host 只开放本地文件命令；未接通的 AI / 同步 / 凭据能力明确返回不可用。

use notesagent_host::recent::{RecentVault, RecentVaultStore};
use notesagent_host::workspace::{Document, Entry, Workspace};
use std::path::Path;
use std::sync::Mutex;
use tauri::{Emitter, Manager, State};

#[derive(Default)]
struct Host {
    workspace: Mutex<Option<Workspace>>,
    recent: Mutex<Option<RecentVaultStore>>,
}

fn info(ws: &Workspace) -> RecentVault {
    RecentVault {
        vault_id: ws.vault_id.clone(),
        path: ws.root.to_string_lossy().into(),
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
    serde_json::json!({"protocol":1,"workspace":true,"core":false,"sync":false,"credentials":false,"extensions":false,"release":"preview"})
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
    if guard
        .as_ref()
        .is_some_and(|ws| ws.root == Path::new(&authorized.path))
    {
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
