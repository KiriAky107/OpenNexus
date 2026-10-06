//! Source-file RPC only. Running and importing remain separate main-window decisions.
use crate::{
    experiment_contract::is_experiment_file,
    experiment_input::{open_root, read_selected, valid_path, MAX_FILE_BYTES},
    workspace::{hash, Entry, Workspace},
};
use base64::{engine::general_purpose::STANDARD, Engine};
use rusqlite::{params, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Value};

pub(crate) const SCHEMA: &str = "CREATE TABLE IF NOT EXISTS experiment_file_writes (
    operation_id TEXT PRIMARY KEY, binding_hash TEXT NOT NULL);";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct List {
    vault_id: String,
    offset: usize,
    limit: usize,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Read {
    vault_id: String,
    file_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    vault_id: String,
    path: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Write {
    vault_id: String,
    path: String,
    expected: String,
    expected_file_id: Option<String>,
    expected_revision: Option<i64>,
    content_base64: String,
    operation_id: String,
}

fn bound(ws: &Workspace, vault: &str) -> Result<(), String> {
    if ws.vault_id != vault {
        return Err("VAULT_PERMISSION_CHANGED".into());
    }
    Ok(())
}
fn decode<T: serde::de::DeserializeOwned>(v: &Value) -> Result<T, String> {
    serde_json::from_value(v.clone()).map_err(|_| "WORKSPACE_REQUEST_INVALID".into())
}
fn entries(ws: &mut Workspace) -> Result<Vec<Entry>, String> {
    Ok(ws
        .scan()
        .map_err(|e| e.code)?
        .into_iter()
        .filter(|e| !e.deleted && !e.is_folder && is_experiment_file(&e.path))
        .collect())
}
fn snapshot(ws: &mut Workspace, entry: Entry) -> Result<Value, String> {
    if !valid_path(&entry.path) {
        return Err("EXPERIMENT_REQUEST_INVALID".into());
    }
    let root = open_root(&ws.root).map_err(|e| e.code)?;
    let bytes = read_selected(&root, &entry.path).map_err(|e| e.code)?;
    if hash(&bytes) != entry.hash {
        return Err("EXPERIMENT_INPUT_CHANGED".into());
    }
    std::str::from_utf8(&bytes).map_err(|_| "EXPERIMENT_INPUT_NOT_UTF8")?;
    Ok(json!({"entry":entry,"content_base64":STANDARD.encode(&bytes),"byte_size":bytes.len()}))
}
pub(crate) fn dispatch(ws: &mut Workspace, request: &Value) -> Result<Value, String> {
    let params = &request["params"];
    match request["rpc"].as_str().unwrap_or_default() {
        "workspace.experiments.list" => {
            let p: List = decode(params)?;
            bound(ws, &p.vault_id)?;
            if !(1..=100).contains(&p.limit) {
                return Err("WORKSPACE_REQUEST_INVALID".into());
            }
            let mut all = entries(ws)?;
            all.sort_by(|a, b| a.path.cmp(&b.path));
            let total = all.len();
            let items: Vec<_> = all.into_iter().skip(p.offset).take(p.limit).collect();
            Ok(json!({"items":items,"total":total}))
        }
        "workspace.experiments.read" => {
            let p: Read = decode(params)?;
            bound(ws, &p.vault_id)?;
            let entry = entries(ws)?
                .into_iter()
                .find(|e| e.file_id == p.file_id)
                .ok_or("FILE_NOT_FOUND")?;
            snapshot(ws, entry)
        }
        "workspace.experiments.snapshot" => {
            let p: Snapshot = decode(params)?;
            bound(ws, &p.vault_id)?;
            if !valid_path(&p.path) {
                return Err("EXPERIMENT_REQUEST_INVALID".into());
            }
            match entries(ws)?.into_iter().find(|e| e.path == p.path) {
                Some(entry) => snapshot(ws, entry),
                None => Ok(Value::Null),
            }
        }
        "workspace.experiments.write" => {
            let p: Write = decode(params)?;
            bound(ws, &p.vault_id)?;
            if !valid_path(&p.path)
                || p.content_base64.len() > (MAX_FILE_BYTES as usize).div_ceil(3) * 4
                || (p.expected.is_empty()
                    && (p.expected_file_id.is_some() || p.expected_revision.is_some()))
                || (!p.expected.is_empty()
                    && (p.expected.len() != 64
                        || !p
                            .expected
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                        || p.expected_file_id
                            .as_deref()
                            .is_none_or(|v| uuid::Uuid::parse_str(v).is_err())
                        || p.expected_revision.is_none_or(|v| v <= 0)))
            {
                return Err("EXPERIMENT_REQUEST_INVALID".into());
            }
            let bytes = STANDARD
                .decode(&p.content_base64)
                .map_err(|_| "EXPERIMENT_REQUEST_INVALID")?;
            if bytes.len() > MAX_FILE_BYTES as usize {
                return Err("EXPERIMENT_INPUT_TOO_LARGE".into());
            }
            std::str::from_utf8(&bytes).map_err(|_| "EXPERIMENT_INPUT_NOT_UTF8")?;
            uuid::Uuid::parse_str(&p.operation_id).map_err(|_| "OPERATION_ID_INVALID")?;
            let binding = hash(
                &serde_json::to_vec(&(
                    &p.vault_id,
                    &p.path,
                    &p.expected,
                    &p.expected_file_id,
                    p.expected_revision,
                    hash(&bytes),
                ))
                .map_err(|_| "EXPERIMENT_REQUEST_INVALID")?,
            );
            let existing: Option<String> = ws
                .db
                .query_row(
                    "SELECT binding_hash FROM experiment_file_writes WHERE operation_id=?1",
                    [&p.operation_id],
                    |r| r.get(0),
                )
                .optional()
                .map_err(|_| "DATABASE_ERROR")?;
            if existing.as_ref().is_some_and(|old| old != &binding) {
                return Err("OPERATION_PAYLOAD_CONFLICT".into());
            }
            // Replays use the journal's original path/expected/content binding,
            // returning the committed entry rather than a later human edit.
            let recorded = ws.operation(&p.operation_id).map_err(|e| e.code)?;
            if recorded.is_some() && existing.is_none() {
                return Err("OPERATION_PAYLOAD_CONFLICT".into());
            }
            if recorded.is_none() {
                let current = entries(ws)?.into_iter().find(|e| e.path == p.path);
                match current {
                    None if p.expected.is_empty() => {}
                    Some(e)
                        if Some(&e.file_id) == p.expected_file_id.as_ref()
                            && Some(e.revision) == p.expected_revision
                            && e.hash == p.expected =>
                    {
                        snapshot(ws, e)?;
                    }
                    _ => return Err("EXPERIMENT_FILE_CONFLICT".into()),
                }
            }
            ws.db
                .execute(
                    "INSERT OR IGNORE INTO experiment_file_writes VALUES (?1,?2)",
                    params![p.operation_id, binding],
                )
                .map_err(|_| "DATABASE_ERROR")?;
            let entry = ws
                .write_with_identity(
                    &p.path,
                    &p.expected,
                    &bytes,
                    "local",
                    &p.operation_id,
                    p.expected_file_id.as_deref(),
                )
                .map_err(|e| e.code)?;
            Ok(json!({"operation_id":p.operation_id,"state":"committed","result":entry}))
        }
        _ => Err("HOST_METHOD_DENIED".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request(ws: &Workspace, method: &str, mut params: Value) -> Value {
        params["vault_id"] = json!(ws.vault_id);
        json!({"rpc":format!("workspace.experiments.{method}"),"params":params})
    }
    fn setup() -> (tempfile::TempDir, Workspace) {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(
            root.path().join("experiments/课程 #%.py"),
            "print('中文')\r\n",
        )
        .unwrap();
        std::fs::write(root.path().join("note.md"), "# Note").unwrap();
        let ws = Workspace::open(root.path()).unwrap();
        (root, ws)
    }
    fn write(ws: &mut Workspace, path: &str, content: &str) -> Value {
        let before = invoke(ws, "snapshot", json!({"path":path})).unwrap();
        request(
            ws,
            "write",
            json!({"path":path,"expected":before["entry"]["hash"].as_str().unwrap_or(""),
            "expected_file_id":before["entry"]["file_id"],"expected_revision":before["entry"]["revision"],
            "content_base64":STANDARD.encode(content),"operation_id":uuid::Uuid::new_v4().to_string()}),
        )
    }
    fn invoke(ws: &mut Workspace, method: &str, params: Value) -> Result<Value, String> {
        let r = request(ws, method, params);
        dispatch(ws, &r)
    }
    #[test]
    fn source_rpc_and_note_read_have_separate_file_scopes() {
        let (_root, mut ws) = setup();
        let listed = invoke(&mut ws, "list", json!({"offset":0,"limit":10})).unwrap();
        assert_eq!(listed["total"], 1);
        let source_id = &listed["items"][0]["file_id"];
        let old =
            json!({"rpc":"workspace.read","params":{"vault_id":ws.vault_id,"file_id":source_id}});
        assert_eq!(
            crate::workspace_broker::dispatch(&mut ws, &old).unwrap_err(),
            "CORE_NOTE_TYPE_UNSUPPORTED"
        );
        let note_id = ws.read("note.md").unwrap().entry.file_id;
        assert_eq!(
            invoke(&mut ws, "read", json!({"file_id":note_id})).unwrap_err(),
            "FILE_NOT_FOUND"
        );
        let source = invoke(&mut ws, "read", json!({"file_id":source_id})).unwrap();
        assert_eq!(
            STANDARD
                .decode(source["content_base64"].as_str().unwrap())
                .unwrap(),
            "print('中文')\r\n".as_bytes()
        );
    }
    #[test]
    fn source_write_is_exact_revisioned_and_replays_without_overwriting_human_edits() {
        let (root, mut ws) = setup();
        let path = "experiments/课程 #%.py";
        let r = write(&mut ws, path, "# 中文😀\r\nprint(2)\r\n");
        let result = dispatch(&mut ws, &r).unwrap();
        let id = result["result"]["file_id"].as_str().unwrap().to_owned();
        assert_eq!(
            std::fs::read(root.path().join(path)).unwrap(),
            "# 中文😀\r\nprint(2)\r\n".as_bytes()
        );
        ws.write(
            path,
            result["result"]["hash"].as_str().unwrap(),
            b"# human\n",
            "local",
        )
        .unwrap();
        assert_eq!(dispatch(&mut ws, &r).unwrap(), result);
        assert_eq!(ws.read(path).unwrap().entry.file_id, id);
        assert_eq!(std::fs::read(root.path().join(path)).unwrap(), b"# human\n");
        let mut changed = r.clone();
        changed["params"]["expected_revision"] = json!(1000);
        assert_eq!(
            dispatch(&mut ws, &changed).unwrap_err(),
            "OPERATION_PAYLOAD_CONFLICT"
        );
    }
    #[test]
    fn stale_revision_or_replaced_identity_cannot_write_even_with_same_content() {
        let (root, mut ws) = setup();
        let path = "experiments/课程 #%.py";
        let stale = write(&mut ws, path, "print('AI')\n");
        let original = ws.read(path).unwrap();
        ws.write(path, &original.entry.hash, b"print('human')\n", "local")
            .unwrap();
        let other = ws.read(path).unwrap();
        ws.write(
            path,
            &other.entry.hash,
            original.content.as_bytes(),
            "local",
        )
        .unwrap();
        assert_eq!(
            dispatch(&mut ws, &stale).unwrap_err(),
            "EXPERIMENT_FILE_CONFLICT"
        );
        let mut wrong_id = write(&mut ws, path, "print('AI')\n");
        wrong_id["params"]["expected_file_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            dispatch(&mut ws, &wrong_id).unwrap_err(),
            "EXPERIMENT_FILE_CONFLICT"
        );
        assert_eq!(
            std::fs::read(root.path().join(path)).unwrap(),
            original.content.as_bytes()
        );
    }
    #[test]
    fn new_file_write_preserves_bytes_and_fails_for_existing_target() {
        let (root, mut ws) = setup();
        let r = write(&mut ws, "experiments/input.json", "{\"中文\":3}\r\n");
        let saved = dispatch(&mut ws, &r).unwrap();
        assert_eq!(saved["state"], "committed");
        assert_eq!(
            std::fs::read(root.path().join("experiments/input.json")).unwrap(),
            "{\"中文\":3}\r\n".as_bytes()
        );
        let mut other = r;
        other["params"]["operation_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            dispatch(&mut ws, &other).unwrap_err(),
            "EXPERIMENT_FILE_CONFLICT"
        );
    }
    #[test]
    fn paths_unknown_fields_and_execution_routes_are_rejected() {
        let (_root, mut ws) = setup();
        for path in [
            "note.md",
            "../secret.py",
            "experiments/../secret.py",
            "experiments/x.txt",
            "experiments/.git/x.py",
            "experiments/CON.py",
            "experiments/x.py:secret",
            "C:/x.py",
        ] {
            let r = request(&ws, "snapshot", json!({"path":path}));
            assert_eq!(
                dispatch(&mut ws, &r).unwrap_err(),
                "EXPERIMENT_REQUEST_INVALID",
                "{path}"
            );
        }
        for method in ["run", "confirm_run", "import", "recover_cleanup"] {
            assert_eq!(
                invoke(&mut ws, method, json!({})).unwrap_err(),
                "HOST_METHOD_DENIED"
            );
        }
        let mut r = write(&mut ws, "experiments/x.py", "print(1)");
        r["params"]["approved"] = json!(true);
        assert_eq!(
            dispatch(&mut ws, &r).unwrap_err(),
            "WORKSPACE_REQUEST_INVALID"
        );
    }
    #[test]
    fn cross_vault_reads_and_writes_leave_sources_unchanged() {
        let (root, mut ws) = setup();
        let mut r = write(&mut ws, "experiments/x.py", "print(1)");
        r["params"]["vault_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            dispatch(&mut ws, &r).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
        assert!(!root.path().join("experiments/x.py").exists());
        let mut r = request(&ws, "snapshot", json!({"path":"experiments/课程 #%.py"}));
        r["params"]["vault_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            dispatch(&mut ws, &r).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
    }
    #[test]
    fn oversized_binary_or_linked_sources_are_not_exposed() {
        let (root, mut ws) = setup();
        std::fs::write(
            root.path().join("experiments/large.py"),
            vec![b'x'; MAX_FILE_BYTES as usize + 1],
        )
        .unwrap();
        std::fs::write(root.path().join("experiments/binary.py"), b"\xff").unwrap();
        std::fs::hard_link(
            root.path().join("experiments/课程 #%.py"),
            root.path().join("experiments/link.py"),
        )
        .unwrap();
        for (path, error) in [
            ("experiments/large.py", "EXPERIMENT_INPUT_TOO_LARGE"),
            ("experiments/binary.py", "EXPERIMENT_INPUT_NOT_UTF8"),
            ("experiments/link.py", "EXPERIMENT_INPUT_UNSAFE"),
        ] {
            let r = request(&ws, "snapshot", json!({"path":path}));
            assert_eq!(dispatch(&mut ws, &r).unwrap_err(), error);
        }
    }
}
