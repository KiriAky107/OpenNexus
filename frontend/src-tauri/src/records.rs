//! Versioned logical records: explicit fields only, never raw application databases/config.
use crate::workspace::{HostError, Result, Workspace};
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
pub fn path_for(kind: &str, id: &str) -> Result<String> {
    match (kind, id) {
        ("task", id) => path(id),
        ("theme_settings", "appearance") => {
            Ok("opennexus-records/v1/theme-settings/appearance.json".into())
        }
        ("preferences", "editor") => Ok("opennexus-records/v1/preferences/editor.json".into()),
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
    ) {
        return true;
    }
    path_value
        .strip_prefix("opennexus-records/v1/tasks/")
        .and_then(|v| v.strip_suffix(".json"))
        .is_some_and(|id| path(id).is_ok())
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
        if limit == 0 || limit > 1000 {
            return Err(HostError::new("RECORD_LIMIT_INVALID"));
        }
        let paths = self
            .sync_paths()?
            .into_iter()
            .filter(|path| path.starts_with("opennexus-records/v1/tasks/") && allowed(path))
            .collect::<Vec<_>>();
        let total = paths.len();
        let mut items = Vec::new();
        let mut bytes = 0;
        for path in paths.into_iter().skip(offset).take(limit) {
            let id = path
                .strip_prefix("opennexus-records/v1/tasks/")
                .and_then(|v| v.strip_suffix(".json"))
                .ok_or_else(|| HostError::new("RECORD_ID_INVALID"))?;
            if let Some(value) = self.record_get(id)? {
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
        Ok(Some(
            json!({"record":record,"deleted":receipt["result"]["deleted"],"state":receipt["state"]}),
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
}
