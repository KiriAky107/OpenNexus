//! Each selected template file has its own sealed review and atomic Host write.
//! Importing data never grants permission to run it or starts another file write.
use crate::{
    experiment_import::{check_target, target},
    experiment_input::{valid_path, SelectedFile},
    extension_store::{ExtensionStore, RuntimePackage},
    extension_templates,
    workspace::{hash, Entry, HostError, Result, Workspace},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::Read;

const NOTE_KEY: &str = "@markdown";
const MAX_REVIEW_BYTES: usize = 1024 * 1024;
const MAX_TOTAL_REVIEW_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Serialize)]
pub struct TemplateFileReview {
    pub key: String,
    pub target: SelectedFile,
    pub fingerprint: String,
    pub before: Option<String>,
    pub after: String,
    pub after_sha256: String,
}
#[derive(Debug, Serialize)]
pub struct TemplateReview {
    pub slot: String,
    pub package_name: String,
    pub package_version: String,
    pub entry: Option<String>,
    pub inputs: Vec<String>,
    pub files: Vec<TemplateFileReview>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TemplateFileApply {
    pub vault_id: String,
    pub slot: String,
    pub key: String,
    pub target: SelectedFile,
    pub fingerprint: String,
    pub operation_id: String,
}
fn invalid() -> HostError {
    HostError::new("EXTENSION_TEMPLATE_INVALID")
}
fn data(material: &RuntimePackage) -> Result<Option<extension_templates::ExperimentTemplate>> {
    if material.release.kind != "template" {
        return Err(HostError::new("EXTENSION_CANDIDATE_TYPE"));
    }
    extension_templates::validate_release(&material.release, &material.manifest)
}
fn content(material: &RuntimePackage, key: &str, path: &str) -> Result<String> {
    let template = data(material)?;
    if key == NOTE_KEY {
        if !path.to_ascii_lowercase().ends_with(".md")
            || path
                .split('/')
                .any(|part| part.eq_ignore_ascii_case("opennexus-records"))
        {
            return Err(invalid());
        }
        return material.manifest["markdown"]
            .as_str()
            .map(str::to_owned)
            .ok_or_else(invalid);
    }
    if !valid_path(path) {
        return Err(invalid());
    }
    template
        .and_then(|template| template.files.into_iter().find(|file| file.path == key))
        .map(|file| file.content)
        .ok_or_else(invalid)
}
fn fingerprint(
    store: &ExtensionStore,
    material: &RuntimePackage,
    vault: &str,
    key: &str,
    target: &SelectedFile,
    after: &str,
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
            "template-file-apply/v1",
            vault,
            &material.active,
            &material.release,
            &material.source,
            &material.signer_sha256,
            trust.fingerprint()?,
            key,
            target,
            hash(after.as_bytes()),
        ))
        .map_err(|_| invalid())?,
    ))
}
fn review_file(
    store: &ExtensionStore,
    material: &RuntimePackage,
    ws: &Workspace,
    key: &str,
    path: &str,
) -> Result<TemplateFileReview> {
    let destination = target(ws, path)?;
    let after = content(material, key, &destination.path)?;
    let before = if destination.revision == 0 {
        None
    } else {
        let mut bytes = Vec::new();
        std::fs::File::open(ws.resolve(&destination.path)?)?
            .take(MAX_REVIEW_BYTES as u64 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > MAX_REVIEW_BYTES {
            return Err(HostError::new("EXTENSION_TEMPLATE_TARGET_LIMIT"));
        }
        if hash(&bytes) != destination.hash {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        Some(String::from_utf8(bytes).map_err(|_| invalid())?)
    };
    check_target(ws, &destination)?;
    Ok(TemplateFileReview {
        key: key.into(),
        fingerprint: fingerprint(store, material, &ws.vault_id, key, &destination, &after)?,
        target: destination,
        before,
        after_sha256: hash(after.as_bytes()),
        after,
    })
}
pub fn template_preview(
    store: &ExtensionStore,
    ws: &mut Workspace,
    slot: &str,
    directory: &str,
    note_path: Option<&str>,
) -> Result<TemplateReview> {
    let material = store.runtime_package(slot, &ws.vault_id, None)?;
    let experiment = data(&material)?;
    let directory = directory.trim_end_matches('/');
    if experiment.is_some() && !valid_path(&format!("{directory}/__template__.py")) {
        return Err(invalid());
    }
    ws.scan()?;
    let mut files = Vec::new();
    if let Some(note_path) = note_path {
        files.push(review_file(store, &material, ws, NOTE_KEY, note_path)?);
    }
    let mut entry = None;
    let mut inputs = Vec::new();
    let mut review_bytes: usize = files
        .iter()
        .map(|file| file.before.as_ref().map_or(0, String::len) + file.after.len())
        .sum();
    if let Some(experiment) = experiment {
        for file in experiment.files {
            let review = review_file(
                store,
                &material,
                ws,
                &file.path,
                &format!("{directory}/{}", file.path),
            )?;
            if file.path == experiment.entry {
                entry = Some(review.target.path.clone());
            }
            if experiment.inputs.contains(&file.path) {
                inputs.push(review.target.path.clone());
            }
            review_bytes += review.before.as_ref().map_or(0, String::len) + review.after.len();
            if review_bytes > MAX_TOTAL_REVIEW_BYTES {
                return Err(HostError::new("EXTENSION_TEMPLATE_TARGET_LIMIT"));
            }
            files.push(review);
        }
    }
    if files.is_empty() {
        return Err(HostError::new("EXTENSION_TEMPLATE_EMPTY_TARGET"));
    }
    Ok(TemplateReview {
        slot: slot.into(),
        package_name: material.release.name,
        package_version: material.release.version,
        entry,
        inputs,
        files,
    })
}
pub fn template_apply_file(
    store: &ExtensionStore,
    ws: &mut Workspace,
    request: &TemplateFileApply,
    authorize: impl Fn() -> Result<()>,
) -> Result<Value> {
    authorize()?;
    if request.vault_id != ws.vault_id {
        return Err(HostError::new("VAULT_CHANGED"));
    }
    let digest = |value: &str| {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    };
    if !digest(&request.slot)
        || !digest(&request.fingerprint)
        || (!request.target.hash.is_empty() && !digest(&request.target.hash))
        || uuid::Uuid::parse_str(&request.target.file_id).is_err()
        || request.target.revision < 0
        || request.target.hash.is_empty() != (request.target.revision == 0)
    {
        return Err(invalid());
    }
    uuid::Uuid::parse_str(&request.operation_id)
        .map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
    let material = store.runtime_package(&request.slot, &ws.vault_id, None)?;
    let after = content(&material, &request.key, &request.target.path)?;
    if fingerprint(
        store,
        &material,
        &ws.vault_id,
        &request.key,
        &request.target,
        &after,
    )? != request.fingerprint
    {
        return Err(HostError::new("EXTENSION_CANDIDATE_CHANGED"));
    }
    if ws.operation(&request.operation_id)?.is_none() {
        ws.scan()?;
        check_target(ws, &request.target)?;
    }
    let entry: Entry = ws.write_authorized(
        &request.target.path,
        &request.target.hash,
        after.as_bytes(),
        "local",
        &request.operation_id,
        (Some(&request.target.file_id), &authorize),
    )?;
    if entry.file_id != request.target.file_id
        || entry.path != request.target.path
        || entry.hash != hash(after.as_bytes())
    {
        return Err(HostError::new("EXTENSION_TEMPLATE_RECEIPT_INVALID"));
    }
    Ok(
        json!({"operation_id":request.operation_id,"state":"applied","key":request.key,"entry":entry}),
    )
}

#[cfg(test)]
mod tests;
