#![cfg(feature = "desktop")]

use notesagent_host::{
    records, sync_client::SyncClient, sync_scope::OptionalScope, workspace::Workspace,
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

fn record(kind: &str, id: &str, data: Value) -> Value {
    json!({"schema":1,"kind":kind,"id":id,"data":data})
}

fn records_fixture() -> Vec<(&'static str, &'static str, Value, bool)> {
    let task = "task_00000000000000000000000000000001";
    let skill = "user_skill_00000000000000000000000000000001";
    let conversation = "conversation_00000000000000000000000000000001";
    let run = "agent_run_00000000000000000000000000000001";
    let provider = "provider_00000000000000000000000000000001";
    let extension = "extension_00000000000000000000000000000001";
    vec![
        (
            "task",
            task,
            record(
                "task",
                task,
                json!({"title":"Ship","description":"Production checklist","status":"todo","note_id":null,"due_at_ms":null,"created_at_ms":1,"updated_at_ms":2}),
            ),
            false,
        ),
        (
            "user_skill",
            skill,
            record(
                "user_skill",
                skill,
                json!({"version":1,"name":"Review","description":"Review a note","prompt":"Check carefully","tools":["notes.read"],"permissions":["notes.read"],"retrieval":{"top_k":10,"rerank":true,"citation":true},"required_capabilities":["chat"],"created_at_ms":1,"updated_at_ms":2}),
            ),
            false,
        ),
        (
            "theme_settings",
            "appearance",
            record(
                "theme_settings",
                "appearance",
                json!({"themeId":"dark","fontEditorSize":18,"fontEditorFamily":"system-ui","lineHeight":1.7,"codeBlockTheme":"auto","headings":{"custom":false,"family":"inherit","levels":([32,28,24,21,18,16].map(|size|json!({"size":size,"weight":700})))}}),
            ),
            false,
        ),
        (
            "preferences",
            "editor",
            record(
                "preferences",
                "editor",
                json!({"restoreLastVault":true,"autoSaveInterval":1500,"language":"zh-CN","defaultEditorMode":"wysiwyg","editorLineWidth":80,"spellCheck":false,"markdown":{"heading":"atx","bullet":"-","incrementList":true,"fence":"`","math":true,"callouts":true,"diagrams":true,"autoLinks":true,"lineNumbers":true,"wrapCode":false,"indent":4,"defaultLanguage":""},"presets":[]}),
            ),
            false,
        ),
        (
            "persona",
            "default",
            record(
                "persona",
                "default",
                json!({"version":1,"name":"Writer","system_prompt":"Be concise","dialogue_pairs":[]}),
            ),
            true,
        ),
        (
            "layout",
            "sidebars",
            record(
                "layout",
                "sidebars",
                json!({"primaryExpanded":true,"workspaceWidth":400,"chatWidth":320}),
            ),
            true,
        ),
        (
            "conversation",
            conversation,
            record(
                "conversation",
                conversation,
                json!({"title":"Release review","active_leaf":"message_1","created_at_ms":1,"updated_at_ms":2,"messages":[{"message_id":"message_1","parent_message_id":null,"role":"user","content":"Review the release","thinking":null,"attachments":[],"created_at_ms":1}]}),
            ),
            true,
        ),
        (
            "agent_history",
            run,
            record(
                "agent_history",
                run,
                json!({"status":"completed","input":"Review release","output":"Ready","model":"gpt-5.6","skill_id":null,"error_code":null,"error_message":null,"token_usage":42,"created_at_ms":1,"updated_at_ms":2}),
            ),
            true,
        ),
        (
            "provider_settings",
            provider,
            record(
                "provider_settings",
                provider,
                json!({"version":1,"provider_type":"openai_responses","name":"OpenAI","base_url":"https://api.openai.com/v1","default_model":"gpt-5.6","enabled":true,"capabilities":["chat","tool_calling"]}),
            ),
            true,
        ),
        (
            "extension_installation",
            extension,
            record(
                "extension_installation",
                extension,
                json!({"package_kind":"plugin","package_id":"opennexus.review","source":"community/opennexus.review","version":"1.2.3","sha256":"a".repeat(64)}),
            ),
            true,
        ),
    ]
}

fn seed(workspace: &mut Workspace) -> Vec<(String, Value, bool)> {
    workspace
        .write("notes/default.md", "", b"# Default markdown", "local")
        .unwrap();
    workspace
        .write(
            "attachments/manual.pdf",
            "",
            b"portable attachment",
            "local",
        )
        .unwrap();
    let mut written = Vec::new();
    for (kind, id, value, optional) in records_fixture() {
        let path = records::path_for(kind, id).unwrap();
        workspace
            .write(&path, "", &serde_json::to_vec(&value).unwrap(), "local")
            .unwrap();
        written.push((path, value, optional));
    }
    for (path, contents) in [
        ("logs/trace.md", "forbidden log"),
        ("cache/search.md", "forbidden cache"),
        ("models/local.md", "forbidden model"),
        ("index.sqlite3", "forbidden index"),
        ("vectors.bin", "forbidden vectors"),
        ("provider-settings.json", "fixture-api-key"),
    ] {
        workspace
            .write(path, "", contents.as_bytes(), "local")
            .unwrap();
    }
    written
}

async fn push_all(
    client: &SyncClient,
    workspace: &Arc<Mutex<Workspace>>,
    binding: &notesagent_host::sync_state::Binding,
) {
    while client.push_one(workspace, binding).await.unwrap() {}
}

async fn pull_all(
    client: &SyncClient,
    workspace: &Arc<Mutex<Workspace>>,
    binding: &notesagent_host::sync_state::Binding,
) {
    while client.pull_page(workspace, binding).await.unwrap() > 0 {}
}

async fn create_remote(client: &SyncClient, name: &str) -> String {
    client
        .json(
            reqwest::Method::POST,
            "sync/v1/vaults",
            Some(json!({"name":name})),
        )
        .await
        .unwrap()["vault_id"]
        .as_str()
        .unwrap()
        .to_owned()
}

#[tokio::test]
async fn s08_actual_service_enforces_classification_and_roundtrips_selected_records() {
    let service_root = tempfile::tempdir().unwrap();
    std::fs::write(service_root.path().join(".opennexus-test"), b"fixture").unwrap();
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
            .arg(service_root.path())
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
            "S-08 classification",
        )
        .await
        .unwrap();
    let client = SyncClient::new(
        &endpoint,
        Zeroizing::new(session.access_token.clone()),
        true,
    )
    .unwrap();

    let default_remote = create_remote(&client, "S-08 default").await;
    let source_root = tempfile::tempdir().unwrap();
    let mut source = Workspace::open(source_root.path()).unwrap();
    let written = seed(&mut source);
    let source = Arc::new(Mutex::new(source));
    let source_binding = source
        .lock()
        .unwrap()
        .sync_bind_empty(&endpoint, &default_remote, "rust-fixture")
        .unwrap();
    push_all(&client, &source, &source_binding).await;
    let default_snapshot = client.snapshot(&default_remote).await.unwrap();
    let default_paths = default_snapshot
        .items
        .iter()
        .map(|item| item.path.as_str())
        .collect::<Vec<_>>();
    assert_eq!(default_paths.len(), 6);
    assert!(default_paths.contains(&"notes/default.md"));
    assert!(default_paths.contains(&"attachments/manual.pdf"));
    assert!(written
        .iter()
        .filter(|(_, _, optional)| !optional)
        .all(|(path, _, _)| default_paths.contains(&path.as_str())));
    assert!(written
        .iter()
        .filter(|(_, _, optional)| *optional)
        .all(|(path, _, _)| !default_paths.contains(&path.as_str())));
    assert!(default_paths.iter().all(|path| {
        !path.starts_with("logs/")
            && !path.starts_with("cache/")
            && !path.starts_with("models/")
            && !path.contains("index")
            && !path.contains("vectors")
            && !path.contains("provider-settings.json")
    }));

    let target_root = tempfile::tempdir().unwrap();
    let target = Arc::new(Mutex::new(Workspace::open(target_root.path()).unwrap()));
    let target_binding = target
        .lock()
        .unwrap()
        .sync_bind_download(&endpoint, &default_remote, "rust-fixture")
        .unwrap();
    pull_all(&client, &target, &target_binding).await;
    assert_eq!(
        target
            .lock()
            .unwrap()
            .read("notes/default.md")
            .unwrap()
            .content,
        "# Default markdown"
    );
    assert_eq!(
        target
            .lock()
            .unwrap()
            .read("attachments/manual.pdf")
            .unwrap()
            .content,
        "portable attachment"
    );
    for (path, value, optional) in &written {
        if *optional {
            assert!(!target_root.path().join(path).exists());
        } else {
            assert_eq!(
                serde_json::from_slice::<Value>(
                    &std::fs::read(target_root.path().join(path)).unwrap()
                )
                .unwrap(),
                *value
            );
        }
    }

    let optional_remote = create_remote(&client, "S-08 optional").await;
    let optional_source_root = tempfile::tempdir().unwrap();
    let mut optional_source = Workspace::open(optional_source_root.path()).unwrap();
    optional_source
        .sync_set_optional_scope(OptionalScope {
            persona: true,
            layout: true,
            conversations: true,
            agent_history: true,
            provider_settings: true,
            extension_installations: true,
        })
        .unwrap();
    let optional_written = seed(&mut optional_source);
    let optional_source = Arc::new(Mutex::new(optional_source));
    let optional_source_binding = optional_source
        .lock()
        .unwrap()
        .sync_bind_empty(&endpoint, &optional_remote, "rust-fixture")
        .unwrap();
    push_all(&client, &optional_source, &optional_source_binding).await;
    assert_eq!(
        client.snapshot(&optional_remote).await.unwrap().items.len(),
        12
    );

    let optional_target_root = tempfile::tempdir().unwrap();
    let mut optional_target = Workspace::open(optional_target_root.path()).unwrap();
    optional_target
        .sync_set_optional_scope(OptionalScope {
            persona: true,
            layout: true,
            conversations: true,
            agent_history: true,
            provider_settings: true,
            extension_installations: true,
        })
        .unwrap();
    let optional_target = Arc::new(Mutex::new(optional_target));
    let optional_target_binding = optional_target
        .lock()
        .unwrap()
        .sync_bind_download(&endpoint, &optional_remote, "rust-fixture")
        .unwrap();
    pull_all(&client, &optional_target, &optional_target_binding).await;
    for (path, value, _) in &optional_written {
        assert_eq!(
            serde_json::from_slice::<Value>(
                &std::fs::read(optional_target_root.path().join(path)).unwrap()
            )
            .unwrap(),
            *value
        );
    }
    let installation = &optional_written
        .iter()
        .find(|(_, value, _)| value["kind"] == "extension_installation")
        .unwrap()
        .1["data"];
    let keys = installation
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    assert_eq!(keys.len(), 5);
    assert!(keys.iter().all(|key| {
        matches!(
            key.as_str(),
            "package_kind" | "package_id" | "source" | "version" | "sha256"
        )
    }));
}
