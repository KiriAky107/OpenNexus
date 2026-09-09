//! 版本化逻辑记录：仅显式字段，从不原始应用程序数据库/配置。
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskData {
    pub title: String,
    pub description: String,
    pub status: String,
    pub note_id: Option<String>,
    pub due_at_ms: Option<i64>,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub schema: u32,
    pub kind: String,
    pub id: String,
    pub data: Value,
}
pub fn path(id: &str) -> Result<String> {
    if !id.starts_with("task_")
        || id.len() != 37
        || !id[5..]
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err(HostError::new("RECORD_ID_INVALID"));
    }
    Ok(format!("opennexus-records/v1/tasks/{id}.json"))
}

pub fn user_skill_path(id: &str) -> Result<String> {
    if !id.starts_with("user_skill_")
        || id.len() != 43
        || !id[11..]
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err(HostError::new("RECORD_ID_INVALID"));
    }
    Ok(format!("opennexus-records/v1/user-skills/{id}.json"))
}
fn portable_id_path(id: &str, prefix: &str, directory: &str) -> Result<String> {
    if !id.starts_with(prefix)
        || id.len() != prefix.len() + 32
        || !id[prefix.len()..]
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err(HostError::new("RECORD_ID_INVALID"));
    }
    Ok(format!("opennexus-records/v1/{directory}/{id}.json"))
}
pub fn path_for(kind: &str, id: &str) -> Result<String> {
    match (kind, id) {
        ("task", id) => path(id),
        ("theme_settings", "appearance") => {
            Ok("opennexus-records/v1/theme-settings/appearance.json".into())
        }
        ("persona", "default") => Ok("opennexus-records/v1/persona/default.json".into()),
        ("layout", "sidebars") => Ok("opennexus-records/v1/layout/sidebars.json".into()),
        ("preferences", "editor") => Ok("opennexus-records/v1/preferences/editor.json".into()),
        ("user_skill", id) => user_skill_path(id),
        ("conversation", id) => portable_id_path(id, "conversation_", "conversations"),
        ("agent_history", id) => portable_id_path(id, "agent_run_", "agent-history"),
        ("provider_settings", id) => portable_id_path(id, "provider_", "provider-settings"),
        ("extension_installation", id) => {
            portable_id_path(id, "extension_", "extension-installations")
        }
        _ => Err(HostError::new("RECORD_ID_INVALID")),
    }
}
pub fn is_record(path: &str) -> bool {
    path.starts_with("opennexus-records/")
}
pub fn allowed(path_value: &str) -> bool {
    if matches!(
        path_value,
        "opennexus-records/v1/theme-settings/appearance.json"
            | "opennexus-records/v1/preferences/editor.json"
            | "opennexus-records/v1/layout/sidebars.json"
            | "opennexus-records/v1/persona/default.json"
    ) {
        return true;
    }
    if path_value
        .strip_prefix("opennexus-records/v1/tasks/")
        .and_then(|v| v.strip_suffix(".json"))
        .is_some_and(|id| path(id).is_ok())
    {
        return true;
    }
    if path_value
        .strip_prefix("opennexus-records/v1/user-skills/")
        .and_then(|v| v.strip_suffix(".json"))
        .is_some_and(|id| user_skill_path(id).is_ok())
    {
        return true;
    }
    [
        ("conversation", "opennexus-records/v1/conversations/"),
        ("agent_history", "opennexus-records/v1/agent-history/"),
        (
            "provider_settings",
            "opennexus-records/v1/provider-settings/",
        ),
        (
            "extension_installation",
            "opennexus-records/v1/extension-installations/",
        ),
    ]
    .iter()
    .any(|(kind, prefix)| {
        path_value
            .strip_prefix(prefix)
            .and_then(|value| value.strip_suffix(".json"))
            .is_some_and(|id| path_for(kind, id).is_ok())
    })
}
pub fn validate(path_value: &str, content: &[u8]) -> Result<Record> {
    if content.len() > 1024 * 1024 {
        return Err(HostError::new("RECORD_TOO_LARGE"));
    }
    let record: Record =
        serde_json::from_slice(content).map_err(|_| HostError::new("RECORD_SCHEMA_INVALID"))?;
    let time = |value: i64| (0..=253402300799999).contains(&value);
    if record.schema != 1 || path_for(&record.kind, &record.id)? != path_value {
        return Err(HostError::new("RECORD_SCHEMA_UNSUPPORTED"));
    }
    if record.kind != "task" {
        crate::preference_records::validate(&record.kind, &record.data)?;
        return Ok(record);
    }
    let data: TaskData = serde_json::from_value(record.data.clone())
        .map_err(|_| HostError::new("RECORD_SCHEMA_INVALID"))?;
    if data.title.trim().is_empty()
        || data.title.len() > 4096
        || data.description.len() > 262144
        || !matches!(
            data.status.as_str(),
            "todo" | "in_progress" | "done" | "cancelled"
        )
        || !time(data.created_at_ms)
        || !time(data.updated_at_ms)
        || data.due_at_ms.is_some_and(|v| !time(v))
        || data.note_id.as_ref().is_some_and(|v| {
            v.is_empty()
                || v.len() > 128
                || !v
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        })
    {
        return Err(HostError::new("RECORD_DATA_INVALID"));
    }
    Ok(record)
}
impl Workspace {
    pub(crate) fn normalize_record_links(&mut self) -> Result<()> {
        for path in self
            .sync_paths()?
            .into_iter()
            .filter(|v| v.starts_with("opennexus-records/v1/tasks/") && allowed(v))
        {
            let bytes = std::fs::read(self.resolve(&path)?)?;
            let original = validate(&path, &bytes)?;
            if let Some(note_id) = original.data["note_id"].as_str() {
                if let Ok(note_path) = self.path_for_id(note_id) {
                    if let Some(entry) = self.entry(&note_path)? {
                        if entry.file_id != note_id {
                            let mut record = original;
                            record.data["note_id"] = json!(entry.file_id);
                            self.write(
                                &path,
                                &crate::workspace::hash(&bytes),
                                &serde_json::to_vec(&record)
                                    .map_err(|_| HostError::new("RECORD_SCHEMA_INVALID"))?,
                                "local",
                            )?;
                        }
                    }
                }
            }
        }
        Ok(())
    }
    pub fn record_get(&mut self, id: &str) -> Result<Option<Value>> {
        self.record_get_kind("task", id)
    }
    pub fn record_get_kind(&mut self, kind: &str, id: &str) -> Result<Option<Value>> {
        let path = path_for(kind, id)?;
        if !self.resolve(&path)?.is_file() {
            return Ok(None);
        }
        if std::fs::metadata(self.resolve(&path)?)?.len() > 1024 * 1024 {
            return Err(HostError::new("RECORD_TOO_LARGE"));
        }
        let document = self.read(&path)?;
        let mut record = validate(&path, document.content.as_bytes())?;
        if let Some(note_id) = record.data["note_id"].as_str() {
            if let Ok(path) = self.path_for_id(note_id) {
                if let Some(entry) = self.entry(&path)? {
                    record.data["note_id"] = json!(entry.file_id);
                }
            }
        }
        Ok(Some(
            json!({"record":record,"hash":document.entry.hash,"file_id":document.entry.file_id}),
        ))
    }
    pub fn record_list(&mut self, offset: usize, limit: usize) -> Result<Value> {
        self.record_list_kind("task", offset, limit)
    }
    pub fn record_list_kind(&mut self, kind: &str, offset: usize, limit: usize) -> Result<Value> {
        if limit == 0 || limit > 1000 {
            return Err(HostError::new("RECORD_LIMIT_INVALID"));
        }
        let prefix = match kind {
            "task" => "opennexus-records/v1/tasks/",
            "user_skill" => "opennexus-records/v1/user-skills/",
            "conversation" => "opennexus-records/v1/conversations/",
            "agent_history" => "opennexus-records/v1/agent-history/",
            "provider_settings" => "opennexus-records/v1/provider-settings/",
            "extension_installation" => "opennexus-records/v1/extension-installations/",
            _ => return Err(HostError::new("RECORD_SCHEMA_UNSUPPORTED")),
        };
        let paths = self
            .sync_paths()?
            .into_iter()
            .filter(|path| path.starts_with(prefix) && allowed(path))
            .collect::<Vec<_>>();
        let total = paths.len();
        let mut items = Vec::new();
        let mut bytes = 0;
        for path in paths.into_iter().skip(offset).take(limit) {
            let id = path
                .strip_prefix(prefix)
                .and_then(|v| v.strip_suffix(".json"))
                .ok_or_else(|| HostError::new("RECORD_ID_INVALID"))?;
            if let Some(value) = self.record_get_kind(kind, id)? {
                let size = serde_json::to_vec(&value)
                    .map_err(|_| HostError::new("RECORD_SCHEMA_INVALID"))?
                    .len();
                if bytes + size > 4 * 1024 * 1024 {
                    break;
                }
                bytes += size;
                items.push(value);
            }
        }
        Ok(json!({"items":items,"total":total}))
    }
    pub fn record_operation(&mut self, operation: &str) -> Result<Option<Value>> {
        self.recover()?;
        let Some(receipt) = self.operation(operation)? else {
            return Ok(None);
        };
        let path = receipt["result"]["path"]
            .as_str()
            .ok_or_else(|| HostError::new("RECORD_OPERATION_PENDING"))?;
        if !allowed(path) {
            return Err(HostError::new("RECORD_OPERATION_DENIED"));
        }
        let bytes = self.payload(operation, &[])?;
        let record = validate(path, &bytes)?;
        let expected = receipt["result"]["expected"]
            .as_str()
            .map(str::to_owned)
            .or(self
                .db
                .query_row(
                    "SELECT expected FROM journal WHERE operation_id=?1",
                    [operation],
                    |row| row.get::<_, String>(0),
                )
                .optional()?)
            .or(self
                .db
                .query_row(
                    "SELECT hash FROM file_ops WHERE id=?1",
                    [operation],
                    |row| row.get::<_, String>(0),
                )
                .optional()?);
        Ok(Some(
            json!({"record":record,"hash":crate::workspace::hash(&bytes),"expected":expected,"deleted":receipt["result"]["deleted"],"state":receipt["state"]}),
        ))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value {
        json!({"schema":1,"kind":"task","id":"task_00000000000000000000000000000001","data":{"title":"Task","description":"","status":"todo","note_id":null,"due_at_ms":null,"created_at_ms":0,"updated_at_ms":0}})
    }
    #[test]
    fn whitelist_rejects_secret_unknown_fields_and_future_schema_before_journal() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let value = fixture();
        let path = path(value["id"].as_str().unwrap()).unwrap();
        for field in ["api_key", "token", "environment", "permissions", "extra"] {
            let mut value = value.clone();
            value["data"][field] = json!("planted-secret");
            assert_eq!(
                ws.write(&path, "", &serde_json::to_vec(&value).unwrap(), "local")
                    .unwrap_err()
                    .code,
                "RECORD_SCHEMA_INVALID"
            );
        }
        let mut future = value.clone();
        future["schema"] = json!(2);
        assert_eq!(
            ws.write(&path, "", &serde_json::to_vec(&future).unwrap(), "remote")
                .unwrap_err()
                .code,
            "RECORD_SCHEMA_UNSUPPORTED"
        );
        assert!(!root.path().join(&path).exists());
        assert_eq!(ws.pending_count().unwrap(), 0);
        let operation = uuid::Uuid::new_v4().to_string();
        ws.write_operation(
            &path,
            "",
            &serde_json::to_vec(&value).unwrap(),
            "local",
            &operation,
        )
        .unwrap();
        assert_eq!(
            ws.record_operation(&operation).unwrap().unwrap()["record"],
            value
        );
    }

    #[test]
    fn s08_incompatible_remote_schema_preserves_local_file_and_upload_queue() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let current = fixture();
        let path = path(current["id"].as_str().unwrap()).unwrap();
        let entry = ws
            .write(&path, "", &serde_json::to_vec(&current).unwrap(), "local")
            .unwrap();
        assert_eq!(ws.pending_count().unwrap(), 1);

        let mut future = current.clone();
        future["schema"] = json!(2);
        future["data"]["title"] = json!("future remote value");
        assert_eq!(
            ws.write(
                &path,
                &entry.hash,
                &serde_json::to_vec(&future).unwrap(),
                "remote",
            )
            .unwrap_err()
            .code,
            "RECORD_SCHEMA_UNSUPPORTED"
        );
        assert_eq!(ws.pending_count().unwrap(), 1);
        assert_eq!(
            ws.record_get(current["id"].as_str().unwrap())
                .unwrap()
                .unwrap()["record"],
            current
        );
    }
}
