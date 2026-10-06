use super::*;
use crate::extension_store::{InstallRequest, Signer, Stage, TrustSetting};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signer as _, SigningKey};
use std::{
    io::{Cursor, Write},
    sync::atomic::{AtomicUsize, Ordering},
};

fn installed(manifest: Value) -> (tempfile::TempDir, ExtensionStore, Workspace, String) {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir(root.path().join("packages")).unwrap();
    std::fs::create_dir(root.path().join("vault")).unwrap();
    let ws = Workspace::open(&root.path().join("vault")).unwrap();
    let mut store = ExtensionStore::open(&root.path().join("packages")).unwrap();
    let mut vector: Value = serde_json::from_str(include_str!(
        "../../../src/services/fixtures/community-python-vector.json"
    ))
    .unwrap();
    for key in ["release_id", "withdrawn", "download_path"] {
        vector["release"].as_object_mut().unwrap().remove(key);
    }
    let mut release: crate::extension_package::Release =
        serde_json::from_value(vector["release"].clone()).unwrap();
    release.kind = "template".into();
    release.min_app_version = "0.6.0".into();
    release.permissions.clear();
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    zip.start_file("template.json", zip::write::SimpleFileOptions::default())
        .unwrap();
    zip.write_all(&serde_json::to_vec(&manifest).unwrap())
        .unwrap();
    let archive = zip.finish().unwrap().into_inner();
    release.sha256 = hash(&archive);
    release.size = archive.len() as u64;
    let signer = SigningKey::from_bytes(&[9; 32]);
    let key = signer.verifying_key().to_bytes();
    release.signature = STANDARD.encode(signer.sign(&release.signed_payload().unwrap()).to_bytes());
    let trust = TrustSetting {
        source: "https://catalog.example/".into(),
        source_id: "fixture".into(),
        namespace: release.namespace.clone(),
        key_id: release.key_id.clone(),
        public_key: key,
        enabled: true,
    };
    store
        .confirm_trust(&trust, None, &trust.fingerprint().unwrap())
        .unwrap();
    let receipt = store
        .stage(Stage {
            operation_id: &uuid::Uuid::new_v4().to_string(),
            source: &trust.source,
            release: &release,
            archive: &archive,
            withdrawn: false,
            signer: Signer {
                public_key: &key,
                key_id: &release.key_id,
                namespace: &release.namespace,
                revoked: false,
            },
        })
        .unwrap();
    let plan = store
        .installation_preview(&InstallRequest {
            root_key: receipt.package_key,
            vault_id: ws.vault_id.clone(),
            app_version: "0.6.0".into(),
            platform: "windows".into(),
            architecture: "x86_64".into(),
            configurations: Default::default(),
        })
        .unwrap();
    let operation = uuid::Uuid::new_v4().to_string();
    store
        .switch_prepared(&operation, &ws.vault_id, &plan.changes)
        .unwrap();
    store.finish_installation(&operation, true).unwrap();
    let slot = plan.changes[0].target.slot.clone();
    (root, store, ws, slot)
}
fn manifest() -> Value {
    json!({"markdown":"# {{title}}\r\nReview the source before running.","executable":false,"experiment":{"schema_version":1,"entry":"课程 #%.PY","inputs":["inputs/data.json","inputs/表格.csv"],"files":[{"path":"课程 #%.PY","content":"raise RuntimeError('must not run on import')\r\n"},{"path":"inputs/data.json","content":"{\"value\": 2}\r\n"},{"path":"inputs/表格.csv","content":"name,value\r\n中文,2\r\n"}]}})
}
fn request(ws: &Workspace, slot: &str, file: &TemplateFileReview) -> TemplateFileApply {
    TemplateFileApply {
        vault_id: ws.vault_id.clone(),
        slot: slot.into(),
        key: file.key.clone(),
        target: file.target.clone(),
        fingerprint: file.fingerprint.clone(),
        operation_id: uuid::Uuid::new_v4().to_string(),
    }
}
#[test]
fn preview_is_read_only_and_each_selected_file_keeps_utf8_crlf_and_identity() {
    let (root, store, mut ws, slot) = installed(manifest());
    let review = template_preview(
        &store,
        &mut ws,
        &slot,
        "experiments/课程 A",
        Some("课程说明.md"),
    )
    .unwrap();
    assert_eq!(review.files.len(), 4);
    assert_eq!(review.inputs.len(), 2);
    assert_eq!(
        review.entry.as_deref(),
        Some("experiments/课程 A/课程 #%.PY")
    );
    assert_eq!(ws.pending_count().unwrap(), 0);
    for file in &review.files {
        assert!(file.before.is_none());
        assert!(!root.path().join("vault").join(&file.target.path).exists());
        let apply = request(&ws, &slot, file);
        let receipt = template_apply_file(&store, &mut ws, &apply, || Ok(())).unwrap();
        assert_eq!(receipt["entry"]["file_id"], file.target.file_id);
        assert_eq!(receipt["entry"]["hash"], file.after_sha256);
        assert_eq!(
            std::fs::read(ws.resolve(&file.target.path).unwrap()).unwrap(),
            file.after.as_bytes()
        );
    }
    assert_eq!(ws.pending_count().unwrap(), 4);
    assert_eq!(
        ws.db
            .query_row("SELECT COUNT(*) FROM experiment_runs", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
}
#[test]
fn current_target_revision_and_identity_must_still_match_even_when_bytes_are_equal() {
    let (_root, store, mut ws, slot) = installed(manifest());
    let path = "existing.md";
    let first = ws.write(path, "", b"User note", "local").unwrap();
    let review =
        template_preview(&store, &mut ws, &slot, "experiments/course", Some(path)).unwrap();
    let file = &review.files[0];
    assert_eq!(file.before.as_deref(), Some("User note"));
    let apply = request(&ws, &slot, file);
    let edited = ws.write(path, &first.hash, b"User note", "local").unwrap();
    assert!(edited.revision > first.revision);
    assert!(template_apply_file(&store, &mut ws, &apply, || Ok(())).is_err());
    assert_eq!(ws.read(path).unwrap().content, "User note");
    assert!(ws.operation(&apply.operation_id).unwrap().is_none());
}
#[test]
fn committed_reply_replay_survives_restart_and_does_not_overwrite_later_edits() {
    let (root, store, mut ws, slot) = installed(manifest());
    let review = template_preview(
        &store,
        &mut ws,
        &slot,
        "experiments/course",
        Some("note.md"),
    )
    .unwrap();
    let file = &review.files[0];
    let apply = request(&ws, &slot, file);
    let receipt = template_apply_file(&store, &mut ws, &apply, || Ok(())).unwrap();
    let before = ws.read("note.md").unwrap();
    ws.write("note.md", &before.entry.hash, b"Later edit", "local")
        .unwrap();
    drop(ws);
    let mut ws = Workspace::open(&root.path().join("vault")).unwrap();
    assert_eq!(
        template_apply_file(&store, &mut ws, &apply, || Ok(())).unwrap(),
        receipt
    );
    assert_eq!(ws.read("note.md").unwrap().content, "Later edit");
    assert_eq!(ws.pending_count().unwrap(), 2);
}
#[test]
fn cancellation_creates_no_write_and_previously_imported_files_are_kept() {
    let (_root, store, mut ws, slot) = installed(manifest());
    let review = template_preview(&store, &mut ws, &slot, "experiments/course", None).unwrap();
    let first = request(&ws, &slot, &review.files[0]);
    template_apply_file(&store, &mut ws, &first, || Ok(())).unwrap();
    let second = request(&ws, &slot, &review.files[1]);
    let calls = AtomicUsize::new(0);
    assert_eq!(
        template_apply_file(&store, &mut ws, &second, || {
            if calls.fetch_add(1, Ordering::SeqCst) >= 2 {
                Err(HostError::new("REQUEST_CANCELLED"))
            } else {
                Ok(())
            }
        })
        .unwrap_err()
        .code,
        "REQUEST_CANCELLED"
    );
    assert!(ws.operation(&second.operation_id).unwrap().is_none());
    assert_eq!(ws.pending_count().unwrap(), 1);
    assert_eq!(
        ws.read(&review.files[0].target.path).unwrap().content,
        review.files[0].after
    );
    assert!(!ws.resolve(&review.files[1].target.path).unwrap().exists());
    assert!(!ws.resolve(&review.files[2].target.path).unwrap().exists());
}
#[test]
fn changed_vault_file_key_path_identity_or_hash_cannot_reuse_a_review() {
    let (_root, store, mut ws, slot) = installed(manifest());
    let review = template_preview(&store, &mut ws, &slot, "experiments/course", None).unwrap();
    for change in ["vault", "key", "path", "identity", "hash"] {
        let mut apply = request(&ws, &slot, &review.files[0]);
        match change {
            "vault" => apply.vault_id = uuid::Uuid::new_v4().to_string(),
            "key" => apply.key = "inputs/data.json".into(),
            "path" => apply.target.path = "experiments/other.py".into(),
            "identity" => apply.target.file_id = uuid::Uuid::new_v4().to_string(),
            _ => {
                apply.target.hash = "a".repeat(64);
                apply.target.revision = 1;
            }
        }
        assert!(template_apply_file(&store, &mut ws, &apply, || Ok(())).is_err());
    }
    assert_eq!(ws.pending_count().unwrap(), 0);
}
#[test]
fn unsafe_target_roots_and_record_paths_are_refused_and_legacy_markdown_works() {
    let (_root, store, mut ws, slot) = installed(manifest());
    for directory in [
        "notes",
        "../experiments",
        "experiments/../notes",
        "experiments/.git",
        "experiments/NUL",
    ] {
        assert!(template_preview(&store, &mut ws, &slot, directory, None).is_err());
    }
    for note in [
        "../outside.md",
        "opennexus-records/default.md",
        ".ainote/note.md",
        "experiments/other.py",
    ] {
        assert!(
            template_preview(&store, &mut ws, &slot, "experiments/course", Some(note)).is_err()
        );
    }
    assert_eq!(ws.pending_count().unwrap(), 0);
    let (_root, store, mut ws, slot) = installed(json!({"markdown":"# Legacy template"}));
    let review = template_preview(&store, &mut ws, &slot, "", Some("legacy.md")).unwrap();
    assert_eq!(review.files.len(), 1);
    assert!(review.entry.is_none());
    let apply = request(&ws, &slot, &review.files[0]);
    template_apply_file(&store, &mut ws, &apply, || Ok(())).unwrap();
    assert_eq!(ws.read("legacy.md").unwrap().content, "# Legacy template");
}

#[test]
fn linked_target_files_are_refused_without_reading_an_importable_review() {
    let (root, store, mut ws, slot) = installed(manifest());
    let outside = root.path().join("outside.md");
    std::fs::write(&outside, b"Owned outside fixture").unwrap();
    let linked = root.path().join("vault/linked.md");
    std::fs::hard_link(&outside, &linked).unwrap();
    assert!(template_preview(
        &store,
        &mut ws,
        &slot,
        "experiments/course",
        Some("linked.md")
    )
    .is_err());
    assert_eq!(std::fs::read(&outside).unwrap(), b"Owned outside fixture");
    assert_eq!(ws.pending_count().unwrap(), 0);
}

#[test]
fn large_before_content_and_total_preview_bytes_are_bounded_without_writes() {
    let mut value = manifest();
    value["experiment"]["entry"] = json!("0.py");
    value["experiment"]["inputs"] = json!([]);
    value["experiment"]["files"] = json!((0..5)
        .map(|index| json!({"path":format!("{index}.py"),"content":"print(1)"}))
        .collect::<Vec<_>>());
    let (_root, store, mut ws, slot) = installed(value);
    ws.write("large.md", "", &vec![b'x'; MAX_REVIEW_BYTES + 1], "local")
        .unwrap();
    assert_eq!(
        template_preview(
            &store,
            &mut ws,
            &slot,
            "experiments/course",
            Some("large.md")
        )
        .unwrap_err()
        .code,
        "EXTENSION_TEMPLATE_TARGET_LIMIT"
    );
    for index in 0..5 {
        ws.write(
            &format!("experiments/course/{index}.py"),
            "",
            &vec![b'x'; MAX_REVIEW_BYTES],
            "local",
        )
        .unwrap();
    }
    let before = ws.pending_count().unwrap();
    assert_eq!(
        template_preview(&store, &mut ws, &slot, "experiments/course", None)
            .unwrap_err()
            .code,
        "EXTENSION_TEMPLATE_TARGET_LIMIT"
    );
    assert_eq!(ws.pending_count().unwrap(), before);
}

#[test]
#[ignore = "requires prepared locked embedded Python; independently approved native template workflow"]
fn imported_template_runs_only_after_independent_approval_and_preserves_origins() {
    use crate::{
        experiment_import::{ImportRequest, ImportState, Selection},
        experiment_input::RunRequest,
        experiment_policy::ExecutionLimits,
        experiment_runner::Runner,
        experiment_runtime::RUNTIME_ID,
        experiment_store::RunState,
    };
    use std::{
        path::PathBuf,
        sync::{Arc, Mutex},
        time::{Duration, Instant},
    };
    let _serial = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
    let project = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    let runtime = std::env::var_os("OPENNEXUS_PROBE_RUNTIME_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| project.join(".build/experiment-runtime").join(RUNTIME_ID));
    let mut value = manifest();
    value["experiment"]["files"][0]["content"] = json!(include_str!(
        "../../../../scripts/fixtures/community_template_probe.py"
    ));
    let (root, store, mut ws, slot) = installed(value);
    ws.write(
        "experiments/课程 A/not-selected.py",
        "",
        b"raise RuntimeError('unselected')",
        "local",
    )
    .unwrap();
    let original = ws
        .write("报告.md", "", b"Original user note", "local")
        .unwrap();
    let review = template_preview(&store, &mut ws, &slot, "experiments/课程 A", None).unwrap();
    let mut selected = Vec::new();
    for file in &review.files {
        let application = request(&ws, &slot, file);
        let receipt = template_apply_file(&store, &mut ws, &application, || Ok(())).unwrap();
        let entry: Entry = serde_json::from_value(receipt["entry"].clone()).unwrap();
        selected.push(SelectedFile {
            file_id: entry.file_id,
            path: entry.path,
            hash: entry.hash,
            revision: entry.revision,
        });
    }
    assert_eq!(
        ws.db
            .query_row("SELECT COUNT(*) FROM experiment_runs", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
    let pending_before_run = ws.pending_count().unwrap();
    let run = RunRequest {
        vault_id: ws.vault_id.clone(),
        operation_id: uuid::Uuid::new_v4().to_string(),
        runtime_id: RUNTIME_ID.into(),
        entry: selected.remove(0),
        inputs: selected,
        limits: ExecutionLimits {
            wall_seconds: 10,
            cpu_seconds: 3,
            disk_mib: 8,
            output_mib: 1,
            log_kib: 16,
            ..Default::default()
        },
    };
    let workspace = Arc::new(Mutex::new(Some(ws)));
    let host_root = tempfile::tempdir().unwrap();
    let runner = Runner::default();
    runner.initialize_cleanup(host_root.path()).unwrap();
    let waiting = runner.prepare(&workspace, &run).unwrap();
    assert_eq!(waiting.state, RunState::AwaitingConfirmation);
    assert!(runner
        .start_approved_for_probe(workspace.clone(), runtime.clone(), &run.operation_id)
        .is_err());
    assert_eq!(
        runner
            .record(&workspace, &run.operation_id)
            .unwrap()
            .unwrap()
            .state,
        RunState::AwaitingConfirmation
    );
    runner
        .confirm_from_user_for_vault(
            &workspace,
            &run.vault_id,
            &run.operation_id,
            &waiting.summary.fingerprint,
        )
        .unwrap();
    runner
        .start_approved_for_probe(workspace.clone(), runtime.clone(), &run.operation_id)
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(20);
    let finished = loop {
        let record = runner
            .record(&workspace, &run.operation_id)
            .unwrap()
            .unwrap();
        if matches!(
            record.state,
            RunState::Completed
                | RunState::Failed
                | RunState::Limited
                | RunState::Interrupted
                | RunState::Cancelled
        ) {
            break record;
        }
        assert!(
            Instant::now() < deadline,
            "native template worker did not finish"
        );
        std::thread::sleep(Duration::from_millis(10));
    };
    runner.shutdown().unwrap();
    assert_eq!(
        finished.state,
        RunState::Completed,
        "{}",
        serde_json::to_string(&finished).unwrap()
    );
    assert!(finished
        .result
        .as_ref()
        .unwrap()
        .logs
        .stdout
        .text
        .contains("TEMPLATE_RUN:中文:4:3.13.16"));
    assert!(runner.cleanup_status().unwrap().is_none());
    // A completed run does not write any generated file into the vault.
    {
        let lock = workspace.lock().unwrap();
        let ws = lock.as_ref().unwrap();
        assert_eq!(ws.pending_count().unwrap(), pending_before_run);
        assert_eq!(
            std::fs::read(root.path().join("vault/报告.md")).unwrap(),
            b"Original user note"
        );
        assert!(!root.path().join("vault/result.json").exists());
        assert_eq!(
            ws.experiment_output(&run.operation_id, "报告.md")
                .unwrap()
                .content(),
            "# 课程成果\r\n总计：4\r\n".as_bytes()
        );
    }
    let import = ImportRequest {
        vault_id: run.vault_id.clone(),
        operation_id: uuid::Uuid::new_v4().to_string(),
        run_id: run.operation_id.clone(),
        selections: vec![Selection {
            output_path: "报告.md".into(),
            destination: "报告.md".into(),
        }],
    };
    let preview = runner.import_prepare(&workspace, &import).unwrap();
    assert_eq!(preview.state, ImportState::AwaitingConfirmation);
    assert_eq!(preview.plan.items[0].target.file_id, original.file_id);
    assert_eq!(
        runner
            .import_next(&workspace, &import.operation_id)
            .unwrap()
            .state,
        ImportState::AwaitingConfirmation
    );
    assert_eq!(
        std::fs::read(root.path().join("vault/报告.md")).unwrap(),
        b"Original user note"
    );
    runner
        .confirm_import_from_user_for_vault(
            &workspace,
            &run.vault_id,
            &import.operation_id,
            &preview.fingerprint,
        )
        .unwrap();
    let imported = runner
        .import_next_for_vault(&workspace, &run.vault_id, &import.operation_id)
        .unwrap();
    assert_eq!(imported.state, ImportState::Completed);
    let entry = imported.items[0].entry.as_ref().unwrap();
    assert_eq!(entry.file_id, original.file_id);
    assert!(!root.path().join("vault/result.json").exists());
    // Restarting retains the exact source/input, run and chosen output evidence.
    drop(workspace);
    let mut ws = Workspace::open(&root.path().join("vault")).unwrap();
    assert_eq!(
        ws.read("报告.md").unwrap().content,
        "# 课程成果\r\n总计：4\r\n"
    );
    let origins = ws
        .experiment_artifact_origins(&entry.file_id, 10, None)
        .unwrap();
    assert_eq!(origins.items.len(), 1);
    assert_eq!(origins.items[0].run_id, run.operation_id);
    assert_eq!(
        origins.items[0].source.request.entry.file_id,
        run.entry.file_id
    );
    assert_eq!(origins.items[0].source.request.inputs.len(), 2);
    assert_eq!(origins.items[0].output.path, "报告.md");
    assert_eq!(origins.items[0].execution.approval_id, finished.approval_id);
    assert_eq!(ws.pending_count().unwrap(), pending_before_run + 1);
    println!(
        "TEMPLATE_NATIVE_WORKFLOW_CONFIRMED {} {} {}",
        run.operation_id, import.operation_id, entry.file_id
    );
}
