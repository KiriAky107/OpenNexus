//! Signed, offline configuration schemas. Credentials belong to the vault broker.
use crate::workspace::{HostError, Result};
use serde_json::{json, Value};

fn walk(value: &Value, depth: usize, nodes: &mut usize, schema: bool) -> Result<()> {
    if depth > 24 || *nodes >= 4096 {
        return Err(HostError::new("EXTENSION_CONFIG_LIMIT"));
    }
    *nodes += 1;
    match value {
        Value::Object(map) => {
            for (key, value) in map {
                if schema && matches!(key.as_str(), "$ref" | "$dynamicRef" | "$recursiveRef") {
                    // Recursive/unbounded schema execution is not allowed in the Host.
                    return Err(HostError::new("EXTENSION_CONFIG_REFERENCE"));
                }
                if !schema
                    && matches!(
                        key.to_ascii_lowercase().as_str(),
                        "api_key"
                            | "apikey"
                            | "password"
                            | "token"
                            | "secret"
                            | "access_token"
                            | "refresh_token"
                            | "authorization"
                            | "client_secret"
                            | "private_key"
                    )
                {
                    return Err(HostError::new("EXTENSION_CONFIG_SECRET"));
                }
                walk(value, depth + 1, nodes, schema)?;
            }
        }
        Value::Array(items) => {
            for item in items {
                walk(item, depth + 1, nodes, schema)?;
            }
        }
        _ => {}
    }
    Ok(())
}

// Examine every applicable schema branch; an alternative branch must not
// turn a secret declaration back into persistable plaintext.
fn reject_secrets(schema: &Value, instance: &Value) -> Result<()> {
    let Some(map) = schema.as_object() else {
        return Ok(());
    };
    if map.get("writeOnly") == Some(&Value::Bool(true))
        || map.get("x-opennexus-secret") == Some(&Value::Bool(true))
    {
        return Err(HostError::new("EXTENSION_CONFIG_SECRET"));
    }
    for key in ["allOf", "anyOf", "oneOf"] {
        if let Some(items) = map.get(key).and_then(Value::as_array) {
            for item in items {
                reject_secrets(item, instance)?;
            }
        }
    }
    for key in ["if", "then", "else", "not"] {
        if let Some(child) = map.get(key) {
            reject_secrets(child, instance)?;
        }
    }
    if let Some(properties) = instance.as_object() {
        for (name, value) in properties {
            if let Some(child) = map.get("unevaluatedProperties") {
                reject_secrets(child, value)?;
            }
            let declared = map.get("properties").and_then(|p| p.get(name));
            if let Some(child) = declared {
                reject_secrets(child, value)?;
            }
            let mut matched = false;
            if let Some(patterns) = map.get("patternProperties").and_then(Value::as_object) {
                for (pattern, child) in patterns {
                    let matcher = jsonschema::options()
                        .offline()
                        .with_pattern_options(jsonschema::PatternOptions::regex())
                        .build(&json!({"pattern":pattern}))
                        .map_err(|_| HostError::new("EXTENSION_CONFIG_SCHEMA"))?;
                    if matcher.is_valid(&Value::String(name.clone())) {
                        matched = true;
                        reject_secrets(child, value)?;
                    }
                }
            }
            if declared.is_none() && !matched {
                if let Some(child) = map.get("additionalProperties") {
                    reject_secrets(child, value)?;
                }
            }
        }
        if let Some(dependent) = map.get("dependentSchemas").and_then(Value::as_object) {
            for (name, child) in dependent {
                if properties.contains_key(name) {
                    reject_secrets(child, instance)?;
                }
            }
        }
    }
    if let Some(items) = instance.as_array() {
        let prefix = map.get("prefixItems").and_then(Value::as_array);
        for (index, item) in items.iter().enumerate() {
            if let Some(child) = map.get("unevaluatedItems") {
                reject_secrets(child, item)?;
            }
            if let Some(child) = prefix
                .and_then(|p| p.get(index))
                .or_else(|| map.get("items"))
            {
                reject_secrets(child, item)?;
            }
            if let Some(child) = map.get("contains") {
                reject_secrets(child, item)?;
            }
        }
    }
    Ok(())
}

