//! 可移植设置可以声明所需的权限，但绝不携带设备授权、路径或秘密。
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
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConversationMessage {
    message_id: String,
    parent_message_id: Option<String>,
    role: String,
    content: String,
    thinking: Option<String>,
    attachments: Vec<String>,
    created_at_ms: i64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Conversation {
    title: String,
    active_leaf: Option<String>,
    created_at_ms: i64,
    updated_at_ms: i64,
    messages: Vec<ConversationMessage>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AgentHistory {
    status: String,
    input: String,
    output: Option<String>,
    model: String,
    skill_id: Option<String>,
    error_code: Option<String>,
    error_message: Option<String>,
    token_usage: u64,
    created_at_ms: i64,
    updated_at_ms: i64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProviderSettings {
    version: u64,
    provider_type: String,
    name: String,
    base_url: Option<String>,
    default_model: Option<String>,
    enabled: bool,
    capabilities: Vec<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExtensionInstallation {
    package_kind: String,
    package_id: String,
    source: String,
    version: String,
    sha256: String,
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
fn bounded_identifier(value: &str, max_chars: usize) -> bool {
    !value.is_empty()
        && value.chars().count() <= max_chars
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
}
fn portable_reference(value: &str, max_chars: usize) -> bool {
    !value.is_empty()
        && value.chars().count() <= max_chars
        && !value.starts_with('/')
        && !value.contains(['\\', '\0'])
        && value
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
}
fn model_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.chars().count() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:/+-".contains(&byte))
}
fn timestamp(value: i64) -> bool {
    (0..=253402300799999).contains(&value)
}
fn safe_base_url(value: &str) -> bool {
    let Some(rest) = value
        .strip_prefix("https://")
        .or_else(|| value.strip_prefix("http://"))
    else {
        return false;
    };
    !rest.is_empty()
        && !rest.bytes().any(|byte| byte.is_ascii_control())
        && !rest.contains(['@', '?', '#', '\\'])
        && rest.split('/').next().is_some_and(|host| !host.is_empty())
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
        "conversation" => {
            let value: Conversation = decode(value)?;
            let message_id = |id: &str| bounded_identifier(id, 128);
            !value.title.trim().is_empty()
                && value.title.chars().count() <= 120
                && timestamp(value.created_at_ms)
                && timestamp(value.updated_at_ms)
                && value.updated_at_ms >= value.created_at_ms
                && value.messages.len() <= 2_000
                && value.active_leaf.as_deref().is_none_or(|id| {
                    message_id(id) && value.messages.iter().any(|v| v.message_id == id)
                })
                && value.messages.iter().enumerate().all(|(index, message)| {
                    message_id(&message.message_id)
                        && !value.messages[..index]
                            .iter()
                            .any(|previous| previous.message_id == message.message_id)
                        && message.parent_message_id.as_deref().is_none_or(|id| {
                            message_id(id)
                                && id != message.message_id
                                && value
                                    .messages
                                    .iter()
                                    .any(|candidate| candidate.message_id == id)
                        })
                        && matches!(message.role.as_str(), "user" | "assistant" | "system")
                        && message.content.chars().count() <= 262_144
                        && message
                            .thinking
                            .as_ref()
                            .is_none_or(|text| text.chars().count() <= 262_144)
                        && message.attachments.len() <= 64
                        && message
                            .attachments
                            .iter()
                            .all(|path| portable_reference(path, 512))
                        && message
                            .attachments
                            .iter()
                            .enumerate()
                            .all(|(index, path)| !message.attachments[..index].contains(path))
                        && timestamp(message.created_at_ms)
                })
        }
        "agent_history" => {
            let value: AgentHistory = decode(value)?;
            matches!(value.status.as_str(), "completed" | "failed" | "cancelled")
                && value.input.chars().count() <= 262_144
                && value
                    .output
                    .as_ref()
                    .is_none_or(|text| text.chars().count() <= 524_288)
                && model_identifier(&value.model)
                && value
                    .skill_id
                    .as_deref()
                    .is_none_or(|id| bounded_identifier(id, 128))
                && value
                    .error_code
                    .as_deref()
                    .is_none_or(|code| bounded_identifier(code, 128))
                && value
                    .error_message
                    .as_ref()
                    .is_none_or(|message| message.chars().count() <= 4_096)
                && value.token_usage <= 9_007_199_254_740_991
                && timestamp(value.created_at_ms)
                && timestamp(value.updated_at_ms)
                && value.updated_at_ms >= value.created_at_ms
        }
        "provider_settings" => {
            let value: ProviderSettings = decode(value)?;
            let _ = value.enabled;
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
            value.version <= 9_007_199_254_740_991
                && matches!(
                    value.provider_type.as_str(),
                    "mock"
                        | "openai_responses"
                        | "openai_chat"
                        | "openai_compatible"
                        | "anthropic_messages"
                        | "ollama"
                )
                && !value.name.trim().is_empty()
                && value.name.chars().count() <= 128
                && value
                    .base_url
                    .as_deref()
                    .is_none_or(|url| url.len() <= 2_048 && safe_base_url(url))
                && value.default_model.as_deref().is_none_or(model_identifier)
                && unique_bounded(&value.capabilities, 16, 64)
                && value
                    .capabilities
                    .iter()
                    .all(|capability| known_capabilities.contains(&capability.as_str()))
        }
        "extension_installation" => {
            let value: ExtensionInstallation = decode(value)?;
            matches!(value.package_kind.as_str(), "skill" | "plugin" | "theme")
                && bounded_identifier(&value.package_id, 128)
                && !value.source.is_empty()
                && value.source.len() <= 512
                && value
                    .source
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._:/-".contains(&byte))
                && !value.source.contains("..")
                && !value.source.contains('@')
                && !value.source.contains(['?', '#', '\\'])
                && !value.version.is_empty()
                && value.version.len() <= 128
                && value
                    .version
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b".+-_".contains(&byte))
                && value.sha256.len() == 64
                && value
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
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

    #[test]
    fn s08_optional_schemas_are_strict_and_installations_cannot_authorize() {
        let fixtures = [
            (
                "conversation",
                "conversation_00000000000000000000000000000001",
                json!({"title":"Review","active_leaf":"message_1","created_at_ms":1,"updated_at_ms":2,"messages":[{"message_id":"message_1","parent_message_id":null,"role":"user","content":"Hello","thinking":null,"attachments":[],"created_at_ms":1}]}),
            ),
            (
                "agent_history",
                "agent_run_00000000000000000000000000000001",
                json!({"status":"completed","input":"Review","output":"Done","model":"gpt-5.6","skill_id":null,"error_code":null,"error_message":null,"token_usage":42,"created_at_ms":1,"updated_at_ms":2}),
            ),
            (
                "provider_settings",
                "provider_00000000000000000000000000000001",
                json!({"version":1,"provider_type":"openai_responses","name":"OpenAI","base_url":"https://api.openai.com/v1","default_model":"gpt-5.6","enabled":true,"capabilities":["chat","tool_calling"]}),
            ),
            (
                "extension_installation",
                "extension_00000000000000000000000000000001",
                json!({"package_kind":"plugin","package_id":"opennexus.review","source":"community/opennexus.review","version":"1.2.3","sha256":"a".repeat(64)}),
            ),
        ];
        for (kind, id, data) in fixtures {
            let path = crate::records::path_for(kind, id).unwrap();
            let record = json!({"schema":1,"kind":kind,"id":id,"data":data});
            crate::records::validate(&path, &serde_json::to_vec(&record).unwrap()).unwrap();
            assert!(crate::records::allowed(&path));
            assert!(!crate::sync_scope::OptionalScope::default().includes(&path));
            let mut unknown = record.clone();
            unknown["data"]["api_key"] = json!("must-never-sync");
            assert_eq!(
                crate::records::validate(&path, &serde_json::to_vec(&unknown).unwrap())
                    .err()
                    .unwrap()
                    .code,
                "RECORD_SCHEMA_INVALID"
            );
        }

        let installation = json!({"package_kind":"plugin","package_id":"opennexus.review","source":"community/opennexus.review","version":"1.2.3","sha256":"a".repeat(64)});
        for forbidden in [
            "permissions",
            "granted_permissions",
            "enabled",
            "trusted",
            "device_grants",
            "package_path",
        ] {
            let mut invalid = installation.clone();
            invalid[forbidden] = json!(true);
            assert_eq!(
                validate("extension_installation", &invalid)
                    .unwrap_err()
                    .code,
                "RECORD_SCHEMA_INVALID"
            );
        }
        let mut provider = json!({"version":1,"provider_type":"openai_responses","name":"OpenAI","base_url":"https://api.openai.com/v1","default_model":"gpt-5.6","enabled":true,"capabilities":["chat"]});
        provider["credential_id"] = json!("device-secret-reference");
        assert_eq!(
            validate("provider_settings", &provider).unwrap_err().code,
            "RECORD_SCHEMA_INVALID"
        );
    }
}
