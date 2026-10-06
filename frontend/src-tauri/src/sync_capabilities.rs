//! Validate the wire format before sending raw vault content.
use crate::workspace::{HostError, Result};
use serde::Serialize;
use serde_json::Value;

#[derive(Clone, Debug, Serialize)]
pub struct Capabilities {
    pub protocol: u32,
    pub chunk_size: u64,
    pub max_object_size: u64,
    pub encryption: &'static str,
    pub transport_security: &'static str,
    pub features: Option<Features>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Features {
    pub version: u32,
    pub objects: &'static str,
    pub revisions: &'static str,
    pub paths: &'static str,
    pub uploads: &'static str,
    pub execution: bool,
}
pub fn parse(value: &Value, tls: bool) -> Result<Capabilities> {
    let incompatible = || HostError::new("PROTOCOL_INCOMPATIBLE");
    if value["protocol"].as_u64() != Some(1)
        || value["chunk_size"].as_u64() != Some(1048576)
        || value["max_object_size"].as_u64() != Some(104857600)
    {
        return Err(incompatible());
    }
    // A v1 raw-content client cannot safely send plaintext to an E2EE contract.
    if value["encryption"].as_str() != Some("transport-only") {
        return Err(HostError::new("SYNC_ENCRYPTION_INCOMPATIBLE"));
    }
    let features = if let Some(features) = value.get("features") {
        if features["version"].as_u64() != Some(1)
            || features["objects"].as_str() != Some("opaque-bytes-sha256")
            || features["revisions"].as_str() != Some("stable-file-id-cas")
            || features["paths"].as_str() != Some("portable-nfc-casefold")
            || features["uploads"].as_str() != Some("confirmed-offset")
            || features["execution"].as_bool() != Some(false)
        {
            return Err(incompatible());
        }
        Some(Features {
            version: 1,
            objects: "opaque-bytes-sha256",
            revisions: "stable-file-id-cas",
            paths: "portable-nfc-casefold",
            uploads: "confirmed-offset",
            execution: false,
        })
    } else {
        // Existing v1 services already declare encryption and the size limits.
        None
    };
    Ok(Capabilities {
        protocol: 1,
        chunk_size: 1048576,
        max_object_size: 104857600,
        encryption: "transport-only",
        transport_security: if tls { "tls" } else { "test-http" },
        features,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn fixture() -> Value {
        serde_json::from_str(include_str!("../tests/fixtures/sync-v1-contract.json")).unwrap()
    }
    #[test]
    fn shared_contract_keeps_raw_content_and_experiment_discovery_distinct_from_execution() {
        let fixture = fixture();
        let result = parse(&fixture["handshake"], true).unwrap();
        assert_eq!(result.transport_security, "tls");
        assert!(!result.features.unwrap().execution);
        for case in fixture["paths"].as_array().unwrap() {
            assert_eq!(
                crate::sync_discovery::allowed(case["path"].as_str().unwrap()),
                case["desktop_sync"].as_bool().unwrap(),
                "{}",
                case["path"]
            );
        }
        let mut legacy = fixture["handshake"].clone();
        legacy.as_object_mut().unwrap().remove("features");
        let result = parse(&legacy, false).unwrap();
        assert_eq!(result.transport_security, "test-http");
        assert!(result.features.is_none());
    }
    #[test]
    fn missing_unknown_or_misleading_encryption_and_capabilities_are_rejected() {
        for encryption in [Value::Null, json!("e2ee"), json!("tls"), json!(true)] {
            let mut value = fixture()["handshake"].clone();
            value["encryption"] = encryption;
            assert_eq!(
                parse(&value, true).unwrap_err().code,
                "SYNC_ENCRYPTION_INCOMPATIBLE"
            );
        }
        for (key, bad) in [
            ("execution", json!(true)),
            ("version", json!(2)),
            ("uploads", json!("restart-from-zero")),
        ] {
            let mut value = fixture()["handshake"].clone();
            value["features"][key] = bad;
            assert_eq!(
                parse(&value, true).unwrap_err().code,
                "PROTOCOL_INCOMPATIBLE"
            );
        }
        let mut value = fixture()["handshake"].clone();
        value["protocol"] = json!(1.0);
        assert!(parse(&value, true).is_err());
    }
}
