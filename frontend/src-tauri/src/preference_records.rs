//! Portable settings may declare required permissions, but never carry device grants, paths or secrets.
use crate::workspace::{HostError, Result};
use serde::Deserialize;
use serde_json::Value;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DialoguePair {
    user: String,
    assistant: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Persona {
    version: u64,
    name: String,
    system_prompt: String,
    dialogue_pairs: Vec<DialoguePair>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UserSkillRetrieval {
    top_k: u8,
    rerank: bool,
    citation: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UserSkill {
    version: u64,
    name: String,
    description: String,
    prompt: String,
    tools: Vec<String>,
    permissions: Vec<String>,
    retrieval: UserSkillRetrieval,
    required_capabilities: Vec<String>,
    created_at_ms: i64,
    updated_at_ms: i64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Layout {
    primary_expanded: bool,
    workspace_width: f64,
    chat_width: f64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Level {
    size: f64,
    weight: u16,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Headings {
    custom: bool,
    family: String,
    levels: Vec<Level>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Theme {
    theme_id: String,
    font_editor_size: f64,
    font_editor_family: String,
    line_height: f64,
    code_block_theme: String,
    headings: Headings,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Markdown {
    heading: String,
    bullet: String,
    increment_list: bool,
    fence: String,
    math: bool,
    callouts: bool,
    diagrams: bool,
    auto_links: bool,
    line_numbers: bool,
    wrap_code: bool,
    indent: u8,
    default_language: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Preset {
    name: String,
    preferences: Markdown,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Preferences {
    restore_last_vault: bool,
    auto_save_interval: u32,
    language: String,
    default_editor_mode: String,
    editor_line_width: u16,
    spell_check: bool,
    markdown: Markdown,
    presets: Vec<Preset>,
}
fn decode<T: serde::de::DeserializeOwned>(value: &Value) -> Result<T> {
    serde_json::from_value(value.clone()).map_err(|_| HostError::new("RECORD_SCHEMA_INVALID"))
}
fn markdown(value: &Markdown) -> bool {
    let _ = (
        value.increment_list,
        value.math,
        value.callouts,
        value.diagrams,
        value.auto_links,
        value.line_numbers,
        value.wrap_code,
    );
    matches!(value.heading.as_str(), "atx" | "setext")
        && matches!(value.bullet.as_str(), "-" | "*" | "+")
        && matches!(value.fence.as_str(), "`" | "~")
        && [2, 4, 8].contains(&value.indent)
        && value.default_language.len() <= 40
        && value
            .default_language
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_+-".contains(&b))
}
fn unique_bounded(values: &[String], max_items: usize, max_chars: usize) -> bool {
    values.len() <= max_items
        && values.iter().all(|value| {
            !value.is_empty()
                && value.chars().count() <= max_chars
                && value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        })
        && values
            .iter()
            .enumerate()
            .all(|(index, value)| !values[..index].contains(value))
}
pub fn validate(kind: &str, value: &Value) -> Result<()> {
    let valid = match kind {
        "persona" => {
            let value: Persona = decode(value)?;
            value.version <= 9007199254740991
                && value.name.chars().count() <= 128
                && value.system_prompt.chars().count() <= 16000
                && value.dialogue_pairs.len() <= 20
                && value.dialogue_pairs.iter().all(|pair| {
                    pair.user.chars().count() <= 8000 && pair.assistant.chars().count() <= 8000
                })
        }
        "layout" => {
            let value: Layout = decode(value)?;
            let _ = value.primary_expanded;
            (200.0..=520.0).contains(&value.workspace_width)
                && (200.0..=520.0).contains(&value.chat_width)
        }
        "user_skill" => {
            let value: UserSkill = decode(value)?;
            let _ = (value.retrieval.rerank, value.retrieval.citation);
            let timestamp = |time: i64| (0..=253402300799999).contains(&time);
            let known_permissions = [
                "notes.read",
                "notes.search",
                "notes.write",
                "notes.delete",
                "tasks.read",
                "tasks.write",
                "attachments.read",
                "network.request",
                "secrets.use",
                "ui.command",
                "ui.settings",
                "ui.sidebar",
            ];
            let known_capabilities = [
                "chat",
                "vision",
                "tool_calling",
                "reasoning",
                "streaming",
                "structured_output",
                "embedding",
                "transcription",
                "speaker_matching",
            ];
            value.version <= 9007199254740991
                && !value.name.trim().is_empty()
                && value.name.chars().count() <= 128
                && value.description.chars().count() <= 2000
                && value.prompt.chars().count() <= 64000
                && unique_bounded(&value.tools, 64, 128)
                && unique_bounded(&value.permissions, 32, 64)
                && value
                    .permissions
                    .iter()
                    .all(|v| known_permissions.contains(&v.as_str()))
                && unique_bounded(&value.required_capabilities, 16, 64)
                && value
                    .required_capabilities
                    .iter()
                    .all(|v| known_capabilities.contains(&v.as_str()))
                && (1..=100).contains(&value.retrieval.top_k)
                && timestamp(value.created_at_ms)
                && timestamp(value.updated_at_ms)
                && value.updated_at_ms >= value.created_at_ms
        }
        "theme_settings" => {
            let value: Theme = decode(value)?;
            let _ = value.headings.custom;
            !value.theme_id.is_empty()
                && value.theme_id.len() <= 128
                && value
                    .theme_id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
                && (6.0..=72.0).contains(&value.font_editor_size)
                && (1.0..=3.0).contains(&value.line_height)
                && !value.font_editor_family.is_empty()
                && value.font_editor_family.len() <= 256
                && !value
                    .font_editor_family
                    .chars()
                    .any(|v| v.is_control() || ";{}\\".contains(v))
                && matches!(
                    value.code_block_theme.as_str(),
                    "auto" | "github-light" | "github-dark"
                )
                && matches!(
                    value.headings.family.as_str(),
                    "inherit" | "serif" | "sans-serif" | "monospace"
                )
                && value.headings.levels.len() == 6
                && value.headings.levels.iter().all(|level| {
                    (12.0..=72.0).contains(&level.size)
                        && [400, 500, 600, 700, 800].contains(&level.weight)
                })
        }
        "preferences" => {
            let value: Preferences = decode(value)?;
            let _ = (value.restore_last_vault, value.spell_check);
            (50..=60000).contains(&value.auto_save_interval)
                && matches!(value.language.as_str(), "zh-CN" | "en")
                && matches!(value.default_editor_mode.as_str(), "source" | "wysiwyg")
                && (40..=200).contains(&value.editor_line_width)
                && markdown(&value.markdown)
                && value.presets.len() <= 20
                && value.presets.iter().all(|p| {
                    !p.name.trim().is_empty()
                        && p.name.chars().count() <= 40
                        && markdown(&p.preferences)
                })
        }
        _ => return Err(HostError::new("RECORD_SCHEMA_UNSUPPORTED")),
    };
    if valid {
        Ok(())
    } else {
        Err(HostError::new("RECORD_DATA_INVALID"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn user_skill_schema_is_portable_strict_and_default_synced() {
        let id = "user_skill_00000000000000000000000000000001";
        let path = crate::records::path_for("user_skill", id).unwrap();
        let data = json!({"version":1,"name":"Review","description":"Check a note","prompt":"Be precise.","tools":["notes.read"],"permissions":["notes.read"],"retrieval":{"top_k":10,"rerank":true,"citation":true},"required_capabilities":["chat","tool_calling"],"created_at_ms":1,"updated_at_ms":2});
        let record = json!({"schema":1,"kind":"user_skill","id":id,"data":data});
        crate::records::validate(&path, &serde_json::to_vec(&record).unwrap()).unwrap();
        assert!(crate::records::allowed(&path));
        assert!(crate::sync_scope::OptionalScope::default().includes(&path));
        for field in [
            "api_key",
            "package_path",
            "enabled",
            "device_grants",
            "environment",
        ] {
            let mut bad = data.clone();
            bad[field] = json!("private");
            assert_eq!(
                validate("user_skill", &bad).unwrap_err().code,
                "RECORD_SCHEMA_INVALID"
            );
        }
        let mut bad = data.clone();
        bad["permissions"] = json!(["notes.read", "unknown.permission"]);
        assert_eq!(
            validate("user_skill", &bad).unwrap_err().code,
            "RECORD_DATA_INVALID"
        );
        assert!(crate::records::path_for("user_skill", "user_skill_ABCD").is_err());
    }
    #[test]
    fn layout_schema_limits_widths_and_rejects_device_fields() {
        let good = json!({"primaryExpanded":true,"workspaceWidth":400.5,"chatWidth":320});
        let path = crate::records::path_for("layout", "sidebars").unwrap();
        assert!(crate::records::allowed(&path));
        let record = json!({"schema":1,"kind":"layout","id":"sidebars","data":good});
        crate::records::validate(&path, &serde_json::to_vec(&record).unwrap()).unwrap();
        for invalid in [199.0, 521.0, -1.0] {
            let mut data = good.clone();
            data["workspaceWidth"] = json!(invalid);
            assert!(validate("layout", &data).is_err());
        }
        let mut bad = good;
        bad["windowPath"] = json!("private-device-path");
        assert!(validate("layout", &bad).is_err());
        assert!(crate::records::path_for("layout", "other").is_err());
    }
    #[test]
    fn portable_preferences_reject_unowned_nested_fields_and_invalid_values() {
        let theme = json!({"themeId":"dark","fontEditorSize":18,"fontEditorFamily":"system-ui","lineHeight":1.7,"codeBlockTheme":"auto","headings":{"custom":false,"family":"inherit","levels":([32,28,24,21,18,16].map(|size|json!({"size":size,"weight":700})))}});
        validate("theme_settings", &theme).unwrap();
        let markdown = json!({"heading":"atx","bullet":"-","incrementList":true,"fence":"`","math":true,"callouts":true,"diagrams":true,"autoLinks":true,"lineNumbers":true,"wrapCode":false,"indent":4,"defaultLanguage":""});
        let preferences = json!({"restoreLastVault":true,"autoSaveInterval":1500,"language":"zh-CN","defaultEditorMode":"wysiwyg","editorLineWidth":80,"spellCheck":false,"markdown":markdown,"presets":[]});
        validate("preferences", &preferences).unwrap();
        for field in [
            "apiKey",
            "permissions",
            "environment",
            "vaultPath",
            "provider",
        ] {
            let mut bad = preferences.clone();
            bad[field] = json!("planted-secret");
            assert_eq!(
                validate("preferences", &bad).unwrap_err().code,
                "RECORD_SCHEMA_INVALID"
            );
        }
        let mut bad = preferences.clone();
        bad["markdown"]["apiKey"] = json!("planted-secret");
        assert_eq!(
            validate("preferences", &bad).unwrap_err().code,
            "RECORD_SCHEMA_INVALID"
        );
        let mut bad = theme;
        bad["fontEditorFamily"] = json!("x;url(secret)");
        assert_eq!(
            validate("theme_settings", &bad).unwrap_err().code,
            "RECORD_DATA_INVALID"
        );
    }
}
