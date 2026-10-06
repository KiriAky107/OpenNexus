use super::*;
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signer as _, SigningKey};
use serde_json::{json, Value};

fn fixture() -> (Release, Vec<u8>, [u8; 32]) {
    let mut data: Value = serde_json::from_str(include_str!(
        "../../../../src/services/fixtures/community-python-vector.json"
    ))
    .unwrap();
    for key in ["release_id", "withdrawn", "download_path"] {
        data["release"].as_object_mut().unwrap().remove(key);
    }
    let mut release: Release = serde_json::from_value(data["release"].clone()).unwrap();
    sign(&mut release);
    (
        release,
        STANDARD
            .decode(data["archive_base64"].as_str().unwrap())
            .unwrap(),
        SigningKey::from_bytes(&[7; 32]).verifying_key().to_bytes(),
    )
}
fn sign(release: &mut Release) {
    release.signature = STANDARD.encode(
        SigningKey::from_bytes(&[7; 32])
            .sign(&release.signed_payload().unwrap())
            .to_bytes(),
    );
}
fn setup() -> (
    tempfile::TempDir,
    ExtensionStore,
    Release,
    Vec<u8>,
    [u8; 32],
) {
    let root = tempfile::tempdir().unwrap();
    let mut store = ExtensionStore::open(root.path()).unwrap();
    let (release, archive, key) = fixture();
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
    (root, store, release, archive, key)
}
fn install(
    store: &mut ExtensionStore,
    release: &Release,
    archive: &[u8],
    key: &[u8; 32],
    vault: &str,
    healthy: bool,
) -> (String, InstallPreview) {
    let receipt = store
        .stage(Stage {
            operation_id: &Uuid::new_v4().to_string(),
            source: "https://catalog.example/",
            release,
            archive,
            withdrawn: false,
            signer: Signer {
                public_key: key,
                key_id: &release.key_id,
                namespace: &release.namespace,
                revoked: false,
            },
        })
        .unwrap();
    let preview = store
        .installation_preview(&InstallRequest {
            root_key: receipt.package_key,
            vault_id: vault.into(),
            app_version: "0.6.0".into(),
            platform: "windows".into(),
            architecture: "x86_64".into(),
            configurations: Default::default(),
        })
        .unwrap();
    let operation = Uuid::new_v4().to_string();
    // Seed a committed native pointer, not a runtime health acceptance test.
    crate::extension_transaction::switch(&mut store.db, &operation, &preview.changes).unwrap();
    if healthy {
        store.finish_installation(&operation, true).unwrap();
    }
    (operation, preview)
}

#[test]
fn installed_pages_are_vault_bound_metadata_only_and_survive_reopen() {
    let (root, mut store, mut release, archive, key) = setup();
    let vault = Uuid::new_v4().to_string();
    let other = Uuid::new_v4().to_string();
    assert!(store.installed(&vault, 0, 20).unwrap().items.is_empty());
    for i in 0..25 {
        release.package_id = format!("item-{i:02}");
        sign(&mut release);
        install(&mut store, &release, &archive, &key, &vault, true);
        if i < 3 {
            install(&mut store, &release, &archive, &key, &other, true);
        }
    }
    let page = store.installed(&vault, 20, 20).unwrap();
    assert_eq!(page.total, 25);
    assert_eq!(page.items.len(), 5);
    assert_eq!(page.items[0].release.package_id, "item-20");
    assert!(page
        .items
        .iter()
        .all(|item| item.pending_operation.is_none() && item.rollback_operation_id.is_none()));
    assert_eq!(store.installed(&other, 0, 20).unwrap().total, 3);
    std::fs::remove_file(root.path().join("objects").join(&release.sha256)).unwrap();
    // Missing archive cannot turn a metadata read into an implicit download or
    // repair. Installation/rollback still revalidate archives independently.
    assert_eq!(store.installed(&vault, 0, 20).unwrap().total, 25);
    drop(store);
    let store = ExtensionStore::open(root.path()).unwrap();
    assert_eq!(store.installed(&vault, 20, 20).unwrap().items.len(), 5);
    assert!(store.installed(&vault, 30, 20).unwrap().items.is_empty());
    let encoded = serde_json::to_string(&page).unwrap();
    assert!(!encoded.contains("directory") && !encoded.contains("signer"));
}

#[test]
fn pending_and_recovered_operations_are_reported_without_granting_completion() {
    let (root, mut store, release, archive, key) = setup();
    let vault = Uuid::new_v4().to_string();
    let (operation, _) = install(&mut store, &release, &archive, &key, &vault, false);
    let page = store.installed(&vault, 0, 20).unwrap();
    assert_eq!(
        store
            .installation_targets(&operation, &vault)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        page.items[0].pending_operation.as_deref(),
        Some(operation.as_str())
    );
    assert!(page.items[0].rollback_operation_id.is_none());
    assert_eq!(
        store
            .installation_status(&operation, &vault)
            .unwrap()
            .unwrap()
            .state,
        "checking"
    );
    assert_eq!(
        store
            .installation_status(&operation, &Uuid::new_v4().to_string())
            .unwrap_err()
            .code,
        "VAULT_CHANGED"
    );
    assert!(store
        .installation_status(&Uuid::new_v4().to_string(), &vault)
        .unwrap()
        .is_none());
    drop(store);
    let store = ExtensionStore::open(root.path()).unwrap();
    assert!(store.installed(&vault, 0, 20).unwrap().items.is_empty());
    assert_eq!(
        store
            .installation_targets(&operation, &vault)
            .unwrap_err()
            .code,
        "EXTENSION_INSTALL_NOT_PENDING"
    );
    assert_eq!(
        store
            .installation_status(&operation, &vault)
            .unwrap()
            .unwrap()
            .state,
        "rolled_back"
    );
}

