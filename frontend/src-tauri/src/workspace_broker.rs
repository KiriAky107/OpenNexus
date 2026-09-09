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

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecordRead {
    vault_id: String,
    id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecordDelete {
    vault_id: String,
    id: String,
    expected: String,
    operation_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecordWrite {
    vault_id: String,
    record: Value,
    expected: String,
    operation_id: String,
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
        "workspace.records.list" => {
            let p: List = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.record_list(p.offset, p.limit).map_err(|e| e.code)
        }
        "workspace.persona.get" => {
            let p: RecordRead = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.record_get_kind("persona", &p.id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.persona.write" => {
            let p: RecordWrite = decode(params)?;
            bound(ws, &p.vault_id)?;
            if p.record["kind"] != "persona" {
                return Err("RECORD_SCHEMA_UNSUPPORTED".into());
            }
            let path = crate::records::path_for("persona", p.record["id"].as_str().unwrap_or(""))
                .map_err(|e| e.code)?;
            let bytes = serde_json::to_vec(&p.record).map_err(|_| "RECORD_SCHEMA_INVALID")?;
            ws.write_operation(&path, &p.expected, &bytes, "local", &p.operation_id)
                .map_err(|e| e.code)?;
            ws.record_operation(&p.operation_id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.user_skills.list" => {
            let p: List = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.record_list_kind("user_skill", p.offset, p.limit)
                .map_err(|e| e.code)
        }
        "workspace.user_skills.get" => {
            let p: RecordRead = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.record_get_kind("user_skill", &p.id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.user_skills.write" => {
            let p: RecordWrite = decode(params)?;
            bound(ws, &p.vault_id)?;
            if p.record["kind"] != "user_skill" {
                return Err("RECORD_SCHEMA_UNSUPPORTED".into());
            }
            let path =
                crate::records::path_for("user_skill", p.record["id"].as_str().unwrap_or(""))
                    .map_err(|e| e.code)?;
            let bytes = serde_json::to_vec(&p.record).map_err(|_| "RECORD_SCHEMA_INVALID")?;
            ws.write_operation(&path, &p.expected, &bytes, "local", &p.operation_id)
                .map_err(|e| e.code)?;
            ws.record_operation(&p.operation_id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.user_skills.delete" => {
            let p: RecordDelete = decode(params)?;
            bound(ws, &p.vault_id)?;
            let path = crate::records::user_skill_path(&p.id).map_err(|e| e.code)?;
            ws.mutate_operation("delete", &path, "", &p.expected, &p.operation_id)
                .map_err(|e| e.code)?;
            ws.record_operation(&p.operation_id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.user_skills.operation" => {
            let p: Operation = decode(params)?;
            bound(ws, &p.vault_id)?;
            let value = ws.record_operation(&p.operation_id).map_err(|e| e.code)?;
            if value
                .as_ref()
                .is_some_and(|receipt| receipt["record"]["kind"] != "user_skill")
            {
                return Err("RECORD_OPERATION_DENIED".into());
            }
            Ok(value.unwrap_or(Value::Null))
        }
        "workspace.records.get" => {
            let p: RecordRead = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.record_get(&p.id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.records.operation" => {
            let p: Operation = decode(params)?;
            bound(ws, &p.vault_id)?;
            ws.record_operation(&p.operation_id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.records.delete" => {
            let p: RecordDelete = decode(params)?;
            bound(ws, &p.vault_id)?;
            let path = crate::records::path(&p.id).map_err(|e| e.code)?;
            ws.mutate_operation("delete", &path, "", &p.expected, &p.operation_id)
                .map_err(|e| e.code)?;
            ws.record_operation(&p.operation_id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
        "workspace.records.write" => {
            let p: RecordWrite = decode(params)?;
            bound(ws, &p.vault_id)?;
            let path =
                crate::records::path(p.record["id"].as_str().unwrap_or("")).map_err(|e| e.code)?;
            let bytes = serde_json::to_vec(&p.record).map_err(|_| "RECORD_SCHEMA_INVALID")?;
            ws.write_operation(&path, &p.expected, &bytes, "local", &p.operation_id)
                .map_err(|e| e.code)?;
            ws.record_operation(&p.operation_id)
                .map(|v| v.unwrap_or(Value::Null))
                .map_err(|e| e.code)
        }
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
    fn user_skills_are_vault_bound_listed_and_deleted_as_logical_records() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let id = "user_skill_00000000000000000000000000000001";
        let record = json!({"schema":1,"kind":"user_skill","id":id,"data":{"version":1,"name":"Review","description":"","prompt":"Review carefully","tools":["notes.read"],"permissions":["notes.read"],"retrieval":{"top_k":10,"rerank":true,"citation":true},"required_capabilities":["chat"],"created_at_ms":1,"updated_at_ms":1}});
        let operation_id = uuid::Uuid::new_v4().to_string();
        let write = json!({"rpc":"workspace.user_skills.write","params":{"vault_id":ws.vault_id,"record":record,"expected":"","operation_id":operation_id}});
        let receipt = dispatch(&mut ws, &write).unwrap();
        assert_eq!(receipt["expected"], "");
        assert_eq!(receipt["deleted"], false);
        assert_eq!(dispatch(&mut ws, &write).unwrap(), receipt);
        let list = json!({"rpc":"workspace.user_skills.list","params":{"vault_id":ws.vault_id,"offset":0,"limit":100}});
        let listed = dispatch(&mut ws, &list).unwrap();
        assert_eq!(listed["total"], 1);
        assert_eq!(listed["items"][0]["record"], record);
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let delete = json!({"rpc":"workspace.user_skills.delete","params":{"vault_id":ws.vault_id,"id":id,"expected":receipt["hash"],"operation_id":uuid::Uuid::new_v4().to_string()}});
        let deleted = dispatch(&mut ws, &delete).unwrap();
        assert_eq!(deleted["deleted"], true);
        assert_eq!(dispatch(&mut ws, &delete).unwrap(), deleted);
        assert_eq!(dispatch(&mut ws, &list).unwrap()["total"], 0);
        assert_eq!(ws.pending_count().unwrap(), 2);
        let persona = json!({"schema":1,"kind":"persona","id":"default","data":{"version":1,"name":"private","system_prompt":"","dialogue_pairs":[]}});
        let persona_operation = uuid::Uuid::new_v4().to_string();
        ws.write_operation(
            "opennexus-records/v1/persona/default.json",
            "",
            &serde_json::to_vec(&persona).unwrap(),
            "local",
            &persona_operation,
        )
        .unwrap();
        let smuggle = json!({"rpc":"workspace.user_skills.operation","params":{"vault_id":ws.vault_id,"operation_id":persona_operation}});
        assert_eq!(
            dispatch(&mut ws, &smuggle).unwrap_err(),
            "RECORD_OPERATION_DENIED"
        );
        let mut denied = write;
        denied["params"]["vault_id"] = json!("other-vault");
        denied["params"]["operation_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            dispatch(&mut ws, &denied).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
        assert_eq!(ws.pending_count().unwrap(), 3);
    }
    #[test]
    fn persona_is_vault_bound_durable_and_cas_protected() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let value = json!({"schema":1,"kind":"persona","id":"default","data":{"version":1,"name":"老师","system_prompt":"解释","dialogue_pairs":[{"user":"你好","assistant":"您好"}]}});
        let mut request = json!({"rpc":"workspace.persona.write","params":{"vault_id":ws.vault_id,"record":value,"expected":"","operation_id":uuid::Uuid::new_v4().to_string()}});
        let first = dispatch(&mut ws, &request).unwrap();
        assert_eq!(first["record"], value);
        assert_eq!(dispatch(&mut ws, &request).unwrap(), first);
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let read =
            json!({"rpc":"workspace.persona.get","params":{"vault_id":ws.vault_id,"id":"default"}});
        assert_eq!(dispatch(&mut ws, &read).unwrap()["hash"], first["hash"]);
        request["params"]["operation_id"] = json!(uuid::Uuid::new_v4().to_string());
        request["params"]["record"]["data"]["name"] = json!("不同人设");
        assert_eq!(
            dispatch(&mut ws, &request).unwrap_err(),
            "REVISION_CONFLICT"
        );
        request["params"]["expected"] = first["hash"].clone();
        request["params"]["record"]["data"]["api_key"] = json!("forbidden");
        assert_eq!(
            dispatch(&mut ws, &request).unwrap_err(),
            "RECORD_SCHEMA_INVALID"
        );
        request["params"]["record"]["data"]
            .as_object_mut()
            .unwrap()
            .remove("api_key");
        request["params"]["vault_id"] = json!("different-vault");
        assert_eq!(
            dispatch(&mut ws, &request).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
        assert_eq!(ws.pending_count().unwrap(), 1);
    }
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
