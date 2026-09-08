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
    assert_eq!(workspace.lock().unwrap().pending_count().unwrap(), 4);
    assert!(!temp
        .path()
        .join("core/unbound-vault/Core fixture.md")
        .exists());
}
