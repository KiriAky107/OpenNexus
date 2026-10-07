use super::*;
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signer as _, SigningKey};
use notesagent_host::{
    credentials::CredentialBroker,
    extension_package::Release,
    extension_store::{ExtensionStore, InstallRequest, Signer, Stage, TrustSetting},
    workspace::{hash, Workspace},
};
use serde_json::{json, Value};
use std::{
    io::{Cursor, Write},
    sync::{
        atomic::{AtomicUsize, Ordering},
        OnceLock,
    },
};
use zeroize::Zeroizing;
use zip::write::SimpleFileOptions;

fn executable() -> &'static [u8] {
    static EXE: OnceLock<Vec<u8>> = OnceLock::new();
    EXE.get_or_init(|| {
        let temp = tempfile::tempdir().unwrap();
        let output = temp.path().join("fixture.exe");
        let result = std::process::Command::new("rustc")
            .args(["--edition=2021"])
            .arg(
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/fixtures/sandbox_network_probe.rs"),
            )
            .arg("-o")
            .arg(&output)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        std::fs::read(output).unwrap()
    })
}
struct Harness {
    host: Host,
    _root: tempfile::TempDir,
    vault: String,
}
impl Harness {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let host = Host::default();
        let vault_dir = root.path().join("vault");
        std::fs::create_dir(&vault_dir).unwrap();
        let workspace = Workspace::open(&vault_dir).unwrap();
        let vault = workspace.vault_id.clone();
        *host.workspace.lock().unwrap() = Some(workspace);
        let mut credentials = CredentialBroker::new(root.path().join("credentials.v1"));
        credentials
            .unlock(Zeroizing::new(b"isolated grouped health fixture".to_vec()))
            .unwrap();
        *host.credentials.lock().unwrap() = Some(credentials);
        let extension_root = root.path().join("extensions");
        std::fs::create_dir(&extension_root).unwrap();
        let mut extensions = ExtensionStore::open(&extension_root).unwrap();
        let trust = TrustSetting {
            source: "https://catalog.example/".into(),
            source_id: "fixture".into(),
            namespace: "examples".into(),
            key_id: "fixture-key".into(),
            public_key: SigningKey::from_bytes(&[37; 32]).verifying_key().to_bytes(),
            enabled: true,
        };
        extensions
            .confirm_trust(&trust, None, &trust.fingerprint().unwrap())
            .unwrap();
        *host.extensions.lock().unwrap() = Some(extensions);
        Self {
            host,
            _root: root,
            vault,
        }
    }
    fn stage(
        &self,
        id: &str,
        version: &str,
        mode: Option<&str>,
        dependencies: BTreeMap<String, String>,
    ) -> String {
        self.stage_with_configuration(id, version, mode, dependencies, None)
    }
    fn stage_with_configuration(
        &self,
        id: &str,
        version: &str,
        mode: Option<&str>,
        dependencies: BTreeMap<String, String>,
        configuration: Option<Value>,
    ) -> String {
        let is_mcp = mode.is_some() || configuration.is_some();
        let mut archive = zip::ZipWriter::new(Cursor::new(Vec::new()));
        let manifest = if let Some(mode) = mode {
            archive
                .start_file("entry.exe", SimpleFileOptions::default())
                .unwrap();
            archive.write_all(executable()).unwrap();
            archive
                .start_file("mcp.json", SimpleFileOptions::default())
                .unwrap();
            json!({"transport":"stdio","command":"entry.exe","args":[mode]})
        } else if let Some(configuration) = configuration {
            archive
                .start_file("mcp.json", SimpleFileOptions::default())
                .unwrap();
            configuration
        } else {
            archive
                .start_file("persona.json", SimpleFileOptions::default())
                .unwrap();
            json!({"system_prompt":"Reviewed fixture only"})
        };
        archive
            .write_all(&serde_json::to_vec(&manifest).unwrap())
            .unwrap();
        let archive = archive.finish().unwrap().into_inner();
        let mut template: Value = serde_json::from_str(include_str!(
            "../../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        for field in ["release_id", "withdrawn", "download_path"] {
            template["release"].as_object_mut().unwrap().remove(field);
        }
        let mut release: Release = serde_json::from_value(template["release"].clone()).unwrap();
        release.package_id = id.into();
        release.kind = if is_mcp { "mcp" } else { "persona" }.into();
        release.version = version.into();
        release.dependencies = dependencies;
        release.sha256 = hash(&archive);
        release.size = archive.len() as u64;
        release.key_id = "fixture-key".into();
        release.permissions.clear();
        let signing = SigningKey::from_bytes(&[37; 32]);
        release.signature =
            STANDARD.encode(signing.sign(&release.signed_payload().unwrap()).to_bytes());
        let key = signing.verifying_key().to_bytes();
        store(&self.host, |store| {
            store.stage(Stage {
                operation_id: &uuid::Uuid::new_v4().to_string(),
                source: "https://catalog.example/",
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
        })
        .unwrap()
        .package_key
    }
    fn pending(&self, root_key: String) -> (String, Vec<String>) {
        let request = InstallRequest {
            root_key,
            vault_id: self.vault.clone(),
            app_version: env!("CARGO_PKG_VERSION").into(),
            platform: "windows".into(),
            architecture: "x86_64".into(),
            configurations: BTreeMap::new(),
        };
        let operation = uuid::Uuid::new_v4().to_string();
        let preview = store(&self.host, |store| store.installation_preview(&request)).unwrap();
        let slots = preview
            .changes
            .iter()
            .map(|change| change.target.slot.clone())
            .collect();
        // Online metadata approval has its own real HTTP fixture. Here seed the
        // signed/prepared transaction, then exercise actual native group health.
        store(&self.host, |store| {
            store.switch_prepared(&operation, &self.vault, &preview.changes)
        })
        .unwrap();
        (operation, slots)
    }
    fn group(&self, version: &str, second_mode: &str) -> (String, Vec<String>) {
        self.stage(
            "second-runtime",
            version,
            Some(second_mode),
            BTreeMap::new(),
        );
        let root = self.stage(
            "first-runtime",
            version,
            Some("mcp"),
            [("examples/second-runtime".into(), version.into())]
                .into_iter()
                .collect(),
        );
        self.pending(root)
    }
    fn activate(
        &self,
        operation: &str,
        check: &dyn Fn() -> Result<(), String>,
    ) -> Result<Receipt, String> {
        finish_reviewed(
            &self.host,
            &self.vault,
            operation,
            Ok(Receipt {
                operation_id: operation.into(),
                state: "checking".into(),
            }),
            check,
        )
    }
    fn revisions(&self, slots: &[String]) -> Vec<String> {
        slots
            .iter()
            .map(|slot| {
                store(&self.host, |store| store.active_installation(slot))
                    .unwrap()
                    .unwrap()
                    .revision
            })
            .collect()
    }
    fn endpoints(&self, slots: &[String]) -> Vec<Endpoint> {
        let endpoints = self.host.extension_endpoints.lock().unwrap();
        slots
            .iter()
            .map(|slot| endpoints.get(slot).unwrap().clone())
            .collect()
    }
    fn state(&self, operation: &str) -> String {
        store(&self.host, |store| {
            store.installation_status(operation, &self.vault)
        })
        .unwrap()
        .unwrap()
        .state
    }
}
impl Drop for Harness {
    fn drop(&mut self) {
        self.host
            .extension_instances
            .lock()
            .unwrap()
            .stop_all_and_join();
        self.host.extension_endpoints.lock().unwrap().clear();
    }
}

#[test]
fn declarative_mcp_installations_commit_without_runtime_or_execution_grants() {
    for manifest in [
        json!({"transport":"streamable_http","url":"https://catalog.example/mcp","secret_header_keys":["Authorization"]}),
        json!({"transport":"sse","url":"https://catalog.example/sse"}),
        json!({"transport":"stdio","command":"python","args":["lesson_server.py"]}),
    ] {
        let fixture = Harness::new();
        let key = fixture.stage_with_configuration(
            "configuration",
            "1.0.0",
            None,
            BTreeMap::new(),
            Some(manifest),
        );
        let (operation, slots) = fixture.pending(key);
        assert_eq!(
            fixture.activate(&operation, &|| Ok(())).unwrap().state,
            "complete"
        );
        assert_eq!(fixture.state(&operation), "complete");
        assert_eq!(fixture.revisions(&slots).len(), 1);
        assert!(fixture.host.extension_endpoints.lock().unwrap().is_empty());
        assert_eq!(
            fixture
                .host
                .extension_instances
                .lock()
                .unwrap()
                .active_count(),
            0
        );
    }
}

#[test]
fn runtime_to_configuration_update_stops_the_old_generation() {
    let fixture = Harness::new();
    let key = fixture.stage("replacement", "1.0.0", Some("mcp"), BTreeMap::new());
    let (operation, slots) = fixture.pending(key);
    fixture.activate(&operation, &|| Ok(())).unwrap();
    let old = fixture.endpoints(&slots).remove(0);
    let key = fixture.stage_with_configuration(
        "replacement",
        "2.0.0",
        None,
        BTreeMap::new(),
        Some(json!({"transport":"streamable_http","url":"https://catalog.example/mcp"})),
    );
    let (update, new_slots) = fixture.pending(key);
    assert_eq!(new_slots, slots);
    assert_eq!(
        fixture.activate(&update, &|| Ok(())).unwrap().state,
        "complete"
    );
    assert_eq!(old.snapshot().status, Status::Stopped);
    assert!(fixture.host.extension_endpoints.lock().unwrap().is_empty());
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        0
    );
}

#[test]
fn actual_group_health_commits_all_dependencies_and_replaces_running_generations() {
    let fixture = Harness::new();
    let (first, slots) = fixture.group("1.0.0", "mcp");
    assert_eq!(
        fixture.activate(&first, &|| Ok(())).unwrap().state,
        "complete"
    );
    let old = fixture.endpoints(&slots);
    let revisions = fixture.revisions(&slots);
    assert!(old
        .iter()
        .zip(&revisions)
        .all(|(endpoint, revision)| endpoint.ready_for_revision(revision)));
    let (second, new_slots) = fixture.group("2.0.0", "mcp");
    assert_eq!(new_slots, slots);
    assert_eq!(
        fixture.activate(&second, &|| Ok(())).unwrap().state,
        "complete"
    );
    assert!(old
        .iter()
        .all(|endpoint| endpoint.snapshot().status == Status::Stopped));
    let new_revisions = fixture.revisions(&slots);
    assert_ne!(revisions, new_revisions);
    assert!(fixture
        .endpoints(&slots)
        .iter()
        .zip(&new_revisions)
        .all(|(endpoint, revision)| endpoint.ready_for_revision(revision)));
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        2
    );
}

