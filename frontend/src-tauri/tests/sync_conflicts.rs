#![cfg(feature = "desktop")]

use notesagent_host::{
    sync_client::SyncClient,
    sync_state::Binding,
    workspace::{hash, Workspace},
};
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

#[derive(Clone, Copy)]
enum ConflictKind {
    SameEdit,
    EditDelete,
    EditRename,
    SameTargetRename,
    HistoryRestore,
}

impl ConflictKind {
    fn name(self) -> &'static str {
        match self {
            Self::SameEdit => "same-edit",
            Self::EditDelete => "edit-delete",
            Self::EditRename => "edit-rename",
            Self::SameTargetRename => "same-target-rename",
            Self::HistoryRestore => "history-restore",
        }
    }
}

async fn push_all(client: &SyncClient, workspace: &Arc<Mutex<Workspace>>, binding: &Binding) {
    while client.push_one(workspace, binding).await.unwrap() {}
}

async fn pull_all(client: &SyncClient, workspace: &Arc<Mutex<Workspace>>, binding: &Binding) {
    while client.pull_page(workspace, binding).await.unwrap() > 0 {}
}

struct SyncPair<'a> {
    client_a: &'a SyncClient,
    client_b: &'a SyncClient,
    workspace_a: &'a Arc<Mutex<Workspace>>,
    workspace_b: &'a Arc<Mutex<Workspace>>,
    binding_a: &'a Binding,
    binding_b: &'a Binding,
}

impl SyncPair<'_> {
    async fn resolve_copy_and_converge(
        &self,
        copy_path: &str,
        local_content: &str,
        main_path: &str,
        remote_content: Option<&str>,
    ) {
        let conflict = self
            .workspace_b
            .lock()
            .unwrap()
            .sync_conflicts(&self.binding_b.id)
            .unwrap()
            .remove(0);
        let local_hash = hash(local_content.as_bytes());
        assert_eq!(conflict["current_hash"], local_hash);
        {
            let mut ws = self.workspace_b.lock().unwrap();
            assert!(ws.sync_spool(&local_hash).unwrap().is_file());
            if let Some(remote_hash) = conflict["remote"]["hash"].as_str() {
                assert!(ws.sync_spool(remote_hash).unwrap().is_file());
            }
            ws.sync_resolve(
                &self.binding_b.id,
                conflict["sequence"].as_i64().unwrap(),
                "copy",
                copy_path,
                &local_hash,
            )
            .unwrap();
        }
        push_all(self.client_b, self.workspace_b, self.binding_b).await;
        pull_all(self.client_a, self.workspace_a, self.binding_a).await;
        pull_all(self.client_b, self.workspace_b, self.binding_b).await;

        let copy_a = self.workspace_a.lock().unwrap().read(copy_path).unwrap();
        let copy_b = self.workspace_b.lock().unwrap().read(copy_path).unwrap();
        assert_eq!(copy_a.content, local_content);
        assert_eq!(copy_a.entry.hash, copy_b.entry.hash);
        assert_eq!(copy_a.entry.file_id, copy_b.entry.file_id);
        for workspace in [self.workspace_a, self.workspace_b] {
            let mut ws = workspace.lock().unwrap();
            if let Some(remote_content) = remote_content {
                assert_eq!(ws.read(main_path).unwrap().content, remote_content);
            } else {
                assert!(!ws.root.join(main_path).exists());
            }
        }
        assert!(self
            .workspace_a
            .lock()
            .unwrap()
            .sync_conflicts(&self.binding_a.id)
            .unwrap()
            .is_empty());
        assert!(self
            .workspace_b
            .lock()
            .unwrap()
            .sync_conflicts(&self.binding_b.id)
            .unwrap()
            .is_empty());
    }
}