#[tokio::test]
async fn rollback_freezes_old_versions_configurations_and_current_trust_before_network() {
    let (_root, mut store, mut release, archive, key) = setup();
    let vault = Uuid::new_v4().to_string();
    install(&mut store, &release, &archive, &key, &vault, true);
    release.version = "2.0.0".into();
    sign(&mut release);
    let (operation, current) = install(&mut store, &release, &archive, &key, &vault, true);
    let before = store
        .active_installation(&current.changes[0].target.slot)
        .unwrap()
        .unwrap();
    let page = store.installed(&vault, 0, 20).unwrap();
    assert_eq!(page.items[0].release.version, "2.0.0");
    assert_eq!(
        page.items[0].rollback_operation_id.as_deref(),
        Some(operation.as_str())
    );
    let preview = store.rollback_preview(&operation, &vault).unwrap();
    assert_eq!(preview.dependencies.packages[0].version, "1.0.0");
    assert_eq!(
        preview.changes[0].expected_revision.as_deref(),
        Some(before.revision.as_str())
    );
    assert_eq!(
        store
            .active_installation(&before.target.slot)
            .unwrap()
            .unwrap(),
        before
    );
    assert_eq!(
        store
            .rollback_preview(&operation, &Uuid::new_v4().to_string())
            .unwrap_err()
            .code,
        "VAULT_CHANGED"
    );
    assert_eq!(
        store
            .rollback_reviewed(
                &Uuid::new_v4().to_string(),
                &operation,
                &vault,
                "not-reviewed"
            )
            .await
            .unwrap_err()
            .code,
        "EXTENSION_INSTALL_REVIEW_CHANGED"
    );
    let mut trust = store
        .trust_setting(
            "https://catalog.example/",
            &release.namespace,
            &release.key_id,
        )
        .unwrap()
        .unwrap();
    let prior = trust.fingerprint().unwrap();
    trust.enabled = false;
    store
        .confirm_trust(&trust, Some(&prior), &trust.fingerprint().unwrap())
        .unwrap();
    assert_eq!(
        store.rollback_preview(&operation, &vault).unwrap_err().code,
        "EXTENSION_SOURCE_UNTRUSTED"
    );
    assert_eq!(
        store
            .active_installation(&before.target.slot)
            .unwrap()
            .unwrap(),
        before
    );
}

#[tokio::test]
async fn rollback_does_not_reinterpret_an_older_review_after_another_update() {
    let (_root, mut store, mut release, archive, key) = setup();
    let vault = Uuid::new_v4().to_string();
    install(&mut store, &release, &archive, &key, &vault, true);
    release.version = "2.0.0".into();
    sign(&mut release);
    let (operation, _) = install(&mut store, &release, &archive, &key, &vault, true);
    let preview = store.rollback_preview(&operation, &vault).unwrap();
    release.version = "3.0.0".into();
    sign(&mut release);
    install(&mut store, &release, &archive, &key, &vault, true);
    assert_eq!(
        store
            .rollback_reviewed(
                &Uuid::new_v4().to_string(),
                &operation,
                &vault,
                &preview.fingerprint
            )
            .await
            .unwrap_err()
            .code,
        "EXTENSION_INSTALL_CONFLICT"
    );
    assert_eq!(
        store.installed(&vault, 0, 20).unwrap().items[0]
            .release
            .version,
        "3.0.0"
    );
}

#[test]
fn installed_metadata_rejects_secret_configuration_and_invalid_pages() {
    let (_root, mut store, release, archive, key) = setup();
    let vault = Uuid::new_v4().to_string();
    let (_, preview) = install(&mut store, &release, &archive, &key, &vault, true);
    for limit in [0, 101] {
        assert_eq!(
            store.installed(&vault, 0, limit).unwrap_err().code,
            "EXTENSION_PAGE_INVALID"
        );
    }
    assert_eq!(
        store.installed("not-a-vault", 0, 20).unwrap_err().code,
        "VAULT_INVALID"
    );
    let mut target = preview.changes[0].target.clone();
    target.configuration = json!({"password":"do-not-leak-config-value"});
    let bytes = serde_json::to_string(&target).unwrap();
    store
        .db
        .execute(
            "UPDATE extension_active SET target=?1,revision=?2 WHERE slot=?3",
            params![bytes, hash(bytes.as_bytes()), target.slot],
        )
        .unwrap();
    let error = store.installed(&vault, 0, 20).unwrap_err();
    assert_eq!(error.code, "EXTENSION_CONFIG_SECRET");
    assert!(!format!("{error:?}").contains("do-not-leak-config-value"));
}
