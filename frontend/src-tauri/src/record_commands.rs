//! Main-window preference records; no generic credential or application-state accessor.
use super::{with_workspace, Host};
use notesagent_host::{records, workspace::HostError};
use serde::Deserialize;
use serde_json::{json, Value};
use tauri::State;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Get {
    vault_id: String,
    kind: String,
    id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Write {
    vault_id: String,
    record: Value,
    expected: String,
    operation_id: String,
}
fn preference(kind: &str) -> Result<(), String> {
    if matches!(kind, "theme_settings" | "preferences") {
        Ok(())
    } else {
        Err("RECORD_SCOPE_DENIED".into())
    }
}
#[tauri::command]
pub fn record_get(host: State<'_, Host>, request: Get) -> Result<Option<Value>, String> {
    preference(&request.kind)?;
    with_workspace(&host, |ws| {
        if ws.vault_id != request.vault_id {
            return Err(HostError::new("VAULT_CHANGED"));
        }
        ws.record_get_kind(&request.kind, &request.id)
    })
}
#[tauri::command]
pub fn record_write(host: State<'_, Host>, request: Write) -> Result<Value, String> {
    let kind = request.record["kind"]
        .as_str()
        .ok_or("RECORD_SCHEMA_INVALID")?;
    preference(kind)?;
    let path =
        records::path_for(kind, request.record["id"].as_str().unwrap_or("")).map_err(|e| e.code)?;
    let bytes = serde_json::to_vec(&request.record).map_err(|_| "RECORD_SCHEMA_INVALID")?;
    with_workspace(&host, |ws| {
        if ws.vault_id != request.vault_id {
            return Err(HostError::new("VAULT_CHANGED"));
        }
        let entry = ws.write_operation(
            &path,
            &request.expected,
            &bytes,
            "local",
            &request.operation_id,
        )?;
        Ok(json!({"record":request.record,"hash":entry.hash,"file_id":entry.file_id}))
    })
}
