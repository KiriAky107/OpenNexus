//! Portable experiment-template data. Import and execution need separate decisions.
use crate::{
    experiment_input::valid_path,
    extension_package::Release,
    workspace::{HostError, Result},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const MAX_TEMPLATE_FILES: usize = 32;
pub const MAX_TEMPLATE_FILE_BYTES: usize = 64 * 1024;
pub const MAX_TEMPLATE_TOTAL_BYTES: usize = 512 * 1024;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TemplateFile {
    pub path: String,
    pub content: String,
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExperimentTemplate {
    pub schema_version: u32,
    pub entry: String,
    pub inputs: Vec<String>,
    pub files: Vec<TemplateFile>,
}
fn invalid() -> HostError {
    HostError::new("EXTENSION_TEMPLATE_INVALID")
}
pub fn validate_release(release: &Release, manifest: &Value) -> Result<Option<ExperimentTemplate>> {
    let template = validate(manifest)?;
    if template.is_some()
        && semver::Version::parse(&release.min_app_version).map_err(|_| invalid())?
            < semver::Version::new(0, 6, 0)
    {
        return Err(HostError::new("EXTENSION_TEMPLATE_APP_VERSION"));
    }
    Ok(template)
}
pub fn validate(manifest: &Value) -> Result<Option<ExperimentTemplate>> {
    if !manifest["markdown"].is_string()
        || manifest
            .get("executable")
            .is_some_and(|value| !value.is_null() && value != false)
    {
        return Err(invalid());
    }
    let Some(value) = manifest.get("experiment") else {
        return Ok(None);
    };
    let template: ExperimentTemplate =
        serde_json::from_value(value.clone()).map_err(|_| invalid())?;
    if template.schema_version != 1
        || template.files.is_empty()
        || template.files.len() > MAX_TEMPLATE_FILES
        || template.inputs.len() >= MAX_TEMPLATE_FILES
        || !template.entry.to_ascii_lowercase().ends_with(".py")
    {
        return Err(invalid());
    }
    let mut paths = BTreeSet::new();
    let mut files = BTreeMap::new();
    let mut total = 0usize;
    for file in &template.files {
        let portable_key = file.path.to_uppercase();
        if file.path.len() > 512
            || !valid_path(&format!("experiments/{}", file.path))
            || file.content.len() > MAX_TEMPLATE_FILE_BYTES
            || !paths.insert(portable_key)
        {
            return Err(invalid());
        }
        total += file.content.len();
        files.insert(file.path.as_str(), file);
    }
    if total > MAX_TEMPLATE_TOTAL_BYTES
        || paths.iter().any(|path| {
            path.split('/').enumerate().skip(1).any(|(index, _)| {
                paths.contains(&path.split('/').take(index).collect::<Vec<_>>().join("/"))
            })
        })
        || !files.contains_key(template.entry.as_str())
    {
        return Err(invalid());
    }
    let mut selected = BTreeSet::from([template.entry.as_str()]);
    if template
        .inputs
        .iter()
        .any(|path| !files.contains_key(path.as_str()) || !selected.insert(path.as_str()))
    {
        return Err(invalid());
    }
    Ok(Some(template))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn shared_templates_preserve_text_and_reject_unsafe_or_unbound_inputs() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-v1-templates.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        for case in fixture["cases"].as_array().unwrap() {
            let result = validate(&case["manifest"]);
            assert_eq!(
                result.is_ok(),
                case["accepted"].as_bool().unwrap(),
                "{}",
                case["name"]
            );
            if let Some(template) = result.ok().flatten() {
                assert_eq!(
                    serde_json::to_value(template).unwrap(),
                    case["manifest"]["experiment"]
                );
            }
        }
    }
    #[test]
    fn limits_count_utf8_bytes_and_each_file_and_the_whole_template() {
        let mut value = json!({"markdown":"# Example","experiment":{"schema_version":1,"entry":"main.py","inputs":[],"files":[{"path":"main.py","content":"x".repeat(MAX_TEMPLATE_FILE_BYTES)}]}});
        validate(&value).unwrap();
        value["experiment"]["files"][0]["content"] =
            json!("中".repeat(MAX_TEMPLATE_FILE_BYTES / 3 + 1));
        assert!(validate(&value).is_err());
        let files: Vec<_> = (0..32)
            .map(|index| json!({"path":format!("{index}.py"),"content":""}))
            .collect();
        value["experiment"]["entry"] = json!("0.py");
        value["experiment"]["files"] = json!(files);
        validate(&value).unwrap();
        value["experiment"]["files"]
            .as_array_mut()
            .unwrap()
            .push(json!({"path":"extra.py","content":""}));
        assert!(validate(&value).is_err());
        value["experiment"]["files"] = json!((0..9).map(|index| json!({"path":format!("{index}.py"),"content":"x".repeat(MAX_TEMPLATE_FILE_BYTES)})).collect::<Vec<_>>());
        assert!(validate(&value).is_err());
    }
}
