use notesagent_host::workspace::Workspace;
use serde::Serialize;
use std::sync::{Arc, Mutex};
use tauri::Manager;

struct MobileState {
    workspace: Arc<Mutex<Result<Workspace, String>>>,
}

#[derive(Serialize)]
struct Capabilities {
    protocol: u32,
    platform: &'static str,
    version: &'static str,
    workspace: bool,
    core: bool,
    sync: bool,
    credentials: bool,
    experiments: bool,
}

#[derive(Serialize)]
struct VaultSnapshot {
    vault_id: String,
    note_count: usize,
}

fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("MOBILE_WINDOW_DENIED".into());
    }
    Ok(())
}

#[tauri::command]
fn mobile_capabilities(window: tauri::WebviewWindow) -> Result<Capabilities, String> {
    main_window(&window)?;
    Ok(Capabilities {
        protocol: 1,
        platform: if cfg!(target_os = "android") {
            "android"
        } else {
            "unsupported"
        },
        version: env!("CARGO_PKG_VERSION"),
        workspace: true,
        core: false,
        sync: false,
        credentials: false,
        experiments: false,
    })
}

#[tauri::command]
async fn mobile_vault(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, MobileState>,
) -> Result<VaultSnapshot, String> {
    main_window(&window)?;
    let workspace = Arc::clone(&state.workspace);
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = workspace.lock().map_err(|_| "MOBILE_HOST_BUSY")?;
        let vault = guard.as_mut().map_err(|code| code.clone())?;
        let entries = vault.tree().map_err(|error| error.code)?;
        Ok(VaultSnapshot {
            vault_id: vault.vault_id.clone(),
            note_count: entries
                .iter()
                .filter(|entry| {
                    !entry.deleted
                        && !entry.is_folder
                        && entry.path.to_ascii_lowercase().ends_with(".md")
                })
                .count(),
        })
    })
    .await
    .map_err(|_| "MOBILE_HOST_INTERRUPTED".to_owned())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let root = app.path().app_data_dir()?.join("vault");
            let workspace = std::fs::create_dir_all(&root)
                .map_err(|_| "MOBILE_STORAGE_UNAVAILABLE".to_owned())
                .and_then(|_| Workspace::open(&root).map_err(|error| error.code));
            app.manage(MobileState {
                workspace: Arc::new(Mutex::new(workspace)),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![mobile_capabilities, mobile_vault])
        .run(tauri::generate_context!())
        .expect("Failed to start OpenNexus mobile");
}
