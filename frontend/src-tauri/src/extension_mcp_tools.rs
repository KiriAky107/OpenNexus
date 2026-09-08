//! Bounded, offline MCP tool contracts. Descriptions/annotations are untrusted
//! data and never confer permissions. URI content is validated, never fetched.
use crate::workspace::{HostError, Result};
use base64::Engine;
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
};
const MAX_TOOLS: usize = 500;
const MAX_PAGES: usize = 100;
const MAX_CATALOG_BYTES: usize = 4 * 1024 * 1024;
fn invalid() -> HostError {
    HostError::new("EXTENSION_MCP_TOOL_SCHEMA_INVALID")
}
fn bounds(value: &Value, depth: usize, nodes: &mut usize, schema: bool) -> Result<()> {
    *nodes += 1;
    if depth > 24 || *nodes > 4096 {
        return Err(HostError::new("EXTENSION_MCP_TOOL_LIMIT"));
    }
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if schema && matches!(key.as_str(), "$ref" | "$dynamicRef" | "$recursiveRef") {
                    return Err(HostError::new("EXTENSION_MCP_SCHEMA_REFERENCE"));
                }
                bounds(child, depth + 1, nodes, schema)?;
            }
        }
        Value::Array(items) => {
            for child in items {
                bounds(child, depth + 1, nodes, schema)?;
            }
        }
        _ => {}
    }
    Ok(())
}
fn bounded(value: &Value, bytes: usize, schema: bool) -> Result<()> {
    bounds(value, 0, &mut 0, schema)?;
    if serde_json::to_vec(value).map_err(|_| invalid())?.len() > bytes {
        return Err(HostError::new("EXTENSION_MCP_TOOL_LIMIT"));
    }
    Ok(())
}
fn compile(schema: &Value) -> Result<jsonschema::Validator> {
    bounded(schema, 64 * 1024, true)?;
    if !schema.is_object()
        || schema["type"] != "object"
        || schema
            .get("$schema")
            .is_some_and(|v| v != "https://json-schema.org/draft/2020-12/schema")
    {
        return Err(invalid());
    }
    jsonschema::options()
        .offline()
        .with_draft(jsonschema::Draft::Draft202012)
        .with_pattern_options(jsonschema::PatternOptions::regex())
        .should_validate_formats(true)
        .should_ignore_unknown_formats(false)
        .build(schema)
        .map_err(|_| invalid())
}
#[derive(Clone, Serialize)]
pub struct Description {
    pub name: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub input_schema: Value,
    pub output_schema: Option<Value>,
}
pub struct Tool {
    description: Description,
    input: jsonschema::Validator,
    output: Option<jsonschema::Validator>,
}
impl Tool {
    pub(crate) fn description(&self) -> Description {
        self.description.clone()
    }
    fn parse(value: &Value) -> Result<Self> {
        bounded(value, 192 * 1024, false)?;
        let name = value["name"]
            .as_str()
            .filter(|s| {
                !s.is_empty()
                    && s.len() <= 128
                    && s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
            })
            .ok_or_else(invalid)?;
        fn optional(value: &Value, key: &str, max: usize) -> Result<Option<String>> {
            value
                .get(key)
                .map(|v| {
                    v.as_str()
                        .filter(|s| s.len() <= max)
                        .map(str::to_owned)
                        .ok_or_else(invalid)
                })
                .transpose()
        }
        if let Some(execution) = value.get("execution") {
            if !execution.is_object()
                || execution
                    .get("taskSupport")
                    .is_some_and(|v| v != "optional" && v != "forbidden")
            {
                return Err(HostError::new("EXTENSION_MCP_TASKS_UNSUPPORTED"));
            }
        }
        let input = compile(&value["inputSchema"])?;
        let output = value.get("outputSchema").map(compile).transpose()?;
        Ok(Self {
            description: Description {
                name: name.into(),
                title: optional(value, "title", 256)?,
                description: optional(value, "description", 16 * 1024)?,
                input_schema: value["inputSchema"].clone(),
                output_schema: value.get("outputSchema").cloned(),
            },
            input,
            output,
        })
    }
    pub fn validate_arguments(&self, arguments: &Value) -> Result<()> {
        bounded(arguments, 256 * 1024, false)?;
        if !arguments.is_object() || !self.input.is_valid(arguments) {
            return Err(HostError::new("EXTENSION_MCP_ARGUMENTS_INVALID"));
        }
        Ok(())
    }
    pub fn validate_result(&self, result: &Value) -> Result<()> {
        bounded(result, 256 * 1024, false)?;
        let bad = || HostError::new("EXTENSION_MCP_TOOL_RESULT_INVALID");
        if !result.is_object() || result.get("isError").is_some_and(|v| !v.is_boolean()) {
            return Err(bad());
        }
        let content = result["content"]
            .as_array()
            .filter(|v| v.len() <= 128)
            .ok_or_else(bad)?;
        fn uri(value: &Value) -> bool {
            value
                .as_str()
                .is_some_and(|s| s.len() <= 2048 && reqwest::Url::parse(s).is_ok())
        }
        fn binary(value: &Value) -> bool {
            value
                .as_str()
                .is_some_and(|s| base64::engine::general_purpose::STANDARD.decode(s).is_ok())
        }
        for item in content {
            let valid = match item["type"].as_str() {
                Some("text") => item["text"].is_string(),
                Some("image" | "audio") => {
                    binary(&item["data"])
                        && item["mimeType"].as_str().is_some_and(|s| {
                            s.len() <= 128
                                && s.starts_with(if item["type"] == "image" {
                                    "image/"
                                } else {
                                    "audio/"
                                })
                                && !s.chars().any(char::is_control)
                        })
                }
                Some("resource_link") => {
                    uri(&item["uri"])
                        && item["name"]
                            .as_str()
                            .is_some_and(|s| !s.is_empty() && s.len() <= 256)
                }
                Some("resource") => {
                    let resource = &item["resource"];
                    uri(&resource["uri"])
                        && match (resource.get("text"), resource.get("blob")) {
                            (Some(text), None) => text.is_string(),
                            (None, Some(blob)) => binary(blob),
                            _ => false,
                        }
                }
                _ => false,
            };
            if !valid {
                return Err(bad());
            }
        }
        if result
            .get("structuredContent")
            .is_some_and(|v| !v.is_object())
        {
            return Err(bad());
        }
        if result["isError"] != true {
            if let Some(output) = &self.output {
                if !result["structuredContent"].is_object()
                    || !output.is_valid(&result["structuredContent"])
                {
                    return Err(bad());
                }
            }
        }
        Ok(())
    }
}
pub struct Catalog {
    tools: BTreeMap<String, Arc<Tool>>,
}
impl Catalog {
    pub fn discover(mut fetch: impl FnMut(Option<&str>) -> Result<Value>) -> Result<Self> {
        let mut tools = BTreeMap::new();
        let mut seen = BTreeSet::new();
        let mut cursor: Option<String> = None;
        let mut bytes = 0;
        for _ in 0..MAX_PAGES {
            let page = fetch(cursor.as_deref())?;
            bytes += serde_json::to_vec(&page).map_err(|_| invalid())?.len();
            if bytes > MAX_CATALOG_BYTES {
                return Err(HostError::new("EXTENSION_MCP_CATALOG_LIMIT"));
            }
            let entries = page["tools"].as_array().ok_or_else(invalid)?;
            if entries.len() > MAX_TOOLS - tools.len() {
                return Err(HostError::new("EXTENSION_MCP_CATALOG_LIMIT"));
            }
            for entry in entries {
                let tool = Tool::parse(entry)?;
                if tools
                    .insert(tool.description.name.clone(), Arc::new(tool))
                    .is_some()
                {
                    return Err(HostError::new("EXTENSION_MCP_TOOL_DUPLICATE"));
                }
            }
            match page.get("nextCursor") {
                None => return Ok(Self { tools }),
                Some(next) => {
                    let next = next
                        .as_str()
                        .filter(|s| !s.is_empty() && s.len() <= 1024)
                        .ok_or_else(invalid)?;
                    if !seen.insert(next.to_owned()) {
                        return Err(HostError::new("EXTENSION_MCP_PAGINATION_CYCLE"));
                    }
                    cursor = Some(next.into());
                }
            }
        }
        Err(HostError::new("EXTENSION_MCP_CATALOG_LIMIT"))
    }
    pub fn descriptions(&self) -> Vec<Description> {
        self.tools
            .values()
            .map(|tool| tool.description.clone())
            .collect()
    }
    pub fn tool(&self, name: &str) -> Result<Arc<Tool>> {
        self.tools
            .get(name)
            .cloned()
            .ok_or_else(|| HostError::new("EXTENSION_MCP_TOOL_NOT_FOUND"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn tool() -> Value {
        json!({"name":"Echo.V1","inputSchema":{"type":"object","required":["count"],"additionalProperties":false,"properties":{"count":{"type":"integer","minimum":1,"maximum":3}}},"outputSchema":{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}},"annotations":{"readOnlyHint":true}})
    }
    #[test]
    fn paginated_catalog_is_atomic_unique_and_bounded() {
        let mut pages = 0;
        let catalog = Catalog::discover(|cursor| {
            pages += 1;
            if pages == 1 {
                assert!(cursor.is_none());
                Ok(json!({"tools":[tool()],"nextCursor":"two"}))
            } else {
                assert_eq!(cursor, Some("two"));
                let mut second = tool();
                second["name"] = json!("second");
                Ok(json!({"tools":[second]}))
            }
        })
        .unwrap();
        assert_eq!(catalog.descriptions().len(), 2);
        assert_eq!(
            catalog.tool("missing").err().unwrap().code,
            "EXTENSION_MCP_TOOL_NOT_FOUND"
        );
        let mut pages = 0;
        assert_eq!(
            Catalog::discover(|_| {
                pages += 1;
                Ok(if pages == 1 {
                    json!({"tools":[tool()],"nextCursor":"two"})
                } else {
                    json!({"tools":[tool()]})
                })
            })
            .err()
            .unwrap()
            .code,
            "EXTENSION_MCP_TOOL_DUPLICATE"
        );
        assert_eq!(
            Catalog::discover(|_| Ok(json!({"tools":[],"nextCursor":"same"})))
                .err()
                .unwrap()
                .code,
            "EXTENSION_MCP_PAGINATION_CYCLE"
        );
        assert_eq!(
            Catalog::discover(|_| Ok(json!({"tools":vec![tool();501]})))
                .err()
                .unwrap()
                .code,
            "EXTENSION_MCP_CATALOG_LIMIT"
        );
        let mut pages = 0;
        assert_eq!(
            Catalog::discover(|_| {
                pages += 1;
                Ok(json!({"tools":[],"nextCursor":pages.to_string()}))
            })
            .err()
            .unwrap()
            .code,
            "EXTENSION_MCP_CATALOG_LIMIT"
        );
        assert_eq!(pages, 100);
        assert_eq!(
            Catalog::discover(|_| Ok(json!({"tools":[],"padding":"x".repeat(MAX_CATALOG_BYTES)})))
                .err()
                .unwrap()
                .code,
            "EXTENSION_MCP_CATALOG_LIMIT"
        );
    }
    #[test]
    fn contracts_validate_arguments_structured_results_and_never_resolve_references() {
        let contract = Tool::parse(&tool()).unwrap();
        contract.validate_arguments(&json!({"count":2})).unwrap();
        for arguments in [
            json!({}),
            json!({"count":0}),
            json!({"count":"2"}),
            json!({"count":2,"extra":true}),
        ] {
            assert_eq!(
                contract.validate_arguments(&arguments).unwrap_err().code,
                "EXTENSION_MCP_ARGUMENTS_INVALID"
            );
        }
        let result =
            json!({"content":[{"type":"text","text":"ok"}],"structuredContent":{"ok":true}});
        contract.validate_result(&result).unwrap();
        for result in [
            json!({"content":[]}),
            json!({"content":[],"structuredContent":{"ok":"wrong"}}),
            json!({"content":[{"type":"image","mimeType":"image/png","data":"invalid base64 !"}],"structuredContent":{"ok":true}}),
            json!({"content":[{"type":"text","text":42}],"structuredContent":{"ok":true}}),
        ] {
            assert!(contract.validate_result(&result).is_err());
        }
        contract
            .validate_result(
                &json!({"content":[{"type":"text","text":"tool failed"}],"isError":true}),
            )
            .unwrap();
        for target in [
            "https://example.invalid/schema",
            "file:///C:/private",
            "#/$defs/recursive",
        ] {
            let mut value = tool();
            value["inputSchema"]["$ref"] = json!(target);
            assert_eq!(
                Tool::parse(&value).err().unwrap().code,
                "EXTENSION_MCP_SCHEMA_REFERENCE"
            );
        }
        for bad in [
            json!({"type":"object","properties":{"value":{"pattern":"(?=x)"}}}),
            json!({"type":"object","$schema":"unknown"}),
            json!({"type":"array"}),
        ] {
            let mut value = tool();
            value["inputSchema"] = bad;
            assert!(Tool::parse(&value).is_err());
        }
        let mut value = tool();
        value["execution"] = json!({"taskSupport":"required"});
        assert_eq!(
            Tool::parse(&value).err().unwrap().code,
            "EXTENSION_MCP_TASKS_UNSUPPORTED"
        );
        let mut deep = json!({});
        for _ in 0..25 {
            deep = json!({"nested":deep});
        }
        assert_eq!(
            contract.validate_arguments(&deep).unwrap_err().code,
            "EXTENSION_MCP_TOOL_LIMIT"
        );
    }
}
