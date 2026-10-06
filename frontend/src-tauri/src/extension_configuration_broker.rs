//! Core receives configuration data only from a freshly verified installed package.
use crate::{
    extension_store::ExtensionStore,
    workspace::{hash, HostError, Workspace},
};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Candidate {
    vault_id: String,
    slot: String,
}

pub async fn dispatch(
    store: &mut ExtensionStore,
    workspace: &Workspace,
    request: &Value,
) -> Result<Value, String> {
    if request["rpc"].as_str() != Some("catalog.configuration_candidate") {
        return Err("HOST_RPC_DENIED".into());
    }
    let candidate: Candidate = serde_json::from_value(request["params"].clone())
        .map_err(|_| "EXTENSION_CANDIDATE_INVALID")?;
    if candidate.vault_id != workspace.vault_id {
        return Err("VAULT_CHANGED".into());
    }
    if candidate.slot.len() != 64
        || !candidate
            .slot
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("EXTENSION_CANDIDATE_INVALID".into());
    }
    let verified = store
        .candidate_package_online(&candidate.slot, &candidate.vault_id)
        .await
        .map_err(|error| error.code)?;
    let material = &verified.runtime;
    if !matches!(material.release.kind.as_str(), "mcp" | "model") {
        return Err("EXTENSION_CANDIDATE_TYPE".into());
    }
    let trust = store
        .trust_setting(
            &material.source,
            &material.release.namespace,
            &material.release.key_id,
        )
        .map_err(|error| error.code)?
        .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED").code)?;
    let binding = hash(
        &serde_json::to_vec(&(
            "configuration-candidate/v1",
            &candidate.vault_id,
            &material.active,
            &material.release,
            &material.source,
            &material.signer_sha256,
            trust.fingerprint().map_err(|error| error.code)?,
            &material.manifest,
        ))
        .map_err(|_| "EXTENSION_CANDIDATE_INVALID")?,
    );
    verified.ensure_fresh().map_err(|error| error.code)?;
    Ok(json!({
        "vault_id":candidate.vault_id, "slot":candidate.slot, "binding":binding,
        "kind":material.release.kind, "manifest":material.manifest,
        "package_name":material.release.name, "package_version":material.release.version
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn core_cannot_supply_a_manifest_change_vault_or_use_another_rpc() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("packages")).unwrap();
        std::fs::create_dir(root.path().join("vault")).unwrap();
        let ws = Workspace::open(&root.path().join("vault")).unwrap();
        let mut store = ExtensionStore::open(&root.path().join("packages")).unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        for (request, error) in [
            (
                json!({"rpc":"catalog.install", "params":{}}),
                "HOST_RPC_DENIED",
            ),
            (
                json!({"rpc":"catalog.configuration_candidate", "params":{"vault_id":ws.vault_id,"slot":"a".repeat(64),"manifest":{"command":"injected"}}}),
                "EXTENSION_CANDIDATE_INVALID",
            ),
            (
                json!({"rpc":"catalog.configuration_candidate", "params":{"vault_id":"other-vault","slot":"a".repeat(64)}}),
                "VAULT_CHANGED",
            ),
            (
                json!({"rpc":"catalog.configuration_candidate", "params":{"vault_id":ws.vault_id,"slot":"../packages"}}),
                "EXTENSION_CANDIDATE_INVALID",
            ),
        ] {
            assert_eq!(
                runtime
                    .block_on(dispatch(&mut store, &ws, &request))
                    .unwrap_err(),
                error
            );
        }
        assert!(!root.path().join("vault/opennexus-records").exists());
    }
}