/// Schema is read from the verified manifest, never from the proposed configuration.
pub fn validate(manifest: &Value, configuration: &Value) -> Result<()> {
    if !configuration.is_object() {
        return Err(HostError::new("EXTENSION_CONFIG_INVALID"));
    }
    walk(configuration, 0, &mut 0, false)?;
    let schema = manifest
        .get("configuration_schema")
        .cloned()
        .unwrap_or_else(|| json!({"type":"object","additionalProperties":false}));
    walk(&schema, 0, &mut 0, true)?;
    if schema
        .get("$schema")
        .is_some_and(|v| v.as_str() != Some("https://json-schema.org/draft/2020-12/schema"))
    {
        return Err(HostError::new("EXTENSION_CONFIG_SCHEMA"));
    }
    for value in [&schema, configuration] {
        if serde_json::to_vec(value)
            .map_err(|_| HostError::new("EXTENSION_CONFIG_INVALID"))?
            .len()
            > 64 * 1024
        {
            return Err(HostError::new("EXTENSION_CONFIG_LIMIT"));
        }
    }
    let validator = jsonschema::options()
        .offline()
        .with_draft(jsonschema::Draft::Draft202012)
        .with_pattern_options(jsonschema::PatternOptions::regex())
        .should_validate_formats(true)
        .should_ignore_unknown_formats(false)
        .build(&schema)
        .map_err(|_| HostError::new("EXTENSION_CONFIG_SCHEMA"))?;
    if !validator.is_valid(configuration) {
        return Err(HostError::new("EXTENSION_CONFIG_INVALID"));
    }
    reject_secrets(&schema, configuration)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn signed_schema_checks_nested_types_bounds_and_unknown_properties() {
        let manifest = json!({"configuration_schema":{"type":"object","required":["count"],"additionalProperties":false,
            "properties":{"count":{"type":"integer","minimum":1,"maximum":5},"labels":{"type":"array","maxItems":2,"items":{"type":"string","pattern":"^[a-z]+$"}}}}});
        validate(&manifest, &json!({"count":2,"labels":["valid"]})).unwrap();
        for config in [
            json!({}),
            json!({"count":0}),
            json!({"count":"2"}),
            json!({"count":2,"extra":true}),
            json!({"count":2,"labels":["INVALID"]}),
        ] {
            assert!(validate(&manifest, &config).is_err());
        }
        validate(&json!({}), &json!({})).unwrap();
        assert!(validate(&json!({}), &json!({"undeclared":true})).is_err());
    }
    #[test]
    fn secrets_references_and_excessive_work_are_refused_without_values_in_errors() {
        let secret = "do-not-print-this-value";
        for field in ["password", "TOKEN", "api_key", "client_secret"] {
            let err = validate(
                &json!({"configuration_schema":true}),
                &json!({"nested":[{field:secret}]}),
            )
            .unwrap_err();
            assert_eq!(err.code, "EXTENSION_CONFIG_SECRET");
            assert!(!format!("{err:?}").contains(secret));
        }
        for mark in ["writeOnly", "x-opennexus-secret"] {
            let schema = json!({"configuration_schema":{"properties":{"custom":{"type":"string",mark:true}}}});
            assert!(validate(&schema, &json!({"custom":secret})).is_err());
            validate(&schema, &json!({})).unwrap();
        }
        assert!(validate(&json!({"configuration_schema":{"anyOf":[{"properties":{"custom":{"writeOnly":true}}},true]}}),&json!({"custom":secret})).is_err());
        for reference in ["file:///etc/passwd", "https://example.com/schema", "#"] {
            assert!(validate(
                &json!({"configuration_schema":{"$ref":reference}}),
                &json!({})
            )
            .is_err());
        }
        assert!(validate(&json!({"configuration_schema":{"type":42}}), &json!({})).is_err());
        let mut deep = json!(true);
        for _ in 0..25 {
            deep = json!({"nested":deep});
        }
        assert!(validate(&json!({"configuration_schema":true}), &deep).is_err());
        assert!(validate(
            &json!({"configuration_schema":true}),
            &json!({"value":"a".repeat(65536)})
        )
        .is_err());
    }
}
