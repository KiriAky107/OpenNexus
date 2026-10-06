#![cfg(feature = "desktop")]

use notesagent_host::{sync_client::SyncClient, sync_state::Binding, workspace::Workspace};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
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

#[derive(Deserialize)]
struct File {
    path: String,
    content: String,
}
#[derive(Deserialize)]
struct Step {
    kind: String,
    old_path: String,
    new_path: String,
    note_path: String,
    note_content: String,
    linked_paths: Vec<String>,
}
#[derive(Deserialize)]
struct Fixture {
    schema: u8,
    initial_note_path: String,
    files: Vec<File>,
    steps: Vec<Step>,
}
type Device = Arc<Mutex<Workspace>>;
fn hash_bytes(content: &[u8]) -> String {
    format!("{:x}", Sha256::digest(content))
}

async fn push_all(client: &SyncClient, device: &Device, binding: &Binding) -> usize {
    let mut count = 0;
    while client.push_one(device, binding).await.unwrap() {
        count += 1;
        assert!(count <= 32, "Unbounded upload or rename echo");
    }
    count
}
async fn pull_all(client: &SyncClient, device: &Device, binding: &Binding) -> usize {
    let mut count = 0;
    for _ in 0..32 {
        let received = client.pull_page(device, binding).await.unwrap();
        count += received;
        if received == 0 {
            return count;
        }
    }
    panic!("Unbounded download or rename echo");
}
fn reopen(device: Device, root: &Path) -> Device {
    assert_eq!(Arc::strong_count(&device), 1);
    drop(Arc::try_unwrap(device).ok().unwrap().into_inner().unwrap());
    Arc::new(Mutex::new(Workspace::open(root).unwrap()))
}
fn assert_device(
    device: &Device,
    binding: &Binding,
    files: &BTreeMap<String, (String, String)>,
    expected_cursor: i64,
) {
    let mut ws = device.lock().unwrap();
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, expected_cursor);
    assert_eq!(ws.sync_binding().unwrap().unwrap().id, binding.id);
    assert_eq!(ws.pending_count().unwrap(), 0);
    assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
    let tree = ws.tree().unwrap();
    assert_eq!(
        tree.iter().filter(|entry| !entry.is_folder).count(),
        files.len()
    );
    for (path, (id, content)) in files {
        let document = ws.read(path).unwrap();
        assert_eq!(&document.entry.file_id, id, "Identity changed: {path}");
        assert_eq!(ws.path_for_id(id).unwrap(), *path);
        assert_eq!(&document.content, content, "Content changed: {path}");
        assert_eq!(document.entry.hash, hash_bytes(content.as_bytes()));
    }
}

