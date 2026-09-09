//! 主窗口偏好记录；没有通用凭证或应用程序状态访问器。
use super::{with_workspace, Host};
use notesagent_host::{
    records,
    workspace::{HostError, Result as HostResult, Workspace},
};
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
fn preference(kind: &str) -> HostResult<()> {
    if matches!(kind, "theme_settings" | "preferences" | "layout") {
        Ok(())
    } else {
        Err(HostError::new("RECORD_SCOPE_DENIED"))
    }
}
fn get_record(ws: &mut Workspace, request: Get) -> HostResult<Option<Value>> {
    preference(&request.kind)?;
    if ws.vault_id != request.vault_id {
        return Err(HostError::new("VAULT_CHANGED"));
    }
    ws.record_get_kind(&request.kind, &request.id)
}

fn write_record(ws: &mut Workspace, request: Write) -> HostResult<Value> {
    let kind = request.record["kind"]
        .as_str()
        .ok_or_else(|| HostError::new("RECORD_SCHEMA_INVALID"))?;
    preference(kind)?;
    if ws.vault_id != request.vault_id {
        return Err(HostError::new("VAULT_CHANGED"));
    }
    let path = records::path_for(kind, request.record["id"].as_str().unwrap_or(""))?;
    let bytes =
        serde_json::to_vec(&request.record).map_err(|_| HostError::new("RECORD_SCHEMA_INVALID"))?;
    let entry = ws.write_operation(
        &path,
        &request.expected,
        &bytes,
        "local",
        &request.operation_id,
    )?;
    Ok(json!({"record":request.record,"hash":entry.hash,"file_id":entry.file_id}))
}

#[tauri::command]
pub fn record_get(host: State<'_, Host>, request: Get) -> Result<Option<Value>, String> {
    with_workspace(&host, |ws| get_record(ws, request))
}
#[tauri::command]
pub fn record_write(host: State<'_, Host>, request: Write) -> Result<Value, String> {
    with_workspace(&host, |ws| write_record(ws, request))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn layout_traverses_the_actual_command_policy_and_workspace_journal() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let record = json!({"schema":1,"kind":"layout","id":"sidebars","data":{"primaryExpanded":true,"workspaceWidth":400,"chatWidth":320}});
        let operation = uuid::Uuid::new_v4().to_string();
        let make = |vault: &str| Write {
            vault_id: vault.into(),
            record: record.clone(),
            expected: String::new(),
            operation_id: operation.clone(),
        };
        let vault = ws.vault_id.clone();
        let first = write_record(&mut ws, make(&vault)).unwrap();
        assert_eq!(write_record(&mut ws, make(&vault)).unwrap(), first);
        assert_eq!(ws.pending_count().unwrap(), 1);
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let loaded = get_record(
            &mut ws,
            Get {
                vault_id: vault,
                kind: "layout".into(),
                id: "sidebars".into(),
            },
        )
        .unwrap()
        .unwrap();
        assert_eq!(loaded, first);
        assert_eq!(
            write_record(&mut ws, make("different-vault"))
                .unwrap_err()
                .code,
            "VAULT_CHANGED"
        );
        assert_eq!(ws.pending_count().unwrap(), 1);
    }
    #[test]
    fn preference_commands_do_not_become_a_generic_record_accessor() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        for kind in ["persona", "task", "credentials", "user_skill"] {
            let request = Get {
                vault_id: ws.vault_id.clone(),
                kind: kind.into(),
                id: "default".into(),
            };
            assert_eq!(
                get_record(&mut ws, request).unwrap_err().code,
                "RECORD_SCOPE_DENIED"
            );
        }
        let request = Write {
            vault_id: ws.vault_id.clone(),
            record: json!({"schema":1,"kind":"layout","id":"sidebars","data":{"primaryExpanded":true,"workspaceWidth":400,"chatWidth":320,"token":"forbidden"}}),
            expected: String::new(),
            operation_id: uuid::Uuid::new_v4().to_string(),
        };
        assert_eq!(
            write_record(&mut ws, request).unwrap_err().code,
            "RECORD_SCHEMA_INVALID"
        );
        assert_eq!(ws.pending_count().unwrap(), 0);
    }
}
