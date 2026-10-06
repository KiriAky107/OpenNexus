//! Configuration shapes shared with publisher/Core; no target activation.
use serde_json::Value;

fn secret(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    [
        "api_key",
        "api-key",
        "apikey",
        "token",
        "password",
        "secret",
        "authorization",
        "cookie",
    ]
    .iter()
    .any(|word| key.contains(word))
}
fn strings(value: &Value, limit: usize, unique: bool) -> bool {
    value.as_array().is_some_and(|items| {
        items.len() <= limit
            && items.iter().all(Value::is_string)
            && (!unique
                || items
                    .iter()
                    .map(|v| v.as_str().unwrap())
                    .collect::<std::collections::BTreeSet<_>>()
                    .len()
                    == items.len())
    })
}
pub fn valid(kind: &str, value: &Value) -> bool {
    let Some(map) = value.as_object() else {
        return false;
    };
    if map
        .get("schema_version")
        .is_some_and(|v| v.as_u64() != Some(1))
        || map
            .get("permissions")
            .is_some_and(|v| !strings(v, 64, true))
    {
        return false;
    }
    if kind == "mcp" {
        let transport = value["transport"].as_str();
        return matches!(transport, Some("stdio" | "streamable_http" | "sse"))
            && (transport != Some("stdio") || map.contains_key("args"))
            && map
                .get("args")
                .is_none_or(|v| strings(v, usize::MAX, false))
            && map
                .get("command")
                .is_none_or(|v| v.as_str().is_some_and(|s| !s.trim().is_empty()))
            && map.get("name").is_none_or(|v| {
                v.as_str()
                    .is_some_and(|s| (1..=80).contains(&s.trim().chars().count()))
            })
            && ["enabled", "approved_digest", "tested_digest", "version"]
                .iter()
                .all(|key| !map.contains_key(*key))
            && ["environment", "headers"].iter().all(|key| {
                map.get(*key).is_none_or(|v| {
                    v.as_object().is_some_and(|fields| {
                        fields.iter().all(|(key, v)| !secret(key) && v.is_string())
                    })
                })
            })
            && ["secret_environment_keys", "secret_header_keys"]
                .iter()
                .all(|key| map.get(*key).is_none_or(|v| strings(v, 64, true)));
    }
    if kind != "model"
        || !["source", "revision", "license"]
            .iter()
            .all(|key| value[*key].as_str().is_some_and(|s| !s.trim().is_empty()))
        || !value["resources"]
            .as_object()
            .is_some_and(|map| !map.is_empty())
        || !value["verified_platforms"].as_array().is_some_and(|items| {
            !items.is_empty()
                && items
                    .iter()
                    .all(|v| v.as_str().is_some_and(|s| !s.trim().is_empty()))
        })
    {
        return false;
    }
    if !map.contains_key("model_key") && !map.contains_key("runtime_config") {
        return true;
    }
    let Some(key) = value["model_key"].as_str() else {
        return false;
    };
    let Some(runtime) = value["runtime_config"].as_object() else {
        return false;
    };
    if key.is_empty()
        || key.len() > 64
        || !key.as_bytes()[0].is_ascii_lowercase() && !key.as_bytes()[0].is_ascii_digit()
        || !key
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
        || runtime.get("embedding_model").and_then(Value::as_str) != Some(key)
    {
        return false;
    }
    runtime.iter().all(|(name, value)| match name.as_str() {
        "embedding_model" => true,
        "device" => matches!(value.as_str(), Some("cpu" | "cuda")),
        "cpu_threads" => value.as_u64().is_some_and(|v| (1..=32).contains(&v)),
        "memory_limit_mb" => value.as_u64().is_some_and(|v| (1024..=131072).contains(&v)),
        "gpu_memory_limit_mb" => value.as_u64().is_some_and(|v| (512..=65536).contains(&v)),
        "timeout_seconds" => value.as_u64().is_some_and(|v| (30..=14400).contains(&v)),
        _ => false,
    })
}