#[test]
fn bad_dependency_rolls_back_every_pointer_and_restores_old_runtime_only() {
    let fixture = Harness::new();
    let (old, slots) = fixture.group("1.0.0", "mcp");
    fixture.activate(&old, &|| Ok(())).unwrap();
    let revisions = fixture.revisions(&slots);
    let unrelated_key = fixture.stage("unrelated-runtime", "1.0.0", Some("mcp"), BTreeMap::new());
    let (unrelated_op, unrelated_slots) = fixture.pending(unrelated_key);
    fixture.activate(&unrelated_op, &|| Ok(())).unwrap();
    let unrelated = fixture.endpoints(&unrelated_slots).remove(0);
    let (update, _) = fixture.group("2.0.0", "mcp_bad_version");
    assert!(fixture.activate(&update, &|| Ok(())).is_err());
    assert_eq!(fixture.state(&update), "rolled_back");
    assert_eq!(fixture.revisions(&slots), revisions);
    assert!(fixture
        .endpoints(&slots)
        .iter()
        .zip(&revisions)
        .all(|(endpoint, revision)| endpoint.ready_for_revision(revision)));
    assert_eq!(unrelated.snapshot().status, Status::Ready);
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        3
    );
}

#[test]
fn cancellation_after_one_native_dependency_is_ready_cleans_the_group() {
    let fixture = Harness::new();
    let (operation, slots) = fixture.group("1.0.0", "mcp");
    let calls = AtomicUsize::new(0);
    let check = || {
        calls.fetch_add(1, Ordering::SeqCst);
        if fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .ready_count()
            > 0
        {
            Err("REQUEST_CANCELLED".into())
        } else {
            Ok(())
        }
    };
    assert_eq!(
        fixture.activate(&operation, &check).unwrap_err(),
        "REQUEST_CANCELLED"
    );
    assert_eq!(fixture.state(&operation), "rolled_back");
    assert!(calls.load(Ordering::SeqCst) > 2);
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        0
    );
    assert!(slots.iter().all(
        |slot| store(&fixture.host, |store| store.active_installation(slot))
            .unwrap()
            .is_none()
    ));
}

