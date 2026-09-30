//! Validate JSON Canvas at every local, remote, and recovery write boundary.
use crate::workspace::{HostError, Result};
use serde_json::Value;
use std::collections::HashSet;
use url::Url;

pub const MAX_CANVAS_BYTES: usize = 4 * 1024 * 1024;
const MAX_NODES: usize = 2_000;
const MAX_EDGES: usize = 4_000;

pub fn is_canvas(path: &str) -> bool {
    path.to_ascii_lowercase().ends_with(".canvas")
}

fn invalid() -> HostError {
    HostError::new("CANVAS_INVALID")
}

fn coordinate(value: &Value) -> bool {
    value
        .as_f64()
        .is_some_and(|number| number.is_finite() && (-1_000_000.0..=1_000_000.0).contains(&number))
}

fn dimension(value: &Value) -> bool {
    value
        .as_f64()
        .is_some_and(|number| number.is_finite() && (1.0..=100_000.0).contains(&number))
}

fn vault_file(value: &str) -> bool {
    !value.is_empty()
        && !value.starts_with('/')
        && !value.contains('\\')
        && !value.contains(':')
        && !value.contains('\0')
        && !value.chars().any(|character| {
            character.is_control()
                || matches!(character, '%' | '?' | '#' | '*' | '"' | '<' | '>' | '|')
        })
        && value.split('/').all(|part| {
            !part.is_empty()
                && part != "."
                && part != ".."
                && !part.starts_with('.')
                && !part.eq_ignore_ascii_case("opennexus-records")
        })
}

fn web_url(value: &str) -> bool {
    !value.chars().any(char::is_whitespace)
        && Url::parse(value).is_ok_and(|url| {
            matches!(url.scheme(), "http" | "https")
                && url.host_str().is_some_and(|host| !host.is_empty())
                && url.username().is_empty()
                && url.password().is_none()
                && url.port() != Some(0)
        })
}

fn optional(item: &serde_json::Map<String, Value>, key: &str, valid: impl Fn(&str) -> bool) -> bool {
    item.get(key).is_none_or(|value| value.as_str().is_some_and(valid))
}

fn color(value: &str) -> bool {
    matches!(value, "1" | "2" | "3" | "4" | "5" | "6")
        || (value.len() == 7 && value.starts_with('#') && value[1..].bytes().all(|byte| byte.is_ascii_hexdigit()))
}

pub fn validate(bytes: &[u8]) -> Result<()> {
    if bytes.len() > MAX_CANVAS_BYTES {
        return Err(HostError::new("CANVAS_TOO_LARGE"));
    }
    let document: Value = serde_json::from_slice(bytes).map_err(|_| invalid())?;
    let root = document.as_object().ok_or_else(invalid)?;
    let nodes = root.get("nodes").map(Value::as_array).transpose_array()?;
    let edges = root.get("edges").map(Value::as_array).transpose_array()?;
    if nodes.len() > MAX_NODES || edges.len() > MAX_EDGES {
        return Err(HostError::new("CANVAS_TOO_COMPLEX"));
    }
    let mut ids = HashSet::new();
    for node in nodes {
        let node = node.as_object().ok_or_else(invalid)?;
        let id = node.get("id").and_then(Value::as_str).ok_or_else(invalid)?;
        if id.is_empty() || id.len() > 128 || !ids.insert(id.to_owned()) {
            return Err(invalid());
        }
        if !node.get("x").is_some_and(coordinate)
            || !node.get("y").is_some_and(coordinate)
            || !node.get("width").is_some_and(dimension)
            || !node.get("height").is_some_and(dimension)
        {
            return Err(invalid());
        }
        if !optional(node, "color", color)
            || !optional(node, "subpath", |value| value.starts_with('#'))
            || !optional(node, "label", |_| true)
            || !optional(node, "background", vault_file)
            || !optional(node, "backgroundStyle", |value| matches!(value, "cover" | "ratio" | "repeat"))
        {
            return Err(invalid());
        }
        match node.get("type").and_then(Value::as_str) {
            Some("text") if node.get("text").and_then(Value::as_str).is_some() => {}
            Some("file")
                if node
                    .get("file")
                    .and_then(Value::as_str)
                    .is_some_and(vault_file) => {}
            Some("link") if node.get("url").and_then(Value::as_str).is_some_and(web_url) => {}
            Some("group") => {}
            _ => return Err(invalid()),
        }
    }
    let mut edge_ids = HashSet::new();
    for edge in edges {
        let edge = edge.as_object().ok_or_else(invalid)?;
        let id = edge.get("id").and_then(Value::as_str).ok_or_else(invalid)?;
        let from = edge
            .get("fromNode")
            .and_then(Value::as_str)
            .ok_or_else(invalid)?;
        let to = edge
            .get("toNode")
            .and_then(Value::as_str)
            .ok_or_else(invalid)?;
        if id.is_empty()
            || id.len() > 128
            || !edge_ids.insert(id)
            || !ids.contains(from)
            || !ids.contains(to)
        {
            return Err(invalid());
        }
        if !optional(edge, "color", color)
            || !optional(edge, "label", |_| true)
            || !["fromSide", "toSide"].iter().all(|key| optional(edge, key, |value| matches!(value, "left" | "right" | "top" | "bottom")))
            || !["fromEnd", "toEnd"].iter().all(|key| optional(edge, key, |value| matches!(value, "none" | "arrow")))
        {
            return Err(invalid());
        }
    }
    Ok(())
}

