//! 有界声明性清单检查。无包含、环境插值或代码执行。
use crate::{
    extension_package::{Inventory, Release},
    workspace::{HostError, Result},
};
use serde_json::Value;
use std::{
    collections::BTreeSet,
    io::{Cursor, Read},
};

fn invalid() -> HostError {
    HostError::new("EXTENSION_MANIFEST_INVALID")
}
pub fn read(release: &Release, archive: &[u8], inventory: &Inventory) -> Result<Value> {
    let mut zip = zip::ZipArchive::new(Cursor::new(archive)).map_err(|_| invalid())?;
    let file = zip.by_name(&inventory.manifest).map_err(|_| invalid())?;
    if file.size() > 1024 * 1024 {
        return Err(HostError::new("EXTENSION_MANIFEST_LIMIT"));
    }
    let mut bytes = Vec::new();
    file.take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| invalid())?;
    if bytes.len() > 1024 * 1024 {
        return Err(HostError::new("EXTENSION_MANIFEST_LIMIT"));
    }
    validate(release, &bytes)
}
pub fn validate(release: &Release, bytes: &[u8]) -> Result<Value> {
    if bytes.len() > 1024 * 1024 {
        return Err(HostError::new("EXTENSION_MANIFEST_LIMIT"));
    }
    let input = std::str::from_utf8(bytes).map_err(|_| invalid())?;
    let yaml = matches!(release.kind.as_str(), "theme" | "plugin" | "skill");
    if !yaml {
        serde_json::from_str::<Value>(input).map_err(|_| invalid())?;
    }
    let options = serde_saphyr::options! {
        duplicate_keys: serde_saphyr::options::DuplicateKeyPolicy::Error,
        with_snippet: false,
        budget: serde_saphyr::budget! { max_documents:1, max_depth:32, max_nodes:10000, max_events:20000, max_total_scalar_bytes:1024*1024, max_aliases:128, max_anchors:128, max_recorded_anchor_events:10000, max_recorded_anchor_bytes:1024*1024 },
    };
    let value: Value =
        serde_saphyr::from_str_with_options(input, options).map_err(|_| invalid())?;
    let map = value.as_object().ok_or_else(invalid)?;
    if map
        .get("schema_version")
        .is_some_and(|v| v.as_u64() != Some(1))
    {
        return Err(HostError::new("EXTENSION_MANIFEST_SCHEMA"));
    }
    if yaml {
        let identity = match release.kind.as_str() {
            "theme" => "theme_id",
            "plugin" => "plugin_id",
            _ => "skill_id",
        };
        let typed = map.get(identity);
        let common = map.get("id");
        if (release.kind == "theme" && typed.is_none())
            || typed.or(common).and_then(Value::as_str) != Some(release.package_id.as_str())
            || typed.zip(common).is_some_and(|(a, b)| a != b)
            || map.get("version").and_then(Value::as_str) != Some(release.version.as_str())
        {
            return Err(HostError::new("EXTENSION_MANIFEST_IDENTITY"));
        }
    }
    let permissions: Vec<String> = map
        .get("permissions")
        .map(|v| serde_json::from_value(v.clone()).map_err(|_| invalid()))
        .transpose()?
        .unwrap_or_default();
    let actual: BTreeSet<_> = permissions.iter().collect();
    let expected: BTreeSet<_> = release.permissions.iter().collect();
    if actual != expected || actual.len() != permissions.len() {
        return Err(HostError::new("EXTENSION_MANIFEST_PERMISSIONS"));
    }
    if !yaml {
        fn safe(v: &Value) -> bool {
            match v {
                Value::Object(map) => map.iter().all(|(k, v)| {
                    !matches!(
                        k.to_lowercase().as_str(),
                        "api_key" | "password" | "token" | "secret" | "chat_history" | "messages"
                    ) && safe(v)
                }),
                Value::Array(items) => items.iter().all(safe),
                _ => true,
            }
        }
        if !safe(&value) {
            return Err(HostError::new("EXTENSION_MANIFEST_SECRET"));
        }
        let valid = match release.kind.as_str() {
            "persona" => value["system_prompt"].is_string(),
            "template" => {
                value["markdown"].is_string()
                    && value
                        .get("executable")
                        .is_none_or(|v| v == false || v.is_null())
            }
            "model" => {
                ["source", "revision", "license"]
                    .iter()
                    .all(|k| value[*k].as_str().is_some_and(|s| !s.is_empty()))
                    && value["resources"]
                        .as_object()
                        .is_some_and(|v| !v.is_empty())
                    && value["verified_platforms"]
                        .as_array()
                        .is_some_and(|v| !v.is_empty() && v.iter().all(Value::is_string))
            }
            "mcp" => match value["transport"].as_str() {
                Some("stdio") => {
                    value["args"]
                        .as_array()
                        .is_some_and(|v| v.iter().all(Value::is_string))
                        && value
                            .get("command")
                            .is_none_or(|v| v.as_str().is_some_and(|s| !s.is_empty()))
                }
                Some("streamable_http" | "sse") => true,
                _ => false,
            },
            _ => false,
        };
        if !valid {
            return Err(invalid());
        }
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn release(kind: &str) -> Release {
        let mut v: Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        let value = v["release"].as_object_mut().unwrap();
        for key in ["release_id", "withdrawn", "download_path"] {
            value.remove(key);
        }
        value.insert("type".into(), Value::String(kind.into()));
        serde_json::from_value(v["release"].clone()).unwrap()
    }
    #[test]
    fn real_repository_manifests_match_identity_version_and_signed_permissions() {
        for (kind, id, bytes, permissions) in [
            (
                "plugin",
                "markdown-workbench",
                include_bytes!(
                    "../../../backend/extensions/community/plugins/markdown-workbench/plugin.yaml"
                )
                .as_slice(),
                vec![],
            ),
            (
                "skill",
                "note-reviewer",
                include_bytes!(
                    "../../../backend/extensions/community/skills/note-reviewer/skill.yaml"
                )
                .as_slice(),
                vec!["notes.search".into(), "notes.read".into()],
            ),
        ] {
            let mut r = release(kind);
            r.package_id = id.into();
            r.permissions = permissions;
            validate(&r, bytes).unwrap();
            r.version = "9.0.0".into();
            assert!(validate(&r, bytes).is_err());
        }
        assert!(validate(&release("theme"), b"id: test-package\nversion: 1.0.0").is_err());
        validate(&release("theme"), b"theme_id: test-package\nversion: 1.0.0").unwrap();
        let r = release("plugin");
        for bytes in [
            b"id: test-package\nplugin_id: other\nversion: 1.0.0".as_slice(),
            b"id: test-package\nversion: 1.0.0\npermissions: [notes.read]",
            b"id: test-package\nversion: 1.0.0\npermissions: false",
        ] {
            assert!(validate(&r, bytes).is_err());
        }
    }
    #[test]
    fn duplicate_keys_documents_depth_and_includes_are_rejected() {
        let r = release("plugin");
        for bytes in [
            b"id: test-package\nversion: 1.0.0\nversion: 2.0.0".as_slice(),
            b"id: test-package\nversion: 1.0.0\n---\nid: other",
            b"id: !include /etc/passwd\nversion: 1.0.0",
            b"id: test-package\nversion: 1.0.0\nx: &a [*a]",
        ] {
            assert!(validate(&r, bytes).is_err());
        }
        let deep = format!(
            "id: test-package\nversion: 1.0.0\nx: {}0{}",
            "[".repeat(40),
            "]".repeat(40)
        );
        assert!(validate(&r, deep.as_bytes()).is_err());
        assert_eq!(
            validate(&r, &vec![b' '; 1024 * 1024 + 1]).unwrap_err().code,
            "EXTENSION_MANIFEST_LIMIT"
        );
        assert_eq!(
            validate(&r, b"id: test-package\nversion: 1.0.0\nschema_version: 2")
                .unwrap_err()
                .code,
            "EXTENSION_MANIFEST_SCHEMA"
        );
        let persona = release("persona");
        assert!(validate(
            &persona,
            br#"{"system_prompt":"one","system_prompt":"two"}"#
        )
        .is_err());
        assert!(validate(&persona, b"system_prompt: YAML is not JSON").is_err());
    }
    #[test]
    fn declarative_types_enforce_contract_and_nested_secret_exclusion() {
        for (kind,bytes) in [("persona",br#"{"system_prompt":"hello"}"#.as_slice()),("template",br##"{"markdown":"# title","executable":false}"##),("mcp",br#"{"transport":"stdio","command":"python","args":["server.py"]}"#),("model",br#"{"source":"repository","revision":"fixed","license":"MIT","resources":{"ram_gb":8},"verified_platforms":["windows"]}"#)] {
            let r=release(kind); let valid=validate(&r,bytes).unwrap();
            for field in ["api_key","token","password","secret","messages","chat_history"] {
                let mut bad=valid.clone(); bad["nested"]=serde_json::json!([{field:"planted"}]);
                assert_eq!(validate(&r,&serde_json::to_vec(&bad).unwrap()).unwrap_err().code,"EXTENSION_MANIFEST_SECRET");
            }
        }
        assert!(validate(
            &release("template"),
            br#"{"markdown":"x","executable":true}"#
        )
        .is_err());
        assert!(validate(&release("mcp"), br#"{"transport":"stdio","args":[42]}"#).is_err());
    }
}
