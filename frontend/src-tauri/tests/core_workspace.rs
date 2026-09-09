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
    // The actual Python HTTP handler must round-trip revision through Host pipes,
    // including repeat requests after a successful commit.
    let path = "/api/settings/persona";
    let (status, empty) = request(
        &mut core,
        "GET",
        path,
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{empty}");
    assert_eq!(empty["revision"], "");
    let operation = uuid::Uuid::new_v4().to_string();
    let mut body = empty.clone();
    body["system_prompt"] = json!("Host persona fixture");
    let (status, first) = request(
        &mut core,
        "PUT",
        path,
        &vault,
        &operation,
        Some(body.clone()),
    )
    .await;
    assert_eq!(status, 200, "{first}");
    assert_eq!(first["revision"].as_str().unwrap().len(), 64);
    for _ in 0..20 {
        let (status, replay) = request(
            &mut core,
            "PUT",
            path,
            &vault,
            &operation,
            Some(body.clone()),
        )
        .await;
        assert_eq!(status, 200, "{replay}");
        assert_eq!(replay, first);
    }
    let (status, stale) = request(
        &mut core,
        "PUT",
        path,
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(body.clone()),
    )
    .await;
    assert_eq!(status, 409, "{stale}");
    let (status, wrong_vault) = request(
        &mut core,
        "GET",
        path,
        &uuid::Uuid::new_v4().to_string(),
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 409, "{wrong_vault}");
    let (status, loaded) = request(
        &mut core,
        "GET",
        path,
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{loaded}");
    assert_eq!(loaded, first);
    let stored = workspace
        .lock()
        .unwrap()
        .record_get_kind("persona", "default")
        .unwrap()
        .unwrap();
    assert_eq!(stored["hash"], first["revision"]);

    // User-created Skills are Vault records, use Host CAS/idempotency, and are
    // resolved by the actual Agent route without copying package paths or grants.
    let create_skill_operation = uuid::Uuid::new_v4();
    let skill_body = json!({
        "revision":"", "name":"Vault reviewer", "description":"portable",
        "prompt":"Answer with the exact phrase user-skill-active.", "tools":[],
        "permissions":[], "retrieval":{"top_k":10,"rerank":true,"citation":true},
        "required_capabilities":["chat"]
    });
    let (status, user_skill) = request(
        &mut core,
        "POST",
        "/api/user-skills",
        &vault,
        &create_skill_operation.to_string(),
        Some(skill_body.clone()),
    )
    .await;
    assert_eq!(status, 201, "{user_skill}");
    let skill_id = user_skill["skill_id"].as_str().unwrap();
    assert_eq!(
        skill_id,
        format!("user_skill_{}", create_skill_operation.simple())
    );
    assert_eq!(user_skill["status"], "ready");
    for _ in 0..20 {
        let (status, replay) = request(
            &mut core,
            "POST",
            "/api/user-skills",
            &vault,
            &create_skill_operation.to_string(),
            Some(skill_body.clone()),
        )
        .await;
        assert_eq!(status, 201, "{replay}");
        assert_eq!(replay, user_skill);
    }
    let mut changed = skill_body.clone();
    changed["name"] = json!("Changed replay");
    let (status, conflict) = request(
        &mut core,
        "POST",
        "/api/user-skills",
        &vault,
        &create_skill_operation.to_string(),
        Some(changed),
    )
    .await;
    assert_eq!(status, 409, "{conflict}");
    assert_eq!(conflict["error"]["code"], "USER_SKILL_OPERATION_CONFLICT");
    let (status, listed) = request(
        &mut core,
        "GET",
        "/api/user-skills?limit=100&offset=0",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 200, "{listed}");
    assert_eq!(listed["items"][0]["skill_id"], skill_id);
    let (status, denied) = request(
        &mut core,
        "GET",
        "/api/user-skills?limit=100&offset=0",
        &uuid::Uuid::new_v4().to_string(),
        &uuid::Uuid::new_v4().to_string(),
        None,
    )
    .await;
    assert_eq!(status, 409, "{denied}");
    assert_eq!(denied["error"]["code"], "VAULT_PERMISSION_CHANGED");
    let mut update_body = skill_body.clone();
    update_body["revision"] = user_skill["revision"].clone();
    update_body["name"] = json!("Updated reviewer");
    let update_operation = uuid::Uuid::new_v4().to_string();
    let (status, updated_skill) = request(
        &mut core,
        "PUT",
        &format!("/api/user-skills/{skill_id}"),
        &vault,
        &update_operation,
        Some(update_body.clone()),
    )
    .await;
    assert_eq!(status, 200, "{updated_skill}");
    assert_eq!(updated_skill["data"]["version"], 2);
    let (status, stale) = request(
        &mut core,
        "PUT",
        &format!("/api/user-skills/{skill_id}"),
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(update_body.clone()),
    )
    .await;
    assert_eq!(status, 409, "{stale}");
    assert_eq!(stale["error"]["code"], "USER_SKILL_REVISION_CONFLICT");
    for _ in 0..2 {
        let (status, replay) = request(
            &mut core,
            "PUT",
            &format!("/api/user-skills/{skill_id}"),
            &vault,
            &update_operation,
            Some(update_body.clone()),
        )
        .await;
        assert_eq!(status, 200, "{replay}");
        assert_eq!(replay, updated_skill);
    }
    let (status, run) = request(
        &mut core,
        "POST",
        "/api/agent/runs",
        &vault,
        &uuid::Uuid::new_v4().to_string(),
        Some(json!({"input":"Confirm the Skill configuration","provider_id":"mock","model":"mock-1","skill_id":skill_id})),
    )
    .await;
    assert_eq!(status, 202, "{run}");
    assert_eq!(run["skill_id"], skill_id);
    let delete_operation = uuid::Uuid::new_v4().to_string();
    let delete_path = format!(
        "/api/user-skills/{skill_id}?revision={}",
        updated_skill["revision"].as_str().unwrap()
    );
    for _ in 0..2 {
        let (status, deleted) = request(
            &mut core,
            "DELETE",
            &delete_path,
            &vault,
            &delete_operation,
            None,
        )
        .await;
        assert_eq!(status, 200, "{deleted}");
    }
    assert!(!root
        .join(format!("opennexus-records/v1/user-skills/{skill_id}.json"))
        .exists());
    assert_eq!(workspace.lock().unwrap().pending_count().unwrap(), 12);
}