trait ArrayField<'a> {
    fn transpose_array(self) -> Result<&'a [Value]>;
}

impl<'a> ArrayField<'a> for Option<Option<&'a Vec<Value>>> {
    fn transpose_array(self) -> Result<&'a [Value]> {
        match self {
            Some(Some(values)) => Ok(values.as_slice()),
            Some(None) => Err(invalid()),
            None => Ok(&[]),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_standard_nodes_and_extra_fields() {
        let content = br##"{"nodes":[{"id":"a","type":"text","x":0,"y":-20,"width":200,"height":100,"text":"hi","custom":42},{"id":"b","type":"file","x":220,"y":0,"width":200,"height":100,"file":"notes/example.md"}],"edges":[{"id":"e","fromNode":"a","toNode":"b"}],"extra":{"keep":true}}"##;
        assert!(validate(content).is_ok());
    }

    #[test]
    fn imported_file_retains_standard_and_extension_fields_and_rejects_invalid_optionals() {
        let imported = include_bytes!("../../tests/fixtures/imported.canvas");
        assert!(validate(imported).is_ok());
        for (target, key, value) in [
            ("nodes", "color", serde_json::json!("url(evil)")),
            ("nodes", "background", serde_json::json!("../outside.png")),
            ("nodes", "backgroundStyle", serde_json::json!(42)),
            ("edges", "fromSide", serde_json::json!("diagonal")),
            ("edges", "label", serde_json::json!({})),
        ] {
            let mut document: Value = serde_json::from_slice(imported).unwrap();
            document[target][0][key] = value;
            assert!(validate(&serde_json::to_vec(&document).unwrap()).is_err());
        }
    }

    #[test]
    fn rejects_invalid_and_escaping_files() {
        for content in [
            br#"{"nodes":{}}"#.as_slice(),
            br#"{"nodes":[{"id":"a","type":"file","x":0,"y":0,"width":1,"height":1,"file":"../secret"}]}"#,
            br#"{"nodes":[{"id":"a","type":"file","x":0,"y":0,"width":1,"height":1,"file":"%2e%2e/secret"}]}"#,
            br#"{"nodes":[{"id":"a","type":"text","x":0,"y":0,"width":1,"height":1,"text":"a"}],"edges":[{"id":"e","fromNode":"a","toNode":"missing"}]}"#,
        ] {
            assert!(validate(content).is_err());
        }
    }

    #[test]
    fn accepts_fractional_layout_but_not_credential_urls() {
        assert!(validate(br#"{"nodes":[{"id":"l","type":"link","x":0.5,"y":-2.25,"width":100.5,"height":50,"url":"https://example.com/a"}]}"#).is_ok());
        assert!(validate(br#"{"nodes":[{"id":"l","type":"link","x":0,"y":0,"width":100,"height":50,"url":"https://[::1]/a"}]}"#).is_ok());
        assert!(validate(br#"{"nodes":[{"id":"l","type":"link","x":0,"y":0,"width":100,"height":50,"url":"https://example.com:0"}]}"#).is_err());
        assert!(validate(br#"{"nodes":[{"id":"l","type":"link","x":0,"y":0,"width":100,"height":50,"url":"https://user:pass@example.com"}]}"#).is_err());
    }
}