#[test]
fn declarative_group_completes_without_starting_a_fictitious_runtime() {
    let fixture = Harness::new();
    let key = fixture.stage("reviewed-persona", "1.0.0", None, BTreeMap::new());
    let (operation, slots) = fixture.pending(key);
    assert_eq!(
        fixture.activate(&operation, &|| Ok(())).unwrap().state,
        "complete"
    );
    assert_eq!(fixture.state(&operation), "complete");
    assert_eq!(fixture.revisions(&slots).len(), 1);
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        0
    );
    assert!(fixture.host.extension_endpoints.lock().unwrap().is_empty());
}

#[test]
fn failed_root_after_a_healthy_dependency_stops_new_generations_before_restoration() {
    let fixture = Harness::new();
    let (old, slots) = fixture.group("1.0.0", "mcp");
    fixture.activate(&old, &|| Ok(())).unwrap();
    let revisions = fixture.revisions(&slots);
    let previous = fixture.endpoints(&slots);
    fixture.stage("second-runtime", "2.0.0", Some("mcp"), BTreeMap::new());
    let root = fixture.stage(
        "first-runtime",
        "2.0.0",
        Some("mcp_bad_version"),
        [("examples/second-runtime".into(), "2.0.0".into())]
            .into_iter()
            .collect(),
    );
    let (update, _) = fixture.pending(root);
    assert!(fixture.activate(&update, &|| Ok(())).is_err());
    assert_eq!(fixture.state(&update), "rolled_back");
    assert_eq!(fixture.revisions(&slots), revisions);
    assert!(previous
        .iter()
        .all(|endpoint| endpoint.snapshot().status == Status::Stopped));
    assert!(fixture
        .endpoints(&slots)
        .iter()
        .zip(&revisions)
        .all(|(endpoint, revision)| endpoint.ready_for_revision(revision)));
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        2
    );
}

