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
    let mut ws = Workspace::open(&root.path().join("vault")).unwrap();
    let mut store = ExtensionStore::open(&root.path().join("packages")).unwrap();
    let mut vector: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../src/services/fixtures/community-python-vector.json"
    )))
    .unwrap();
    for key in ["release_id", "withdrawn", "download_path"] {
        vector["release"].as_object_mut().unwrap().remove(key);
    }
    let mut release: crate::extension_package::Release =
        serde_json::from_value(vector["release"].clone()).unwrap();
    release.kind = "persona".into();
    release.permissions.clear();
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    zip.start_file("persona.json", zip::write::SimpleFileOptions::default())
        .unwrap();
    zip.write_all(&serde_json::to_vec(&manifest).unwrap())
        .unwrap();
    let archive = zip.finish().unwrap().into_inner();
    release.sha256 = hash(&archive);
    release.size = archive.len() as u64;
    let signer = SigningKey::from_bytes(&[7; 32]);
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
    let op = uuid::Uuid::new_v4().to_string();
    store
        .switch_prepared(&op, &ws.vault_id, &plan.changes)
        .unwrap();
    store.finish_installation(&op, true).unwrap();
    let slot = plan.changes[0].target.slot.clone();
    assert!(ws.record_get_kind("persona", "default").unwrap().is_none());
    (root, store, ws, slot)
}
fn request(ws: &Workspace, preview: &PersonaPreview) -> PersonaApply {
    PersonaApply {
        vault_id: ws.vault_id.clone(),
        slot: preview.slot.clone(),
        target: preview.target.clone(),
        expected: preview.expected.clone(),
        target_version: preview.target_version,
        fingerprint: preview.fingerprint.clone(),
        operation_id: uuid::Uuid::new_v4().to_string(),
    }
}
fn local_persona(ws: &mut Workspace, name: &str, version: u64) -> Value {
    let current = ws.record_get_kind("persona", "default").unwrap();
    let expected = current
        .as_ref()
        .and_then(|v| v["hash"].as_str())
        .unwrap_or("");
    let record = json!({"schema":1,"kind":"persona","id":"default","data":{"version":version,"name":name,"system_prompt":"User revision","dialogue_pairs":[]}});
    ws.write_operation(
        &records::path_for("persona", "default").unwrap(),
        expected,
        &serde_json::to_vec(&record).unwrap(),
        "local",
        &uuid::Uuid::new_v4().to_string(),
    )
    .unwrap();
    ws.record_get_kind("persona", "default").unwrap().unwrap()
}
#[test]
fn signed_installed_persona_preview_does_not_write_and_explicit_apply_is_durable() {
    let (root, store, mut ws, slot) = installed(
        json!({"name":"Community example","system_prompt":"<img src=x onerror=alert(1)>","dialogue_pairs":[{"user":"Question","assistant":"Answer"}]}),
    );
    let preview = persona_preview(&store, &mut ws, &slot).unwrap();
    assert!(preview.before.is_none());
    assert_eq!(ws.pending_count().unwrap(), 0);
    let apply = request(&ws, &preview);
    let receipt = persona_apply(&store, &mut ws, &apply, || Ok(())).unwrap();
    assert_eq!(receipt["hash"], preview.after_sha256);
    assert_eq!(receipt["data"]["version"], 1);
    assert_eq!(ws.pending_count().unwrap(), 1);
    let vault = ws.vault_id.clone();
    drop(ws);
    let mut ws = Workspace::open(&root.path().join("vault")).unwrap();
    assert_eq!(ws.vault_id, vault);
    assert_eq!(
        ws.record_get_kind("persona", "default").unwrap().unwrap()["record"]["data"],
        preview.after
    );
    assert_eq!(
        persona_apply(&store, &mut ws, &apply, || Ok(())).unwrap(),
        receipt
    );
    assert_eq!(ws.pending_count().unwrap(), 1);
}
#[test]
fn later_persona_edit_is_not_overwritten_by_stale_review_or_receipt_replay() {
    let (_root, store, mut ws, slot) = installed(json!({"system_prompt":"Public candidate"}));
    let preview = persona_preview(&store, &mut ws, &slot).unwrap();
    let stale = request(&ws, &preview);
    let edited = local_persona(&mut ws, "User editing", 1);
    assert_eq!(
        persona_apply(&store, &mut ws, &stale, || Ok(()))
            .unwrap_err()
            .code,
        "REVISION_CONFLICT"
    );
    assert_eq!(
        ws.record_get_kind("persona", "default").unwrap().unwrap(),
        edited
    );
    let preview = persona_preview(&store, &mut ws, &slot).unwrap();
    let apply = request(&ws, &preview);
    let receipt = persona_apply(&store, &mut ws, &apply, || Ok(())).unwrap();
    let later = local_persona(&mut ws, "Later revision", 3);
    assert_eq!(
        persona_apply(&store, &mut ws, &apply, || Ok(())).unwrap(),
        receipt
    );
    assert_eq!(
        ws.record_get_kind("persona", "default").unwrap().unwrap(),
        later
    );
}
#[test]
fn target_vault_version_and_review_binding_cannot_be_changed() {
    let (_root, store, mut ws, slot) = installed(json!({"system_prompt":"Public candidate"}));
    let preview = persona_preview(&store, &mut ws, &slot).unwrap();
    for change in ["vault", "target", "version", "fingerprint"] {
        let mut apply = request(&ws, &preview);
        match change {
            "vault" => apply.vault_id = uuid::Uuid::new_v4().to_string(),
            "target" => apply.target = "other".into(),
            "version" => apply.target_version = 2,
            _ => apply.fingerprint = "0".repeat(64),
        }
        assert!(persona_apply(&store, &mut ws, &apply, || Ok(())).is_err());
    }
    assert_eq!(ws.pending_count().unwrap(), 0);
    assert!(ws.record_get_kind("persona", "default").unwrap().is_none());
}
#[test]
fn cancellation_at_the_durable_authorization_boundary_creates_no_operation() {
    let (_root, store, mut ws, slot) = installed(json!({"system_prompt":"Public candidate"}));
    let preview = persona_preview(&store, &mut ws, &slot).unwrap();
    let apply = request(&ws, &preview);
    let calls = AtomicUsize::new(0);
    let error = persona_apply(&store, &mut ws, &apply, || {
        if calls.fetch_add(1, Ordering::SeqCst) >= 1 {
            Err(HostError::new("REQUEST_CANCELLED"))
        } else {
            Ok(())
        }
    })
    .unwrap_err();
    assert_eq!(error.code, "REQUEST_CANCELLED");
    assert!(ws.operation(&apply.operation_id).unwrap().is_none());
    assert_eq!(ws.pending_count().unwrap(), 0);
}
#[test]
fn unknown_candidate_fields_and_setting_limits_are_rejected_without_writes() {
    for manifest in [
        json!({"system_prompt":"Public candidate","execute":true}),
        json!({"system_prompt":"x".repeat(16001)}),
        json!({"system_prompt":"x","dialogue_pairs":vec![json!({"user":"x","assistant":"y"});21]}),
    ] {
        let (_root, store, mut ws, slot) = installed(manifest);
        assert!(persona_preview(&store, &mut ws, &slot).is_err());
        assert_eq!(ws.pending_count().unwrap(), 0);
    }
}
#[test]
fn changed_source_trust_invalidates_a_previous_candidate_review() {
    let (_root, mut store, mut ws, slot) = installed(json!({"system_prompt":"Public candidate"}));
    let preview = persona_preview(&store, &mut ws, &slot).unwrap();
    let apply = request(&ws, &preview);
    let material = store.runtime_package(&slot, &ws.vault_id, None).unwrap();
    let mut trust = store
        .trust_setting(
            &material.source,
            &material.release.namespace,
            &material.release.key_id,
        )
        .unwrap()
        .unwrap();
    let before = trust.fingerprint().unwrap();
    trust.public_key = SigningKey::from_bytes(&[8; 32]).verifying_key().to_bytes();
    store
        .confirm_trust(&trust, Some(&before), &trust.fingerprint().unwrap())
        .unwrap();
    assert_eq!(
        persona_apply(&store, &mut ws, &apply, || Ok(()))
            .unwrap_err()
            .code,
        "EXTENSION_SOURCE_UNTRUSTED"
    );
    assert_eq!(ws.pending_count().unwrap(), 0);
}
