use notesagent_host::mobile_workspace::{MobileRequest, MobileResponse, MobileWorkspace};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::Manager;
mod mobile_files;

struct MobileState {
    engine: Arc<Mutex<Option<MobileWorkspace>>>,
    private_root: PathBuf,
}

#[derive(Serialize)]
struct Capabilities {
    protocol: u32,
    platform: &'static str,
    version: &'static str,
    workspace: bool,
    notes: bool,
    exports: bool,
    external_links: bool,
    navigation: bool,
    core: bool,
    sync: bool,
    credentials: bool,
    experiments: bool,
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
        notes: true,
        exports: cfg!(target_os = "android"),
        external_links: cfg!(target_os = "android"),
        navigation: cfg!(target_os = "android"),
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
) -> Result<MobileResponse, String> {
    main_window(&window)?;
    let engine = Arc::clone(&state.engine);
    let root = state.private_root.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = engine.lock().map_err(|_| "MOBILE_HOST_BUSY".to_owned())?;
        if guard.is_none() {
            *guard = Some(MobileWorkspace::open(&root).map_err(|error| error.code)?);
        }
        guard
            .as_mut()
            .ok_or_else(|| "MOBILE_STORAGE_UNAVAILABLE".to_owned())?
            .snapshot()
            .map_err(|error| error.code)
    })
    .await
    .map_err(|_| "MOBILE_HOST_INTERRUPTED".to_owned())?
}

#[tauri::command]
async fn mobile_request(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, MobileState>,
    request: MobileRequest,
) -> Result<MobileResponse, String> {
    main_window(&window)?;
    let engine = Arc::clone(&state.engine);
    let root = state.private_root.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = engine.lock().map_err(|_| "MOBILE_HOST_BUSY".to_owned())?;
        if guard.is_none() {
            *guard = Some(MobileWorkspace::open(&root).map_err(|error| error.code)?);
        }
        guard
            .as_mut()
            .ok_or_else(|| "MOBILE_STORAGE_UNAVAILABLE".to_owned())?
            .dispatch(request)
            .map_err(|error| error.code)
    })
    .await
    .map_err(|_| "MOBILE_HOST_INTERRUPTED".to_owned())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(mobile_files::init())
        .setup(|app| {
            app.manage(MobileState {
                engine: Arc::new(Mutex::new(None)),
                private_root: app.path().app_data_dir()?,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            mobile_capabilities,
            mobile_vault,
            mobile_request,
            mobile_files::mobile_save_export,
            mobile_files::mobile_export_delivery,
            mobile_files::mobile_open_external,
            mobile_files::mobile_background
        ])
        .run(tauri::generate_context!())
        .expect("Failed to start OpenNexus mobile");
}