#[tokio::test]
async fn s03_actual_service_converges_five_conflict_classes_twenty_rounds() {
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
    let session_a = public
        .login(
            "rust-fixture",
            Zeroizing::new("controlled-fixture-password".into()),
            "S-03 A",
        )
        .await
        .unwrap();
    let session_b = public
        .login(
            "rust-fixture",
            Zeroizing::new("controlled-fixture-password".into()),
            "S-03 B",
        )
        .await
        .unwrap();
    let client_a = SyncClient::new(
        &endpoint,
        Zeroizing::new(session_a.access_token.clone()),
        true,
    )
    .unwrap();
    let client_b = SyncClient::new(
        &endpoint,
        Zeroizing::new(session_b.access_token.clone()),
        true,
    )
    .unwrap();
    let vault = client_a
        .json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":"S-03 matrix"})),
        )
        .await
        .unwrap();
    let remote = vault["vault_id"].as_str().unwrap();
    client_a.verify_empty(remote).await.unwrap();

    let root_a = tempfile::tempdir().unwrap();
    let root_b = tempfile::tempdir().unwrap();
    let workspace_a = Arc::new(Mutex::new(Workspace::open(root_a.path()).unwrap()));
    let workspace_b = Arc::new(Mutex::new(Workspace::open(root_b.path()).unwrap()));
    workspace_a
        .lock()
        .unwrap()
        .write("first-bind.md", "", b"local snapshot", "local")
        .unwrap();
    let binding_a = workspace_a
        .lock()
        .unwrap()
        .sync_bind_empty(&endpoint, remote, "rust-fixture")
        .unwrap();
    let first_job = workspace_a
        .lock()
        .unwrap()
        .sync_next(&binding_a.id)
        .unwrap()
        .unwrap();
    assert_eq!(first_job.operation, "put");
    let before = client_a
        .json(
            reqwest::Method::GET,
            &format!("sync/v1/vaults/{remote}/changes"),
            None,
        )
        .await
        .unwrap();
    assert_eq!(before["boundary"], 0);
    assert!(before["items"].as_array().unwrap().is_empty());
    push_all(&client_a, &workspace_a, &binding_a).await;
    let after = client_a
        .json(
            reqwest::Method::GET,
            &format!("sync/v1/vaults/{remote}/changes"),
            None,
        )
        .await
        .unwrap();
    assert_eq!(
        after["items"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|revision| revision["operation"] == "delete")
            .count(),
        0
    );
    let binding_b = workspace_b
        .lock()
        .unwrap()
        .sync_bind_download(&endpoint, remote, "rust-fixture")
        .unwrap();
    pull_all(&client_b, &workspace_b, &binding_b).await;
    let pair = SyncPair {
        client_a: &client_a,
        client_b: &client_b,
        workspace_a: &workspace_a,
        workspace_b: &workspace_b,
        binding_a: &binding_a,
        binding_b: &binding_b,
    };

    let kinds = [
        ConflictKind::SameEdit,
        ConflictKind::EditDelete,
        ConflictKind::EditRename,
        ConflictKind::SameTargetRename,
        ConflictKind::HistoryRestore,
    ];
    for kind in kinds {
        for round in 0..20 {
            let stem = format!("matrix/{}-{round}", kind.name());
            let source = format!("{stem}-source.md");
            let target = match kind {
                ConflictKind::EditRename | ConflictKind::SameTargetRename => {
                    format!("{stem}-target.md")
                }
                _ => source.clone(),
            };
            let right = format!("{stem}-right.md");
            let (base, local_content, remote_content) = match kind {
                ConflictKind::SameEdit => (
                    "base".to_owned(),
                    format!("local-edit-{round}"),
                    Some(format!("remote-edit-{round}")),
                ),
                ConflictKind::EditDelete => {
                    ("base".to_owned(), format!("local-survivor-{round}"), None)
                }
                ConflictKind::EditRename => (
                    "rename-base".to_owned(),
                    format!("edited-before-rename-{round}"),
                    Some("rename-base".to_owned()),
                ),
                ConflictKind::SameTargetRename => (
                    "left-content".to_owned(),
                    "right-content".to_owned(),
                    Some("left-content".to_owned()),
                ),
                ConflictKind::HistoryRestore => (
                    "historical".to_owned(),
                    format!("local-history-{round}"),
                    Some(format!("remote-history-{round}")),
                ),
            };
            workspace_a
                .lock()
                .unwrap()
                .write(&source, "", base.as_bytes(), "local")
                .unwrap();
            if matches!(kind, ConflictKind::SameTargetRename) {
                workspace_a
                    .lock()
                    .unwrap()
                    .write(&right, "", local_content.as_bytes(), "local")
                    .unwrap();
            }
            push_all(&client_a, &workspace_a, &binding_a).await;
            pull_all(&client_b, &workspace_b, &binding_b).await;

            if matches!(kind, ConflictKind::HistoryRestore) {
                {
                    let mut ws = workspace_a.lock().unwrap();
                    let current = ws.read(&source).unwrap();
                    ws.delete(&source, &current.entry.hash).unwrap();
                }
                push_all(&client_a, &workspace_a, &binding_a).await;
                pull_all(&client_b, &workspace_b, &binding_b).await;
            }

            match kind {
                ConflictKind::SameEdit => {
                    for (workspace, content) in [
                        (&workspace_a, remote_content.as_deref().unwrap()),
                        (&workspace_b, local_content.as_str()),
                    ] {
                        let mut ws = workspace.lock().unwrap();
                        let current = ws.read(&source).unwrap();
                        ws.write(&source, &current.entry.hash, content.as_bytes(), "local")
                            .unwrap();
                    }
                }
                ConflictKind::EditDelete => {
                    {
                        let mut ws = workspace_a.lock().unwrap();
                        let current = ws.read(&source).unwrap();
                        ws.delete(&source, &current.entry.hash).unwrap();
                    }
                    {
                        let mut ws = workspace_b.lock().unwrap();
                        let current = ws.read(&source).unwrap();
                        ws.write(
                            &source,
                            &current.entry.hash,
                            local_content.as_bytes(),
                            "local",
                        )
                        .unwrap();
                    }
                }
                ConflictKind::EditRename => {
                    {
                        let mut ws = workspace_a.lock().unwrap();
                        let current = ws.read(&source).unwrap();
                        ws.rename(&source, &target, &current.entry.hash).unwrap();
                    }
                    {
                        let mut ws = workspace_b.lock().unwrap();
                        let current = ws.read(&source).unwrap();
                        ws.write(
                            &source,
                            &current.entry.hash,
                            local_content.as_bytes(),
                            "local",
                        )
                        .unwrap();
                    }
                }
                ConflictKind::SameTargetRename => {
                    {
                        let mut ws = workspace_a.lock().unwrap();
                        let current = ws.read(&source).unwrap();
                        ws.rename(&source, &target, &current.entry.hash).unwrap();
                    }
                    {
                        let mut ws = workspace_b.lock().unwrap();
                        let current = ws.read(&right).unwrap();
                        ws.rename(&right, &target, &current.entry.hash).unwrap();
                    }
                }
                ConflictKind::HistoryRestore => {
                    workspace_a
                        .lock()
                        .unwrap()
                        .write(
                            &source,
                            "",
                            remote_content.as_deref().unwrap().as_bytes(),
                            "local",
                        )
                        .unwrap();
                    workspace_b
                        .lock()
                        .unwrap()
                        .write(&source, "", local_content.as_bytes(), "local")
                        .unwrap();
                }
            }
            push_all(&client_a, &workspace_a, &binding_a).await;
            pull_all(&client_b, &workspace_b, &binding_b).await;
            let copy = format!("copies/{}-{round}.md", kind.name());
            pair.resolve_copy_and_converge(
                &copy,
                &local_content,
                &target,
                remote_content.as_deref(),
            )
            .await;
            if matches!(
                kind,
                ConflictKind::EditRename | ConflictKind::SameTargetRename
            ) {
                assert!(!workspace_a.lock().unwrap().root.join(&source).exists());
                assert!(!workspace_b.lock().unwrap().root.join(&source).exists());
            }
            if matches!(kind, ConflictKind::SameTargetRename) {
                let right_a = workspace_a.lock().unwrap().read(&right).unwrap();
                let right_b = workspace_b.lock().unwrap().read(&right).unwrap();
                assert_eq!(right_a.content, "right-content");
                assert_eq!(right_a.entry.hash, right_b.entry.hash);
                assert_eq!(right_a.entry.file_id, right_b.entry.file_id);
            }
        }
    }

    let rebound_vault = client_a
        .json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":"S-03 rebind"})),
        )
        .await
        .unwrap();
    let rebound_remote = rebound_vault["vault_id"].as_str().unwrap();
    let rebound_root = tempfile::tempdir().unwrap();
    let rebound_ws = Arc::new(Mutex::new(Workspace::open(rebound_root.path()).unwrap()));
    rebound_ws
        .lock()
        .unwrap()
        .write("rebound.md", "", b"current", "local")
        .unwrap();
    let old_binding = rebound_ws
        .lock()
        .unwrap()
        .sync_bind_empty(&endpoint, rebound_remote, "rust-fixture")
        .unwrap();
    let old_operation = rebound_ws
        .lock()
        .unwrap()
        .sync_next(&old_binding.id)
        .unwrap()
        .unwrap()
        .operation_id;
    rebound_ws
        .lock()
        .unwrap()
        .sync_unbind(&old_binding.id)
        .unwrap();
    let new_binding = rebound_ws
        .lock()
        .unwrap()
        .sync_bind_empty(&endpoint, rebound_remote, "rust-fixture")
        .unwrap();
    let new_operation = rebound_ws
        .lock()
        .unwrap()
        .sync_next(&new_binding.id)
        .unwrap()
        .unwrap()
        .operation_id;
    assert_ne!(old_operation, new_operation);
    assert_eq!(
        client_a
            .push_one(&rebound_ws, &old_binding)
            .await
            .unwrap_err()
            .code,
        "SYNC_BINDING_CHANGED"
    );
    push_all(&client_a, &rebound_ws, &new_binding).await;
    let rebound_changes = client_a
        .json(
            reqwest::Method::GET,
            &format!("sync/v1/vaults/{rebound_remote}/changes"),
            None,
        )
        .await
        .unwrap();
    let rebound_items = rebound_changes["items"].as_array().unwrap();
    assert_eq!(rebound_items.len(), 1);
    assert_eq!(rebound_items[0]["operation_id"], new_operation);
    assert!(rebound_items
        .iter()
        .all(|revision| revision["operation_id"] != old_operation));
}