#[test]
fn online_cancel_receipt_race_restores_pending_but_never_reverts_completed_installation() {
    let fixture = Harness::new();
    let key = fixture.stage("reviewed-persona", "1.0.0", None, BTreeMap::new());
    let (operation, _) = fixture.pending(key);
    assert_eq!(
        finish_reviewed(
            &fixture.host,
            &fixture.vault,
            &operation,
            Err("REQUEST_CANCELLED".into()),
            &|| Ok(())
        )
        .unwrap_err(),
        "REQUEST_CANCELLED"
    );
    assert_eq!(fixture.state(&operation), "rolled_back");
    let key = fixture.stage("reviewed-persona", "2.0.0", None, BTreeMap::new());
    let (completed, slots) = fixture.pending(key);
    fixture.activate(&completed, &|| Ok(())).unwrap();
    assert!(finish_reviewed(
        &fixture.host,
        &fixture.vault,
        &completed,
        Err("REQUEST_CANCELLED".into()),
        &|| Ok(())
    )
    .is_err());
    assert_eq!(fixture.state(&completed), "complete");
    assert_eq!(fixture.revisions(&slots).len(), 1);
}

#[test]
fn vault_change_cannot_publish_a_new_runtime_or_grant_completion() {
    let fixture = Harness::new();
    let (operation, slots) = fixture.group("1.0.0", "mcp");
    let check = || {
        if fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count()
            > 0
        {
            *fixture.host.workspace.lock().unwrap() = None;
        }
        Ok(())
    };
    assert!(fixture.activate(&operation, &check).is_err());
    assert_eq!(fixture.state(&operation), "rolled_back");
    assert_eq!(
        fixture
            .host
            .extension_instances
            .lock()
            .unwrap()
            .active_count(),
        0
    );
    assert!(fixture.host.extension_endpoints.lock().unwrap().is_empty());
    assert!(slots.iter().all(
        |slot| store(&fixture.host, |store| store.active_installation(slot))
            .unwrap()
            .is_none()
    ));
}