#[tokio::test]
async fn real_service_preserves_experiment_links_ids_and_bytes_after_moves_and_restarts() {
    let fixture: Fixture = serde_json::from_str(include_str!(
        "../../src/services/fixtures/experiment-links-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture.schema, 1);
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join(".opennexus-test"), b"fixture").unwrap();
    let service = std::env::var_os("OPENNEXUS_SYNC_SERVER_DIR")
        .map(PathBuf::from)
        .expect("Set OPENNEXUS_SYNC_SERVER_DIR to the fixed isolated Sync fixture");
    let python = service.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let mut server = Server(
        Command::new(python)
            .args(["-m", "tests.host_fixture"])
            .arg(root.path())
            .current_dir(&service)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(
                fs::File::create(root.path().join("server.log")).unwrap(),
            ))
            .spawn()
            .unwrap(),
    );
    let mut line = String::new();
    BufReader::new(server.0.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let ready: Value = serde_json::from_str(&line).unwrap_or_else(|error| {
        panic!(
            "Fixture startup failed: {error}; {}",
            fs::read_to_string(root.path().join("server.log")).unwrap()
        )
    });
    let endpoint = format!("http://127.0.0.1:{}", ready["port"]);
    let public = SyncClient::new(&endpoint, Zeroizing::new(String::new()), true).unwrap();
    let first = public
        .login(
            "rust-fixture",
            Zeroizing::new("controlled-fixture-password".into()),
            "Experiment A",
        )
        .await
        .unwrap();
    let second = public
        .login(
            "rust-fixture",
            Zeroizing::new("controlled-fixture-password".into()),
            "Experiment B",
        )
        .await
        .unwrap();
    assert_ne!(first.device_id, second.device_id);
    let a = SyncClient::new(&endpoint, Zeroizing::new(first.access_token.clone()), true).unwrap();
    let b = SyncClient::new(&endpoint, Zeroizing::new(second.access_token.clone()), true).unwrap();
    let capabilities = a.capabilities().await.unwrap();
    assert_eq!(capabilities.encryption, "transport-only");
    assert!(!capabilities.features.unwrap().execution);
    let remote = a
        .json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":"Experiment link identities"})),
        )
        .await
        .unwrap();
    let vault_id = remote["vault_id"].as_str().unwrap();
    let root_a = root.path().join("device-a");
    let root_b = root.path().join("device-b");
    fs::create_dir(&root_a).unwrap();
    fs::create_dir(&root_b).unwrap();
    let mut ws_a = Workspace::open(&root_a).unwrap();
    let mut expected = BTreeMap::new();
    for file in fixture.files {
        let entry = ws_a
            .write(&file.path, "", file.content.as_bytes(), "local")
            .unwrap();
        expected.insert(file.path, (entry.file_id, file.content));
    }
    let binding_a = ws_a
        .sync_bind_empty(&endpoint, vault_id, "rust-fixture")
        .unwrap();
    let mut ws_b = Workspace::open(&root_b).unwrap();
    let binding_b = ws_b
        .sync_bind_download(&endpoint, vault_id, "rust-fixture")
        .unwrap();
    let mut device_a = Arc::new(Mutex::new(ws_a));
    let mut device_b = Arc::new(Mutex::new(ws_b));
    let mut cursor = push_all(&a, &device_a, &binding_a).await as i64;
    assert_eq!(cursor, 5);
    assert_eq!(pull_all(&b, &device_b, &binding_b).await, 5);
    assert_eq!(pull_all(&a, &device_a, &binding_a).await, 5);
    assert_device(&device_a, &binding_a, &expected, cursor);
    assert_device(&device_b, &binding_b, &expected, cursor);
    let mut note_path = fixture.initial_note_path;
    for step in fixture.steps {
        // Apply exactly the content reviewed by the frontend's shared contract.
        // Moving and rewriting the referring note are distinct CAS operations.
        let rewrote_note;
        {
            let mut ws = device_a.lock().unwrap();
            if step.kind == "folder" {
                let manifest = expected
                    .iter()
                    .filter_map(|(path, (_, content))| {
                        path.strip_prefix(&(step.old_path.clone() + "/"))
                            .map(|relative| (relative.to_owned(), hash_bytes(content.as_bytes())))
                    })
                    .collect();
                ws.mutate_directory(&step.old_path, &step.new_path, "rename", &manifest)
                    .unwrap();
            } else {
                let before = ws.read(&step.old_path).unwrap();
                ws.rename(&step.old_path, &step.new_path, &before.entry.hash)
                    .unwrap();
            }
            let old_prefix = step.old_path.clone() + "/";
            expected = expected
                .into_iter()
                .map(|(path, value)| {
                    if path == step.old_path || path.starts_with(&old_prefix) {
                        (
                            format!("{}{}", step.new_path, &path[step.old_path.len()..]),
                            value,
                        )
                    } else {
                        (path, value)
                    }
                })
                .collect();
            let before = ws.read(&step.note_path).unwrap();
            rewrote_note = before.content != step.note_content;
            if rewrote_note {
                ws.write(
                    &step.note_path,
                    &before.entry.hash,
                    step.note_content.as_bytes(),
                    "local",
                )
                .unwrap();
            }
            expected.get_mut(&step.note_path).unwrap().1 = step.note_content.clone();
            assert_eq!(
                ws.path_for_id(&expected[&step.note_path].0).unwrap(),
                step.note_path
            );
        }
        let uploaded = push_all(&a, &device_a, &binding_a).await;
        assert_eq!(
            uploaded,
            if step.kind == "folder" { 3 } else { 1 } + usize::from(rewrote_note)
        );
        cursor += uploaded as i64;
        assert_eq!(pull_all(&b, &device_b, &binding_b).await, uploaded);
        assert_eq!(pull_all(&a, &device_a, &binding_a).await, uploaded);
        device_a = reopen(device_a, &root_a);
        device_b = reopen(device_b, &root_b);
        assert_device(&device_a, &binding_a, &expected, cursor);
        assert_device(&device_b, &binding_b, &expected, cursor);
        for path in &step.linked_paths {
            assert!(
                root_b.join(path).is_file(),
                "Synced link target missing: {path}"
            );
        }
        if note_path != step.note_path {
            assert!(!root_b.join(&note_path).exists());
        }
        note_path = step.note_path;
    }
    // A real edit on the receiving device must flow back without an ID change.
    let edited_path = expected
        .keys()
        .find(|path| path.ends_with(".json"))
        .unwrap()
        .clone();
    let edited = "{\"values\":[4,5,6],\"label\":\"双端😀\"}\r\n";
    {
        let mut ws = device_b.lock().unwrap();
        let before = ws.read(&edited_path).unwrap();
        ws.write(&edited_path, &before.entry.hash, edited.as_bytes(), "local")
            .unwrap();
    }
    expected.get_mut(&edited_path).unwrap().1 = edited.into();
    assert_eq!(push_all(&b, &device_b, &binding_b).await, 1);
    cursor += 1;
    assert_eq!(pull_all(&a, &device_a, &binding_a).await, 1);
    assert_eq!(pull_all(&b, &device_b, &binding_b).await, 1);
    device_a = reopen(device_a, &root_a);
    device_b = reopen(device_b, &root_b);
    assert_device(&device_a, &binding_a, &expected, cursor);
    assert_device(&device_b, &binding_b, &expected, cursor);
    assert_eq!(cursor, 21);
    assert!(!root_a.join("must-not-run.txt").exists());
    assert!(!root_b.join("must-not-run.txt").exists());
    assert_eq!(
        device_b.lock().unwrap().read(&note_path).unwrap().content,
        expected[&note_path].1
    );
    println!("EXPERIMENT_SYNC_VERIFIED devices=2 files=5 reviewed_moves=7 final_cursor={cursor} reverse_edits=1 execution=false");
}
