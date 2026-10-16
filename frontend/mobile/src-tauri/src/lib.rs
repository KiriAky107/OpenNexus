use notesagent_host::mobile_workspace::{MobileRequest, MobileResponse, MobileWorkspace};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::Manager;
mod mobile_files;
#[cfg(target_os = "android")]
mod mobile_sync_commands;
#[cfg(target_os = "android")]
mod mobile_sync_secrets;
#[cfg(target_os = "android")]
mod mobile_sync_tls;

struct MobileState {
    engine: Arc<Mutex<Option<MobileWorkspace>>>,
    private_root: PathBuf,
    #[cfg(target_os = "android")]
    sync: mobile_sync_commands::Runtime,
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
    appearance: bool,
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
        appearance: cfg!(target_os = "android"),
        core: false,
        sync: false,
        credentials: cfg!(target_os = "android"),
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

#[cfg(target_os = "android")]
#[tauri::command]
async fn mobile_sync_auth(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, MobileState>,
    request: notesagent_host::mobile_sync_commands::Request,
) -> Result<notesagent_host::mobile_sync_commands::Response, String> {
    mobile_sync_commands::request(window, state, request).await
}

#[cfg(not(target_os = "android"))]
#[tauri::command]
fn mobile_sync_auth(
    window: tauri::WebviewWindow,
    request: serde::de::IgnoredAny,
) -> Result<(), String> {
    main_window(&window)?;
    let _ = request;
    Err("SYNC_PLATFORM_UNSUPPORTED".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(mobile_files::init())
        .setup(|app| {
            app.manage(MobileState {
                engine: Arc::new(Mutex::new(None)),
                private_root: app.path().app_data_dir()?,
                #[cfg(target_os = "android")]
                sync: mobile_sync_commands::Runtime::default(),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            mobile_capabilities,
            mobile_vault,
            mobile_request,
            mobile_sync_auth,
            mobile_files::mobile_save_export,
            mobile_files::mobile_export_delivery,
            mobile_files::mobile_open_external,
            mobile_files::mobile_background,
            mobile_files::mobile_appearance
        ])
        .run(tauri::generate_context!())
        .expect("Failed to start OpenNexus mobile");
}
