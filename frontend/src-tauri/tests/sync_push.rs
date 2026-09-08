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
    // All three explicit choices converge; the local copy gets an independent file ID.
    for (iteration, choice) in ["local", "remote", "copy"].into_iter().enumerate() {
        if iteration > 0 {
            for (ws, content) in [(&workspace, "next-a"), (&workspace_b, "next-b")] {
                let mut ws = ws.lock().unwrap();
                let current = ws.read("note.md").unwrap();
                ws.write("note.md", &current.entry.hash, content.as_bytes(), "local")
                    .unwrap();
            }
            assert!(client.push_one(&workspace, &binding).await.unwrap());
            assert_eq!(
                client_b.pull_page(&workspace_b, &binding_b).await.unwrap(),
                1
            );
        }
        {
            let mut ws = workspace_b.lock().unwrap();
            let conflict = ws.sync_conflicts(&binding_b.id).unwrap().remove(0);
            let sequence = conflict["sequence"].as_i64().unwrap();
            let expected = ws.read("note.md").unwrap().entry.hash;
            assert_eq!(
                ws.sync_resolve(
                    &binding_b.id,
                    sequence,
                    choice,
                    if choice == "copy" { "copy.md" } else { "" },
                    "wrong"
                )
                .unwrap_err()
                .code,
                "REVISION_CONFLICT"
            );
            ws.sync_resolve(
                &binding_b.id,
                sequence,
                choice,
                if choice == "copy" { "copy.md" } else { "" },
                &expected,
            )
            .unwrap();
            assert!(ws.sync_conflicts(&binding_b.id).unwrap().is_empty());
            // Repeating a persisted decision is harmless.
            ws.sync_resolve(
                &binding_b.id,
                sequence,
                choice,
                if choice == "copy" { "copy.md" } else { "" },
                &expected,
            )
            .unwrap();
        }
        while client_b.push_one(&workspace_b, &binding_b).await.unwrap() {}
        client.pull_page(&workspace, &binding).await.unwrap();
        client_b.pull_page(&workspace_b, &binding_b).await.unwrap();
        let expected = if choice == "local" {
            "offline-b"
        } else {
            "next-a"
        };
        assert_eq!(
            workspace.lock().unwrap().read("note.md").unwrap().content,
            expected
        );
        assert_eq!(
            workspace_b.lock().unwrap().read("note.md").unwrap().content,
            expected
        );
        if choice == "copy" {
            let copy = workspace.lock().unwrap().read("copy.md").unwrap();
            assert_eq!(copy.content, "next-b");
            assert_ne!(copy.entry.file_id, first.file_id);
            assert_eq!(
                copy.entry.file_id,
                workspace_b
                    .lock()
                    .unwrap()
                    .read("copy.md")
                    .unwrap()
                    .entry
                    .file_id
            );
        }
    }

    workspace
        .lock()
        .unwrap()
        .write("collision.md", "", b"created-a", "local")
        .unwrap();
    workspace_b
        .lock()
        .unwrap()
        .write("collision.md", "", b"created-b", "local")
        .unwrap();
    client.push_one(&workspace, &binding).await.unwrap();
    client_b.pull_page(&workspace_b, &binding_b).await.unwrap();
    {
        let mut ws = workspace_b.lock().unwrap();
        let conflict = ws.sync_conflicts(&binding_b.id).unwrap().remove(0);
        ws.sync_resolve(
            &binding_b.id,
            conflict["sequence"].as_i64().unwrap(),
            "local",
            "",
            conflict["current_hash"].as_str().unwrap(),
        )
        .unwrap();
    }
    client_b.push_one(&workspace_b, &binding_b).await.unwrap();
    client.pull_page(&workspace, &binding).await.unwrap();
    client_b.pull_page(&workspace_b, &binding_b).await.unwrap();
    assert_eq!(
        workspace
            .lock()
            .unwrap()
            .read("collision.md")
            .unwrap()
            .content,
        "created-b"
    );
    assert_eq!(
        workspace
            .lock()
            .unwrap()
            .read("collision.md")
            .unwrap()
            .entry
            .file_id,
        workspace_b
            .lock()
            .unwrap()
            .read("collision.md")
            .unwrap()
            .entry
            .file_id
    );

    // Initial merge reviews a fixed snapshot and preserves conflicting local content.
    let merge_root = tempfile::tempdir().unwrap();
    let merge_ws = Arc::new(Mutex::new(Workspace::open(merge_root.path()).unwrap()));
    let snapshot = client.snapshot(remote).await.unwrap();
    let merge_binding = {
        let mut ws = merge_ws.lock().unwrap();
        ws.write("note.md", "", b"next-a", "local").unwrap();
        ws.write("collision.md", "", b"merge-local", "local")
            .unwrap();
        ws.write("local-only.md", "", b"only-local", "local")
            .unwrap();
        let preview = ws
            .sync_preview(&endpoint, remote, "rust-fixture", &snapshot)
            .unwrap();
        assert!(preview
            .items
            .iter()
            .any(|v| v.path == "note.md" && v.action == "identical"));
        assert!(preview
            .items
            .iter()
            .any(|v| v.path == "collision.md" && v.action == "conflict"));
        ws.sync_bind_initial(
            &endpoint,
            remote,
            "rust-fixture",
            &snapshot,
            &preview.fingerprint,
        )
        .unwrap()
    };
    assert!(!client_b.push_one(&merge_ws, &merge_binding).await.unwrap());
    assert_eq!(
        client_b.pull_page(&merge_ws, &merge_binding).await.unwrap(),
        snapshot.items.len()
    );
    {
        let mut ws = merge_ws.lock().unwrap();
        assert_eq!(ws.read("collision.md").unwrap().content, "merge-local");
        assert_eq!(ws.read("copy.md").unwrap().content, "next-b");
        assert_eq!(
            ws.sync_binding().unwrap().unwrap().cursor,
            snapshot.boundary
        );
        let conflict = ws.sync_conflicts(&merge_binding.id).unwrap().remove(0);
        ws.sync_resolve(
            &merge_binding.id,
            conflict["sequence"].as_i64().unwrap(),
            "remote",
            "",
            conflict["current_hash"].as_str().unwrap(),
        )
        .unwrap();
    }
    assert!(client_b.push_one(&merge_ws, &merge_binding).await.unwrap());
    assert!(!client_b.push_one(&merge_ws, &merge_binding).await.unwrap());
    client.pull_page(&workspace, &binding).await.unwrap();
    assert_eq!(
        workspace
            .lock()
            .unwrap()
            .read("local-only.md")
            .unwrap()
            .content,
        "only-local"
    );

    let task_id = "task_00000000000000000000000000000001";
    let task_path = format!("opennexus-records/v1/tasks/{task_id}.json");
    let task_record = json!({"schema":1,"kind":"task","id":task_id,"data":{"title":"Synchronized task","description":"","status":"todo","note_id":first.file_id,"due_at_ms":null,"created_at_ms":0,"updated_at_ms":0}});
    workspace
        .lock()
        .unwrap()
        .write(
            &task_path,
            "",
            &serde_json::to_vec(&task_record).unwrap(),
            "local",
        )
        .unwrap();
    client.push_one(&workspace, &binding).await.unwrap();
    client_b.pull_page(&workspace_b, &binding_b).await.unwrap();
    {
        let mut ws = workspace_b.lock().unwrap();
        let task = ws.record_get(task_id).unwrap().unwrap();
        assert_eq!(task["record"], task_record);
        let mut next = task["record"].clone();
        next["data"]["status"] = json!("done");
        ws.write(
            &task_path,
            task["hash"].as_str().unwrap(),
            &serde_json::to_vec(&next).unwrap(),
            "local",
        )
        .unwrap();
    }
    client_b.push_one(&workspace_b, &binding_b).await.unwrap();
    client.pull_page(&workspace, &binding).await.unwrap();
    assert_eq!(
        workspace
            .lock()
            .unwrap()
            .record_get(task_id)
            .unwrap()
            .unwrap()["record"]["data"]["status"],
        "done"
    );
    {
        let mut ws = workspace_b.lock().unwrap();
        let task = ws.record_get(task_id).unwrap().unwrap();
        ws.mutate_operation(
            "delete",
            &task_path,
            "",
            task["hash"].as_str().unwrap(),
            &uuid::Uuid::new_v4().to_string(),
        )
        .unwrap();
    }
    client_b.push_one(&workspace_b, &binding_b).await.unwrap();
    client.pull_page(&workspace, &binding).await.unwrap();
    assert!(workspace
        .lock()
        .unwrap()
        .record_get(task_id)
        .unwrap()
        .is_none());

    let preference_path = "opennexus-records/v1/theme-settings/appearance.json";
    let preference_record = json!({"schema":1,"kind":"theme_settings","id":"appearance","data":{"themeId":"dark","fontEditorSize":18,"fontEditorFamily":"system-ui","lineHeight":1.7,"codeBlockTheme":"auto","headings":{"custom":false,"family":"inherit","levels":([32,28,24,21,18,16].map(|size|json!({"size":size,"weight":700})))}}});
    workspace
        .lock()
        .unwrap()
        .write(
            preference_path,
            "",
            &serde_json::to_vec(&preference_record).unwrap(),
            "local",
        )
        .unwrap();
    client.push_one(&workspace, &binding).await.unwrap();
    client_b.pull_page(&workspace_b, &binding_b).await.unwrap();
    assert_eq!(
        workspace_b
            .lock()
            .unwrap()
            .record_get_kind("theme_settings", "appearance")
            .unwrap()
            .unwrap()["record"],
        preference_record
    );
    {
        let mut ws = workspace_b.lock().unwrap();
        let stored = ws
            .record_get_kind("theme_settings", "appearance")
            .unwrap()
            .unwrap();
        let mut record = stored["record"].clone();
        record["data"]["fontEditorSize"] = json!(24);
        ws.write(
            preference_path,
            stored["hash"].as_str().unwrap(),
            &serde_json::to_vec(&record).unwrap(),
            "local",
        )
        .unwrap();
    }
    client_b.push_one(&workspace_b, &binding_b).await.unwrap();
    client.pull_page(&workspace, &binding).await.unwrap();
    assert_eq!(
        workspace
            .lock()
            .unwrap()
            .record_get_kind("theme_settings", "appearance")
            .unwrap()
            .unwrap()["record"]["data"]["fontEditorSize"],
        24
    );
    // Kill the actual client process after each durable 10 MiB server offset,
    // before its response reaches the client. The next process must query offset.
    use sha2::{Digest, Sha256};
    use std::io::Write;
    let large_remote = client
        .json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":"100 MiB resumable"})),
        )
        .await
        .unwrap();
    let large_remote = large_remote["vault_id"].as_str().unwrap();
    let large_root = tempfile::tempdir().unwrap();
    let mut large_ws = Workspace::open(large_root.path()).unwrap();
    let large_binding = large_ws
        .sync_bind_empty(&endpoint, large_remote, "rust-fixture")
        .unwrap();
    std::fs::create_dir(large_root.path().join("attachments")).unwrap();
    let mut attachment =
        std::fs::File::create(large_root.path().join("attachments/large.bin")).unwrap();
    let block = vec![42u8; 1024 * 1024];
    let mut hasher = Sha256::new();
    for _ in 0..100 {
        attachment.write_all(&block).unwrap();
        hasher.update(&block);
    }
    attachment.sync_all().unwrap();
    drop(attachment);
    let expected_hash = format!("{:x}", hasher.finalize());
    assert_eq!(large_ws.sync_discover(&large_binding.id).unwrap(), 1);
    large_ws.sync_capture(&large_binding.id).unwrap();
    let large_id = large_ws
        .sync_next(&large_binding.id)
        .unwrap()
        .unwrap()
        .file_id;
    drop(large_ws);
    std::fs::write(root.path().join("interrupt-upload"), b"controlled-fixture").unwrap();
    for boundary in 1..=10 {
        let mut worker = Server(
            Command::new(std::env::current_exe().unwrap())
                .args(["--ignored", "--exact", "resumable_upload_worker"])
                .env("OPENNEXUS_SYNC_WORKER_ROOT", large_root.path())
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .unwrap(),
        );
        worker
            .0
            .stdin
            .take()
            .unwrap()
            .write_all(session.access_token.as_bytes())
            .unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(45);
        let marker = root.path().join("upload-boundary");
        while !marker.exists() {
            assert!(
                worker.0.try_wait().unwrap().is_none(),
                "upload worker exited before boundary {boundary}"
            );
            assert!(
                std::time::Instant::now() < deadline,
                "upload boundary timeout"
            );
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        assert_eq!(
            std::fs::read_to_string(&marker)
                .unwrap()
                .parse::<usize>()
                .unwrap(),
            boundary * 10 * 1024 * 1024
        );
        worker.0.kill().unwrap();
        worker.0.wait().unwrap();
        std::fs::remove_file(marker).unwrap();
    }
    std::fs::remove_file(root.path().join("interrupt-upload")).unwrap();
    let large_workspace = Arc::new(Mutex::new(Workspace::open(large_root.path()).unwrap()));
    let interrupted = large_workspace
        .lock()
        .unwrap()
        .sync_attempts(&large_binding.id)
        .unwrap();
    assert_eq!(interrupted.len(), 1);
    assert_eq!(interrupted[0]["attempts"], 10);
    assert_eq!(interrupted[0]["outcome"], "interrupted");
    assert!(client
        .push_one(&large_workspace, &large_binding)
        .await
        .unwrap());
    assert!(!client
        .push_one(&large_workspace, &large_binding)
        .await
        .unwrap());
    let download_root = tempfile::tempdir().unwrap();
    let download = Arc::new(Mutex::new(Workspace::open(download_root.path()).unwrap()));
    let download_binding = download
        .lock()
        .unwrap()
        .sync_bind_download(&endpoint, large_remote, "rust-fixture")
        .unwrap();
    assert_eq!(
        client_b
            .pull_page(&download, &download_binding)
            .await
            .unwrap(),
        1
    );
    let received = std::fs::read(download_root.path().join("attachments/large.bin")).unwrap();
    assert_eq!(received.len(), 104857600);
    assert_eq!(format!("{:x}", Sha256::digest(&received)), expected_hash);
    assert_eq!(
        download.lock().unwrap().path_for_id(&large_id).unwrap(),
        "attachments/large.bin"
    );
    assert_eq!(download.lock().unwrap().pending_count().unwrap(), 0);
    assert_eq!(
        download
            .lock()
            .unwrap()
            .sync_discover(&download_binding.id)
            .unwrap(),
        0
    );
    // SQLite stores metadata, never the 100 MiB body.
    for directory in [large_root.path(), download_root.path()] {
        let managed = directory.join(".ainote");
        for item in std::fs::read_dir(managed).unwrap().flatten() {
            if item.file_type().unwrap().is_file() {
                assert!(
                    item.metadata().unwrap().len() < 5 * 1024 * 1024,
                    "large body leaked into metadata storage"
                );
            }
        }
    }
    // Host sessions survive encrypted storage reopen and refresh on the actual service.
    use notesagent_host::{credentials::CredentialBroker, sync_auth};
    let credential_root = tempfile::tempdir().unwrap();
    let credential_path = credential_root.path().join("credentials.onxcred");
    let mut broker = CredentialBroker::new(credential_path.clone());
    broker
        .unlock(Zeroizing::new(b"fixture-stronghold-password".to_vec()))
        .unwrap();
    let credentials = Arc::new(Mutex::new(Some(broker)));
    let canonical = sync_auth::login(
        &credentials,
        &endpoint,
        "rust-fixture",
        Zeroizing::new("controlled-fixture-password".into()),
        "Encrypted Host",
        true,
    )
    .await
    .unwrap();
    let host_client = sync_auth::client(&credentials, &canonical, "rust-fixture", true)
        .await
        .unwrap();
    assert!(host_client
        .json(reqwest::Method::GET, "sync/v1/vaults", None)
        .await
        .unwrap()["items"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v["id"] == remote));
    credentials.lock().unwrap().as_mut().unwrap().lock();
    *credentials.lock().unwrap() = Some(CredentialBroker::new(credential_path));
    credentials
        .lock()
        .unwrap()
        .as_mut()
        .unwrap()
        .unlock(Zeroizing::new(b"fixture-stronghold-password".to_vec()))
        .unwrap();
    // Expire the access token early in this isolated fixture; the refresh token stays valid.
    let database = rusqlite::Connection::open(root.path().join("sync.sqlite3")).unwrap();
    database
        .execute("UPDATE sessions SET expires=0", [])
        .unwrap();
    let attempts = std::sync::atomic::AtomicUsize::new(0);
    let recovered = sync_auth::authenticated(&credentials, &canonical, "rust-fixture", |client| {
        attempts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        async move {
            client
                .json(reqwest::Method::GET, "sync/v1/vaults", None)
                .await
        }
    })
    .await
    .unwrap();
    assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 2);
    assert!(recovered["items"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v["id"] == remote));
    // Even a repeated 401 must stop after one rotation, rather than refresh indefinitely.
    attempts.store(0, std::sync::atomic::Ordering::SeqCst);
    let denied = sync_auth::authenticated(&credentials, &canonical, "rust-fixture", |_client| {
        attempts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        async {
            Err::<(), _>(notesagent_host::sync_client::SyncError {
                code: "UNAUTHORIZED".into(),
                status: 401,
                retry_after: None,
            })
        }
    })
    .await
    .unwrap_err();
    assert_eq!(denied.status, 401);
    assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 2);
    attempts.store(0, std::sync::atomic::Ordering::SeqCst);
    let denied = sync_auth::authenticated(&credentials, &canonical, "rust-fixture", |_client| {
        attempts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        async {
            Err::<(), _>(notesagent_host::sync_client::SyncError {
                code: "FORBIDDEN".into(),
                status: 403,
                retry_after: None,
            })
        }
    })
    .await
    .unwrap_err();
    assert_eq!(denied.status, 403);
    assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 1);
    let restored = sync_auth::client(&credentials, &canonical, "rust-fixture", false)
        .await
        .unwrap();
    restored.handshake().await.unwrap();
    sync_auth::logout(&credentials, &canonical, "rust-fixture")
        .await
        .unwrap();
    assert!(!sync_auth::available(&credentials, &canonical, "rust-fixture").unwrap());
    assert_eq!(
        restored
            .json(reqwest::Method::GET, "sync/v1/vaults", None)
            .await
            .unwrap_err()
            .status,
        401
    );
    sync_auth::login(
        &credentials,
        &endpoint,
        "rust-fixture",
        Zeroizing::new("controlled-fixture-password".into()),
        "Revoked Host",
        true,
    )
    .await
    .unwrap();
    database.execute("DELETE FROM sessions", []).unwrap();
    attempts.store(0, std::sync::atomic::Ordering::SeqCst);
    let revoked = sync_auth::authenticated(&credentials, &canonical, "rust-fixture", |client| {
        attempts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        async move {
            client
                .json(reqwest::Method::GET, "sync/v1/vaults", None)
                .await
        }
    })
    .await
    .unwrap_err();
    assert_eq!(revoked.status, 401);
    assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 1);
}

#[tokio::test]
#[ignore = "helper process driven and killed by the parent fault test"]
async fn resumable_upload_worker() {
    let root = std::env::var("OPENNEXUS_SYNC_WORKER_ROOT").expect("controlled fixture root");
    let ws = Arc::new(Mutex::new(Workspace::open(Path::new(&root)).unwrap()));
    let binding = ws.lock().unwrap().sync_binding().unwrap().unwrap();
    assert!(binding.endpoint.starts_with("http://127.0.0.1:"));
    use std::io::Read;
    let mut token = Zeroizing::new(String::new());
    std::io::stdin()
        .take(4096)
        .read_to_string(&mut token)
        .unwrap();
    let client = SyncClient::new(&binding.endpoint, token, true).unwrap();
    client.push_one(&ws, &binding).await.unwrap();
}
