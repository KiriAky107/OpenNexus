#![cfg(feature = "desktop")]
use notesagent_host::{sync_client::SyncClient, workspace::Workspace};
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
};
use zeroize::Zeroizing;
struct Server(Child);
impl Drop for Server {
    fn drop(&mut self) {
        self.0.stdin.take();
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[tokio::test]
async fn actual_service_accepts_ordered_push_and_repeat_commit_without_duplicates() {
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join(".opennexus-test"), b"fixture").unwrap();
    let service = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../server sync")
        .canonicalize()
        .unwrap();
    let python = service.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let mut server = Server(
        Command::new(python)
            .args(["-m", "tests.host_fixture"])
            .arg(root.path())
            .current_dir(service)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let mut line = String::new();
    BufReader::new(server.0.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let ready: Value = serde_json::from_str(&line).unwrap();
    let endpoint = format!("http://127.0.0.1:{}", ready["port"]);
    let public = SyncClient::new(&endpoint, Zeroizing::new(String::new()), true).unwrap();
    public.handshake().await.unwrap();
    let session = public
        .login(
            "rust-fixture",
            Zeroizing::new("controlled-fixture-password".into()),
            "Rust integration",
        )
        .await
        .unwrap();
    let client = SyncClient::new(
        &endpoint,
        Zeroizing::new(session.access_token.clone()),
        true,
    )
    .unwrap();
    let vault = client
        .json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":"Rust test"})),
        )
        .await
        .unwrap();
    let remote = vault["vault_id"].as_str().unwrap();
    client.verify_empty(remote).await.unwrap();
    let local = tempfile::tempdir().unwrap();
    let workspace = Arc::new(Mutex::new(Workspace::open(local.path()).unwrap()));
    let binding = {
        let mut ws = workspace.lock().unwrap();
        let mut digest = String::new();
        for index in 0..20 {
            digest = ws
                .write(
                    "note.md",
                    &digest,
                    format!("fixture-{index}").as_bytes(),
                    "local",
                )
                .unwrap()
                .hash;
        }
        ws.sync_bind_empty(&endpoint, remote, "rust-fixture")
            .unwrap()
    };
    let first = workspace
        .lock()
        .unwrap()
        .sync_next(&binding.id)
        .unwrap()
        .unwrap();
    assert!(client.push_one(&workspace, &binding).await.unwrap());
    let payload = workspace
        .lock()
        .unwrap()
        .sync_commit_payload(&first)
        .unwrap();
    for _ in 0..100 {
        let replay = client
            .json(
                reqwest::Method::POST,
                &format!("sync/v1/vaults/{remote}/revisions"),
                Some(payload.clone()),
            )
            .await
            .unwrap();
        assert_eq!(replay["sequence"], 1);
    }
    for _ in 1..20 {
        assert!(client.push_one(&workspace, &binding).await.unwrap());
    }
    assert!(!client.push_one(&workspace, &binding).await.unwrap());
    let changes = client
        .json(
            reqwest::Method::GET,
            &format!("sync/v1/vaults/{remote}/changes"),
            None,
        )
        .await
        .unwrap();
    assert_eq!(changes["items"].as_array().unwrap().len(), 20);
    for (index, revision) in changes["items"].as_array().unwrap().iter().enumerate() {
        assert_eq!(revision["base_revision"], index as i64);
        assert_eq!(revision["sequence"], index as i64 + 1);
    }
    assert_eq!(
        client.verify_empty(remote).await.unwrap_err().code,
        "SYNC_RECONCILIATION_REQUIRED"
    );
    let session_b = public
        .login(
            "rust-fixture",
            Zeroizing::new("controlled-fixture-password".into()),
            "Device B",
        )
        .await
        .unwrap();
    let client_b = SyncClient::new(
        &endpoint,
        Zeroizing::new(session_b.access_token.clone()),
        true,
    )
    .unwrap();
    let root_b = tempfile::tempdir().unwrap();
    let workspace_b = Arc::new(Mutex::new(Workspace::open(root_b.path()).unwrap()));
    let binding_b = workspace_b
        .lock()
        .unwrap()
        .sync_bind_download(&endpoint, remote, "rust-fixture")
        .unwrap();
    assert_eq!(
        client_b.pull_page(&workspace_b, &binding_b).await.unwrap(),
        20
    );
    assert_eq!(
        workspace_b.lock().unwrap().read("note.md").unwrap().content,
        "fixture-19"
    );
    assert_eq!(workspace_b.lock().unwrap().pending_count().unwrap(), 0);
    assert_eq!(
        workspace_b
            .lock()
            .unwrap()
            .read("note.md")
            .unwrap()
            .entry
            .file_id,
        first.file_id
    );
    // Receiving one's historical commits never rolls back newer local edits.
    {
        let mut ws = workspace.lock().unwrap();
        let current = ws.read("note.md").unwrap();
        ws.write("note.md", &current.entry.hash, b"new-a", "local")
            .unwrap();
    }
    assert_eq!(client.pull_page(&workspace, &binding).await.unwrap(), 20);
    assert_eq!(
        workspace.lock().unwrap().read("note.md").unwrap().content,
        "new-a"
    );
    {
        let mut ws = workspace_b.lock().unwrap();
        let current = ws.read("note.md").unwrap();
        ws.write("note.md", &current.entry.hash, b"offline-b", "local")
            .unwrap();
    }
    client.push_one(&workspace, &binding).await.unwrap();
    assert_eq!(
        client_b.pull_page(&workspace_b, &binding_b).await.unwrap(),
        1
    );
    assert_eq!(
        workspace_b.lock().unwrap().read("note.md").unwrap().content,
        "offline-b"
    );
    let conflicts = workspace_b
        .lock()
        .unwrap()
        .sync_conflicts(&binding_b.id)
        .unwrap();
    assert_eq!(conflicts.len(), 1);
    assert_eq!(conflicts[0]["remote"]["sequence"], 21);
    assert_eq!(
        workspace_b
            .lock()
            .unwrap()
            .sync_binding()
            .unwrap()
            .unwrap()
            .cursor,
        21
    );
}
