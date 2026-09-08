//! Preference schemas contain portable values only; no paths, permissions, providers or secrets.
use crate::workspace::{HostError, Result};
use serde::Deserialize;
use serde_json::Value;
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
pub fn validate(kind: &str, value: &Value) -> Result<()> {
    let valid = match kind {
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
