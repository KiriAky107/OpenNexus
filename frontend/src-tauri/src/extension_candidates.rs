//! Reviewed package data is applied through existing Workspace record validation and CAS.
use crate::{
    extension_store::{ExtensionStore, RuntimePackage},
    records,
    workspace::{hash, HostError, Result, Workspace},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Pair {
    #[serde(default)]
    user: String,
    #[serde(default)]
    assistant: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Persona {
    name: Option<String>,
    system_prompt: String,
    #[serde(default)]
    dialogue_pairs: Vec<Pair>,
    schema_version: Option<u32>,
    permissions: Option<Vec<String>>,
    configuration_schema: Option<Value>,
}
#[derive(Debug, Serialize)]
pub struct PersonaPreview {
    pub fingerprint: String,
    pub slot: String,
    pub target: String,
    pub target_label: String,
    pub path: String,
    pub expected: String,
    pub target_version: u64,
    pub before: Option<Value>,
    pub after: Value,
    pub after_sha256: String,
    pub package_name: String,
    pub package_version: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PersonaApply {
    pub vault_id: String,
    pub slot: String,
    pub target: String,
    pub expected: String,
    pub target_version: u64,
    pub fingerprint: String,
    pub operation_id: String,
}
fn proposal(material: &RuntimePackage, version: u64) -> Result<Value> {
    if material.release.kind != "persona" {
        return Err(HostError::new("EXTENSION_CANDIDATE_TYPE"));
    }
    if version >= 9007199254740991 {
        return Err(HostError::new("PERSONA_VERSION_EXHAUSTED"));
    }
    let value: Persona = serde_json::from_value(material.manifest.clone())
        .map_err(|_| HostError::new("EXTENSION_CANDIDATE_INVALID"))?;
    let _ = (
        value.schema_version,
        value.permissions,
        value.configuration_schema,
    );
    let record = json!({"schema":1,"kind":"persona","id":"default","data":{
        "version":version+1,"name":value.name.unwrap_or_else(||material.release.name.clone()),
        "system_prompt":value.system_prompt,"dialogue_pairs":value.dialogue_pairs.into_iter().map(|pair|json!({"user":pair.user,"assistant":pair.assistant})).collect::<Vec<_>>()
    }});
    records::validate(
        &records::path_for("persona", "default")?,
        &serde_json::to_vec(&record).unwrap(),
    )?;
    Ok(record)
}
fn binding(
    store: &ExtensionStore,
    material: &RuntimePackage,
    vault: &str,
    expected: &str,
    version: u64,
    after: &Value,
) -> Result<String> {
    let trust = store
        .trust_setting(
            &material.source,
            &material.release.namespace,
            &material.release.key_id,
        )?
        .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED"))?;
    Ok(hash(
        &serde_json::to_vec(&(
            "persona-apply/v1",
            vault,
            &material.active,
            &material.release,
            &material.source,
            &material.signer_sha256,
            trust.fingerprint()?,
            "workspace_persona",
            expected,
            version,
            after,
        ))
        .unwrap(),
    ))
}
pub fn persona_preview(
    store: &ExtensionStore,
    ws: &mut Workspace,
    slot: &str,
) -> Result<PersonaPreview> {
    let material = store.runtime_package(slot, &ws.vault_id, None)?;
    let current = ws.record_get_kind("persona", "default")?;
    let before = current
        .as_ref()
        .map(|value| value["record"]["data"].clone());
    let expected = current
        .as_ref()
        .map(|value| value["hash"].as_str().unwrap_or("").to_owned())
        .unwrap_or_default();
    let version = before
        .as_ref()
        .and_then(|value| value["version"].as_u64())
        .unwrap_or(0);
    let after = proposal(&material, version)?;
    Ok(PersonaPreview {
        fingerprint: binding(store, &material, &ws.vault_id, &expected, version, &after)?,
        slot: slot.into(),
        target: "workspace_persona".into(),
        target_label: "persona/default".into(),
        path: records::path_for("persona", "default")?,
        expected,
        target_version: version,
        before,
        after_sha256: hash(&serde_json::to_vec(&after).unwrap()),
        after: after["data"].clone(),
        package_name: material.release.name,
        package_version: material.release.version,
    })
}
pub fn persona_apply(
    store: &ExtensionStore,
    ws: &mut Workspace,
    request: &PersonaApply,
    authorize: impl Fn() -> Result<()>,
) -> Result<Value> {
    authorize()?;
    if ws.vault_id != request.vault_id {
        return Err(HostError::new("VAULT_CHANGED"));
    }
    if request.target != "workspace_persona" {
        return Err(HostError::new("EXTENSION_CANDIDATE_TARGET"));
    }
    let digest = |value: &str| {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    };
    if !digest(&request.slot)
        || !digest(&request.fingerprint)
        || (!request.expected.is_empty() && !digest(&request.expected))
    {
        return Err(HostError::new("EXTENSION_CANDIDATE_INVALID"));
    }
    uuid::Uuid::parse_str(&request.operation_id)
        .map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
    let material = store.runtime_package(&request.slot, &ws.vault_id, None)?;
    let after = proposal(&material, request.target_version)?;
    if binding(
        store,
        &material,
        &ws.vault_id,
        &request.expected,
        request.target_version,
        &after,
    )? != request.fingerprint
    {
        return Err(HostError::new("EXTENSION_CANDIDATE_CHANGED"));
    }
    let current = ws.record_get_kind("persona", "default")?;
    let expected_now = current
        .as_ref()
        .map(|value| value["hash"].as_str().unwrap_or(""))
        .unwrap_or("");
    let version_now = current
        .as_ref()
        .and_then(|value| value["record"]["data"]["version"].as_u64())
        .unwrap_or(0);
    if expected_now == request.expected && version_now != request.target_version {
        return Err(HostError::new("EXTENSION_CANDIDATE_CHANGED"));
    }
    let path = records::path_for("persona", "default")?;
    let bytes = serde_json::to_vec(&after).unwrap();
    #[cfg(any(test, windows))]
    let entry = ws.write_operation_guarded(
        &path,
        &request.expected,
        &bytes,
        &request.operation_id,
        authorize,
    )?;
    #[cfg(not(any(test, windows)))]
    let entry = {
        authorize()?;
        ws.write_operation(
            &path,
            &request.expected,
            &bytes,
            "local",
            &request.operation_id,
        )?
    };
    Ok(
        json!({"operation_id":request.operation_id,"state":"applied","target":request.target,"path":path,"hash":entry.hash,"file_id":entry.file_id,"data":after["data"]}),
    )
}

#[cfg(test)]
mod tests;
