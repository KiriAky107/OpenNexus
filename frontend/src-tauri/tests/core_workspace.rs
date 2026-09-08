#![cfg(feature = "desktop")]
//! Real Python Core + Host pipes + isolated Workspace; no personal data or Provider.
use notesagent_host::{core::CoreSupervisor, workspace::Workspace, workspace_broker};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{Arc, Mutex},
};

async fn request(
    core: &mut CoreSupervisor,
    method: &str,
    path: &str,
    vault: &str,
    operation: &str,
    body: Option<Value>,
) -> (u16, Value) {
    let session = core.request_session(path).unwrap();
    let mut request = reqwest::Client::new()
        .request(method.parse::<reqwest::Method>().unwrap(), session.url)
        .header("Authorization", session.authorization.as_str())
        .header("X-Core-Generation", session.generation)
        .header("X-OpenNexus-Vault", vault)
        .header("X-Request-Id", operation);
    if let Some(body) = body {
        request = request.json(&body);
    }
    let response = request.send().await.unwrap();
    let status = response.status().as_u16();
    (status, response.json().await.unwrap())
}

#[tokio::test]
async fn real_core_notes_roundtrip_only_through_bound_host_and_confirm_commits() {
    let backend = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../backend")
        .canonicalize()
        .unwrap();
    let python = backend.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("vault");
    std::fs::create_dir(&root).unwrap();
    let workspace = Arc::new(Mutex::new(Workspace::open(&root).unwrap()));
    let vault = workspace.lock().unwrap().vault_id.clone();
    let handler = workspace.clone();
    let mut core = CoreSupervisor::new(
        python,
        vec!["-m".into(), "app.sidecar".into()],
        backend,
        temp.path().join("core"),
    )
    .with_broker(Arc::new(move |value| {
        workspace_broker::dispatch(&mut handler.lock().unwrap(), value)
    }));
    let operation = uuid::Uuid::new_v4().to_string();
    let (status, created) = request(
        &mut core,
        "POST",
        "/api/notes",
        &vault,
        &operation,
        Some(json!({"title":"Core fixture","markdown":"# 中文\noriginal","tags":["fixture"]})),
    )
    .await;
    assert_eq!(status, 200, "{created}");
    let file_id = created["note_id"].as_str().unwrap();
    let original = workspace.lock().unwrap().read("Core fixture.md").unwrap();
    assert_eq!(original.entry.file_id, file_id);
    assert!(original.content.contains("original"));
    let (status, search) = request(
        &mut core,
        "POST",
        "/api/search",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(json!({"query":"original","mode":"fts"})),
    )
    .await;
    assert_eq!(status, 200, "{search}");
    assert!(search.to_string().contains(file_id));
    assert_eq!(
        workspace
            .lock()
            .unwrap()
            .operation(&operation)
            .unwrap()
            .unwrap()["state"],
        "committed"
    );
    let (status, listed) = request(
        &mut core,
        "GET",
        "/api/notes",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{listed}");
    assert_eq!(listed["items"][0]["note_id"], file_id);
    let next = uuid::Uuid::new_v4().to_string();
    let (status, updated) = request(
        &mut core,
        "PATCH",
        &format!("/api/notes/{file_id}"),
        &vault,
        &next,
        Some(json!({"markdown":"# New\nchanged","expected_content_hash":original.entry.hash})),
    )
    .await;
    assert_eq!(status, 200, "{updated}");
    let (status, conflict) = request(
        &mut core,
        "PATCH",
        &format!("/api/notes/{file_id}"),
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(json!({"markdown":"must-not-overwrite","expected_content_hash":original.entry.hash})),
    )
    .await;
    assert_eq!(status, 409, "{conflict}");
    let (status, moved) = request(
        &mut core,
        "POST",
        &format!("/api/notes/{file_id}/move"),
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(json!({"folder":"nested"})),
    )
    .await;
    assert_eq!(status, 200, "{moved}");
    assert_eq!(moved["note_id"], file_id);
    assert!(root.join("nested/Core fixture.md").is_file());
    let (status, denied) = request(
        &mut core,
        "GET",
        &format!("/api/notes/{file_id}"),
        &uuid::Uuid::new_v4().to_string(),
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 409, "{denied}");
    assert_eq!(denied["error"]["code"], "VAULT_PERMISSION_CHANGED");

    let legacy_database = rusqlite::Connection::open(
        temp.path()
            .join("core/vault-state")
            .join(&vault)
            .join("core.sqlite3"),
    )
    .unwrap();
    legacy_database.execute("INSERT INTO tasks VALUES ('task_00000000000000000000000000000002','Legacy task','','todo',?1,NULL,'2026-09-08T00:00:00+00:00','2026-09-08T00:00:00+00:00')",[file_id]).unwrap();
    let task_operation = uuid::Uuid::new_v4().to_string();
    let task_body = json!({"title":"Host task","note_id":file_id});
    let (status, task) = request(
        &mut core,
        "POST",
        "/api/tasks",
        &vault,
        &task_operation,
        Some(task_body.clone()),
    )
    .await;
    assert_eq!(status, 200, "{task}");
    let task_id = task["task_id"].as_str().unwrap();
    for _ in 0..20 {
        let (status, replay) = request(
            &mut core,
            "POST",
            "/api/tasks",
            &vault,
            &task_operation,
            Some(task_body.clone()),
        )
        .await;
        assert_eq!(status, 200, "{replay}");
        assert_eq!(replay, task);
    }
    let (status, changed_request) = request(
        &mut core,
        "POST",
        "/api/tasks",
        &vault,
        &task_operation,
        Some(json!({"title":"changed","note_id":file_id})),
    )
    .await;
    assert_eq!(status, 409, "{changed_request}");
    let record_path = root.join(format!("opennexus-records/v1/tasks/{task_id}.json"));
    assert!(record_path.is_file());
    let (status, task_updated) = request(
        &mut core,
        "PATCH",
        &format!("/api/tasks/{task_id}"),
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(json!({"status":"done"})),
    )
    .await;
    assert_eq!(status, 200, "{task_updated}");
    assert_eq!(task_updated["status"], "done");
    let (status, tasks) = request(
        &mut core,
        "GET",
        "/api/tasks",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{tasks}");
    assert_eq!(tasks["items"].as_array().unwrap().len(), 2);
    let (status, task_denied) = request(
        &mut core,
        "GET",
        "/api/tasks",
        &uuid::Uuid::new_v4().to_string(),
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 409, "{task_denied}");
    let deletion = uuid::Uuid::new_v4().to_string();
    for _ in 0..2 {
        let (status, result) = request(
            &mut core,
            "DELETE",
            &format!("/api/tasks/{task_id}"),
            &vault,
            &deletion,
            None,
        )
        .await;
        assert_eq!(status, 200, "{result}");
    }
    assert!(!record_path.exists());
    let source: String = legacy_database
        .query_row(
            "SELECT title FROM tasks WHERE task_id='task_00000000000000000000000000000002'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(source, "Legacy task");
    legacy_database
        .execute("DELETE FROM index_meta WHERE key='tasks_host_owned_v1'", [])
        .unwrap();
    let (status, after_migration) = request(
        &mut core,
        "GET",
        "/api/tasks",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{after_migration}");
    assert_eq!(after_migration["items"].as_array().unwrap().len(), 1);

    let (status, deleted) = request(
        &mut core,
        "DELETE",
        &format!("/api/notes/{file_id}"),
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{deleted}");
    assert!(!root.join("nested/Core fixture.md").exists());
    let (status, search) = request(
        &mut core,
        "POST",
        "/api/search",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(json!({"query":"original","mode":"fts"})),
    )
    .await;
    assert_eq!(status, 200, "{search}");
    assert!(!search.to_string().contains(file_id));
    assert_eq!(workspace.lock().unwrap().pending_count().unwrap(), 8);
    assert!(!temp
        .path()
        .join("core/unbound-vault/Core fixture.md")
        .exists());
}
