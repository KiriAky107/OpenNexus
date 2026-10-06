//! Main-window-only application of reviewed installed package data.
use crate::{extension_commands::main_window, Host};
use notesagent_host::extension_candidates::{persona_apply, persona_preview, PersonaApply};
use notesagent_host::extension_template_apply::{
    template_apply_file, template_preview, TemplateFileApply,
};
use serde::Deserialize;
use serde_json::Value;
use tauri::{Manager, State, WebviewWindow};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewRequest {
    vault_id: String,
    slot: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplyRequest {
    request_id: String,
    application: PersonaApply,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TemplatePreviewRequest {
    vault_id: String,
    slot: String,
    directory: String,
    note_path: Option<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TemplateApplyRequest {
    request_id: String,
    application: TemplateFileApply,
}
#[tauri::command]
pub async fn extension_template_preview(
    window: WebviewWindow,
    _host: State<'_, Host>,
    request: TemplatePreviewRequest,
) -> Result<Value, String> {
    main_window(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let host = app.state::<Host>();
        let mut workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
        let ws = workspace.as_mut().ok_or("VAULT_NOT_OPEN")?;
        if ws.vault_id != request.vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let mut extensions = host.extensions.lock().map_err(|_| "HOST_BUSY")?;
        let store = extensions.as_mut().ok_or("EXTENSIONS_NOT_READY")?;
        tauri::async_runtime::block_on(
            store.candidate_package_online(&request.slot, &request.vault_id),
        )
        .map_err(|error| error.code)?;
        serde_json::to_value(
            template_preview(
                store,
                ws,
                &request.slot,
                &request.directory,
                request.note_path.as_deref(),
            )
            .map_err(|error| error.code)?,
        )
        .map_err(|_| "EXTENSION_TEMPLATE_INVALID".into())
    })
    .await
    .map_err(|_| "EXTENSION_TEMPLATE_INVALID".to_string())?
}
#[tauri::command]
pub async fn extension_template_apply(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: TemplateApplyRequest,
) -> Result<Value, String> {
    main_window(&window)?;
    let mut lease = host.extension_requests.claim(&request.request_id)?;
    let checkpoint = lease.checkpoint();
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let host = app.state::<Host>();
        let _activation = host.extension_activation.lock().map_err(|_| "HOST_BUSY")?;
        let mut workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
        let ws = workspace.as_mut().ok_or("VAULT_NOT_OPEN")?;
        let application = request.application;
        if ws.vault_id != application.vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let mut extensions = host.extensions.lock().map_err(|_| "HOST_BUSY")?;
        let store = extensions.as_mut().ok_or("EXTENSIONS_NOT_READY")?;
        let verified = tauri::async_runtime::block_on(lease.run(async {
            checkpoint()?;
            store
                .candidate_package_online(&application.slot, &application.vault_id)
                .await
                .map_err(|error| error.code)
        }))?;
        template_apply_file(store, ws, &application, || {
            checkpoint().map_err(|code| notesagent_host::workspace::HostError::new(&code))?;
            verified.ensure_fresh()
        })
        .map_err(|error| error.code)
    })
    .await
    .map_err(|_| "EXTENSION_TEMPLATE_INVALID".to_string())?
}
#[tauri::command]
pub async fn extension_persona_preview(
    window: WebviewWindow,
    _host: State<'_, Host>,
    request: PreviewRequest,
) -> Result<Value, String> {
    main_window(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let host = app.state::<Host>();
        let mut workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
        let ws = workspace.as_mut().ok_or("VAULT_NOT_OPEN")?;
        if ws.vault_id != request.vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let mut extensions = host.extensions.lock().map_err(|_| "HOST_BUSY")?;
        let store = extensions.as_mut().ok_or("EXTENSIONS_NOT_READY")?;
        tauri::async_runtime::block_on(
            store.candidate_package_online(&request.slot, &request.vault_id),
        )
        .map_err(|e| e.code)?;
        serde_json::to_value(persona_preview(store, ws, &request.slot).map_err(|e| e.code)?)
            .map_err(|_| "EXTENSION_CANDIDATE_INVALID".into())
    })
    .await
    .map_err(|_| "EXTENSION_CANDIDATE_INVALID".to_string())?
}
#[tauri::command]
pub async fn extension_persona_apply(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: ApplyRequest,
) -> Result<Value, String> {
    main_window(&window)?;
    let mut lease = host.extension_requests.claim(&request.request_id)?;
    let checkpoint = lease.checkpoint();
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let host = app.state::<Host>();
        let _activation = host.extension_activation.lock().map_err(|_| "HOST_BUSY")?;
        let mut workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
        let ws = workspace.as_mut().ok_or("VAULT_NOT_OPEN")?;
        let application = request.application;
        if ws.vault_id != application.vault_id {
            return Err("VAULT_CHANGED".into());
        }
        let mut extensions = host.extensions.lock().map_err(|_| "HOST_BUSY")?;
        let store = extensions.as_mut().ok_or("EXTENSIONS_NOT_READY")?;
        let verified = tauri::async_runtime::block_on(lease.run(async {
            checkpoint()?;
            store
                .candidate_package_online(&application.slot, &application.vault_id)
                .await
                .map_err(|e| e.code)
        }))?;
        persona_apply(store, ws, &application, || {
            checkpoint().map_err(|code| notesagent_host::workspace::HostError::new(&code))?;
            verified.ensure_fresh()
        })
        .map_err(|e| e.code)
    })
    .await
    .map_err(|_| "EXTENSION_CANDIDATE_INVALID".to_string())?
}
