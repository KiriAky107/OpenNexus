//! 受限的 Core RPC；每个请求都绑定到 Host 传输捕获的 Vault。
use crate::workspace::Workspace;
use base64::{engine::general_purpose::STANDARD, Engine};
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
struct AssetWrite {
    vault_id: String,
    path: String,
    content_base64: String,
    operation_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AssetRead {
    vault_id: String,
    path: String,
}

const MAX_IMAGE_BYTES: usize = 5 * 1024 * 1024;

fn valid_asset_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/");
    let parts: Vec<_> = normalized.split('/').collect();
    parts.len() == 3
        && parts[0] == "attachments"
        && parts[1].len() == 2
        && !parts
            .iter()
            .any(|part| part.is_empty() || *part == "." || *part == "..")
        && ["png", "jpg", "gif", "webp"]
            .iter()
            .any(|suffix| normalized.ends_with(&format!(".{suffix}")))
}

fn valid_image_bytes(path: &str, bytes: &[u8]) -> bool {
    let extension = path.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    match extension.as_str() {
        "png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "jpg" | "jpeg" => bytes.starts_with(b"\xff\xd8\xff"),
        "gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "webp" => bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP",
        _ => false,
    }
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
        "workspace.assets.write" => {
            let p: AssetWrite = decode(params)?;
            bound(ws, &p.vault_id)?;
            if !valid_asset_path(&p.path) {
                return Err("WORKSPACE_REQUEST_INVALID".into());
            }
            let bytes = STANDARD
                .decode(&p.content_base64)
                .map_err(|_| "WORKSPACE_REQUEST_INVALID")?;
            if bytes.is_empty()
                || bytes.len() > MAX_IMAGE_BYTES
                || !valid_image_bytes(&p.path, &bytes)
                || crate::workspace::hash(&bytes)
                    != p.path
                        .split('/')
                        .last()
                        .unwrap_or("")
                        .split('.')
                        .next()
                        .unwrap_or("")
            {
                return Err("WORKSPACE_REQUEST_INVALID".into());
            }
            let target = ws.resolve(&p.path).map_err(|e| e.code)?;
            if target.exists() {
                let existing = std::fs::read(target).map_err(|_| "FILESYSTEM_ERROR")?;
                if existing != bytes {
                    return Err("REVISION_CONFLICT".into());
                }
                return Ok(
                    json!({"path":p.path,"hash":crate::workspace::hash(&bytes),"size":bytes.len()}),
                );
            }
            let entry = ws
                .write_operation(&p.path, "", &bytes, "local", &p.operation_id)
                .map_err(|e| e.code)?;
            Ok(json!({"path":entry.path,"hash":entry.hash,"size":bytes.len()}))
        }
        "workspace.assets.read" => {
            let p: AssetRead = decode(params)?;
            bound(ws, &p.vault_id)?;
            let normalized = p.path.replace('\\', "/");
            if normalized.split('/').any(|part| part.is_empty() || part.starts_with('.') || part.eq_ignore_ascii_case("opennexus-records"))
                || !["png", "jpg", "jpeg", "gif", "webp"].iter().any(|ext| normalized.to_ascii_lowercase().ends_with(&format!(".{ext}"))) {
                return Err("WORKSPACE_REQUEST_INVALID".into());
            }
            let file = std::fs::File::open(ws.resolve(&normalized).map_err(|e| e.code)?).map_err(|_| "FILE_NOT_FOUND")?;
            if file.metadata().map_err(|_| "FILE_NOT_FOUND")?.len() > MAX_IMAGE_BYTES as u64 { return Err("CORE_NOTE_TOO_LARGE".into()); }
            use std::io::Read;
            let mut bytes = Vec::new();
            file.take(MAX_IMAGE_BYTES as u64 + 1).read_to_end(&mut bytes).map_err(|_| "FILE_NOT_FOUND")?;
            if bytes.len() > MAX_IMAGE_BYTES {
                return Err("CORE_NOTE_TOO_LARGE".into());
            }
            if !valid_image_bytes(&normalized, &bytes) { return Err("WORKSPACE_IMAGE_UNSUPPORTED".into()); }
            Ok(json!({"content_base64":STANDARD.encode(bytes)}))
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
    fn workspace_images_are_binary_content_addressed_and_vault_bound() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let bytes = b"\x89PNG\r\n\x1a\nfixture";
        let digest = crate::workspace::hash(bytes);
        let path = format!("attachments/{}/{}.png", &digest[..2], digest);
        let write = json!({"rpc":"workspace.assets.write","params":{
            "vault_id":ws.vault_id,"path":path,"content_base64":STANDARD.encode(bytes),
            "operation_id":uuid::Uuid::new_v4().to_string()
        }});
        let stored = dispatch(&mut ws, &write).unwrap();
        assert_eq!(stored["hash"], digest);
        assert_eq!(dispatch(&mut ws, &write).unwrap()["hash"], digest);
        let read =
            json!({"rpc":"workspace.assets.read","params":{"vault_id":ws.vault_id,"path":path}});
        assert_eq!(
            STANDARD
                .decode(
                    dispatch(&mut ws, &read).unwrap()["content_base64"]
                        .as_str()
                        .unwrap()
                )
                .unwrap(),
            bytes
        );
        let mut denied = write;
        denied["params"]["vault_id"] = json!("other-vault");
        assert_eq!(
            dispatch(&mut ws, &denied).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
    }
    #[test]
    fn ordinary_images_are_readable_in_tree_but_not_indexed_as_notes() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        std::fs::create_dir(root.path().join("附件")).unwrap();
        let bytes = b"\x89PNG\r\n\x1a\nfixture";
        std::fs::write(root.path().join("附件/logo.png"), bytes).unwrap();
        let read = json!({"rpc":"workspace.assets.read","params":{"vault_id":ws.vault_id,"path":"附件/logo.png"}});
        assert_eq!(STANDARD.decode(dispatch(&mut ws, &read).unwrap()["content_base64"].as_str().unwrap()).unwrap(), bytes);
        assert!(ws.tree().unwrap().iter().any(|e| e.path == "附件/logo.png" && !e.is_folder));
        assert!(!ws.scan().unwrap().iter().any(|e| e.path.ends_with(".png")));
        std::fs::write(root.path().join("附件/logo.png"), b"not an image").unwrap();
        assert!(dispatch(&mut ws, &read).is_err());
        for path in ["../logo.png", ".ainote/secret.png", "opennexus-records/secret.png", "C:/secret.png"] {
            let denied = json!({"rpc":"workspace.assets.read","params":{"vault_id":ws.vault_id,"path":path}});
            assert!(dispatch(&mut ws, &denied).is_err());
        }
    }
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
