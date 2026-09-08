//! Narrow Core RPC. Every request is bound to the Vault captured by the Host transport.
use crate::workspace::Workspace;
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Read {
    vault_id: String,
    file_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct List {
    vault_id: String,
    offset: usize,
    limit: usize,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Write {
    vault_id: String,
    path: String,
    expected: String,
    content: String,
    operation_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Operation {
    vault_id: String,
    operation_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Mutation {
    vault_id: String,
    path: String,
    destination: String,
    expected: String,
    operation_id: String,
    kind: String,
}

fn bound(ws: &Workspace, vault_id: &str) -> Result<(), String> {
    if ws.vault_id != vault_id {
        return Err("VAULT_PERMISSION_CHANGED".into());
    }
    Ok(())
}
fn decode<T: serde::de::DeserializeOwned>(value: &Value) -> Result<T, String> {
    serde_json::from_value(value.clone()).map_err(|_| "WORKSPACE_REQUEST_INVALID".into())
}
pub fn dispatch(ws: &mut Workspace, request: &Value) -> Result<Value, String> {
    let params = &request["params"];
    match request["rpc"].as_str().unwrap_or_default() {
        "workspace.list" => {
            let p: List = decode(params)?;
            bound(ws, &p.vault_id)?;
            if !(1..=1000).contains(&p.limit) {
                return Err("WORKSPACE_REQUEST_INVALID".into());
            }
            let mut entries = ws.scan().map_err(|e| e.code)?;
            entries.retain(|e| !e.deleted && !e.is_folder);
            entries.sort_by(|a, b| a.path.cmp(&b.path));
            let total = entries.len();
            let items = entries
                .into_iter()
                .skip(p.offset)
                .take(p.limit)
                .map(|entry| {
                    let aliases = ws.aliases_for_id(&entry.file_id).map_err(|e| e.code)?;
                    let mut value = serde_json::to_value(entry)
                        .map_err(|_| "HOST_SERIALIZE_FAILED".to_owned())?;
                    value["aliases"] = json!(aliases);
                    Ok(value)
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok(json!({"items":items,"total":total}))
        }
        "workspace.read" => {
            let p: Read = decode(params)?;
            bound(ws, &p.vault_id)?;
            let relative = ws.path_for_id(&p.file_id).map_err(|e| e.code)?;
            let path = ws.resolve(&relative).map_err(|e| e.code)?;
            let metadata = std::fs::metadata(path).map_err(|_| "FILESYSTEM_ERROR")?;
            if metadata.len() > 1024 * 1024 {
                return Err("CORE_NOTE_TOO_LARGE".into());
            }
            let document = ws.read(&relative).map_err(|e| e.code)?;
            let timestamp = |value: std::io::Result<std::time::SystemTime>| {
                value
                    .ok()
                    .and_then(|v| v.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|v| v.as_secs())
                    .unwrap_or(0)
            };
            let mut value = serde_json::to_value(document).map_err(|_| "HOST_SERIALIZE_FAILED")?;
            value["created_at"] = json!(timestamp(metadata.created()));
            value["updated_at"] = json!(timestamp(metadata.modified()));
            Ok(value)
        }
        "workspace.write" => {
            let p: Write = decode(params)?;
            bound(ws, &p.vault_id)?;
            if p.content.len() > 1024 * 1024 || !p.path.to_ascii_lowercase().ends_with(".md") {
                return Err("CORE_NOTE_TOO_LARGE".into());
            }
            let entry = ws
                .write_operation(
                    &p.path,
                    &p.expected,
                    p.content.as_bytes(),
                    "local",
                    &p.operation_id,
                )
                .map_err(|e| e.code)?;
            Ok(json!({"operation_id":p.operation_id,"state":"committed","result":entry}))
        }
        "workspace.operation" => {
            let p: Operation = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.operation(&p.operation_id)
                .map_err(|e| e.code)?
                .ok_or("OPERATION_NOT_FOUND".into())
        }
        "workspace.mutate" => {
            let p: Mutation = decode(params)?;
            bound(ws, &p.vault_id)?;
            if !p.path.to_ascii_lowercase().ends_with(".md")
                || (p.kind == "rename" && !p.destination.to_ascii_lowercase().ends_with(".md"))
            {
                return Err("WORKSPACE_REQUEST_INVALID".into());
            }
            ws.mutate_operation(
                &p.kind,
                &p.path,
                &p.destination,
                &p.expected,
                &p.operation_id,
            )
            .map_err(|e| e.code)
        }
        _ => Err("HOST_METHOD_DENIED".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_stale_vault_and_unowned_fields_before_writes() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let mut request = json!({"rpc":"workspace.write","params":{"vault_id":"another-vault","path":"a.md","expected":"","content":"test","operation_id":uuid::Uuid::new_v4().to_string()}});
        assert_eq!(
            dispatch(&mut ws, &request).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
        request["params"]["vault_id"] = json!(ws.vault_id);
        request["params"]["origin"] = json!("remote");
        assert_eq!(
            dispatch(&mut ws, &request).unwrap_err(),
            "WORKSPACE_REQUEST_INVALID"
        );
        assert!(!root.path().join("a.md").exists());
        request["params"].as_object_mut().unwrap().remove("origin");
        assert_eq!(dispatch(&mut ws, &request).unwrap()["state"], "committed");
        assert_eq!(ws.pending_count().unwrap(), 1);
    }
}
