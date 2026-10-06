//! Typed account metadata contains no access or refresh tokens.
use crate::sync_client::{identifier, SyncClient, SyncError};
use reqwest::Method;
use serde::Serialize;
use serde_json::Value;
use std::collections::HashSet;
#[derive(Debug, Serialize)]
pub struct Device {
    pub id: String,
    pub name: String,
    pub revoked: bool,
}
#[derive(Debug, Serialize)]
pub struct Usage {
    pub id: String,
    pub name: String,
    pub sequence: u64,
    pub used: u64,
    pub quota: u64,
}
#[derive(Debug, Serialize)]
pub struct Details {
    pub vault: Usage,
    pub devices: Vec<Device>,
}
fn invalid() -> SyncError {
    SyncError::new("SYNC_RESPONSE_INVALID")
}
fn name(value: &Value) -> Result<String, SyncError> {
    value
        .as_str()
        .filter(|value| value.chars().count() <= 100)
        .map(str::to_owned)
        .ok_or_else(invalid)
}
fn parse(vault_id: &str, vaults: &Value, devices: &Value) -> Result<Details, SyncError> {
    identifier(vault_id)?;
    let items = vaults["items"]
        .as_array()
        .filter(|items| items.len() <= 10000)
        .ok_or_else(invalid)?;
    let mut matching = items
        .iter()
        .filter(|item| item["id"].as_str() == Some(vault_id));
    let item = matching
        .next()
        .ok_or_else(|| SyncError::new("SYNC_VAULT_NOT_FOUND"))?;
    if matching.next().is_some() {
        return Err(invalid());
    }
    let vault = Usage {
        id: vault_id.into(),
        name: name(&item["name"])?,
        sequence: item["sequence"].as_u64().ok_or_else(invalid)?,
        used: item["used"].as_u64().ok_or_else(invalid)?,
        quota: item["quota"].as_u64().ok_or_else(invalid)?,
    };
    let items = devices["items"]
        .as_array()
        .filter(|items| items.len() <= 1000)
        .ok_or_else(invalid)?;
    let mut seen = HashSet::new();
    let mut devices = Vec::new();
    for item in items {
        let id = item["id"].as_str().ok_or_else(invalid)?;
        identifier(id)?;
        if !seen.insert(id) {
            return Err(invalid());
        }
        let revoked = match &item["revoked"] {
            Value::Bool(value) => *value,
            value if value.as_i64() == Some(0) => false,
            value if value.as_i64() == Some(1) => true,
            _ => return Err(invalid()),
        };
        devices.push(Device {
            id: id.into(),
            name: name(&item["name"])?,
            revoked,
        });
    }
    Ok(Details { vault, devices })
}
impl SyncClient {
    pub async fn account_details(&self, vault_id: &str) -> Result<Details, SyncError> {
        identifier(vault_id)?;
        let vaults = self.json(Method::GET, "sync/v1/vaults", None).await?;
        let devices = self.json(Method::GET, "sync/v1/devices", None).await?;
        parse(vault_id, &vaults, &devices)
    }
    pub async fn revoke_device(&self, device_id: &str) -> Result<(), SyncError> {
        identifier(device_id)?;
        self.json(
            Method::DELETE,
            &format!("sync/v1/devices/{device_id}"),
            None,
        )
        .await?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn metadata_is_bounded_strict_and_reports_over_quota_without_inventing_free_space() {
        let vaults = json!({"items":[{"id":"vault","name":"课程库","sequence":7,"used":12,"quota":10,"private":"ignored"}]});
        let devices = json!({"items":[{"id":"one","name":"设备一","revoked":0},{"id":"two","name":"Device two","revoked":true}]});
        let result = parse("vault", &vaults, &devices).unwrap();
        assert_eq!(result.vault.used, 12);
        assert_eq!(result.vault.quota, 10);
        assert!(!result.devices[0].revoked && result.devices[1].revoked);
        assert!(!serde_json::to_string(&result).unwrap().contains("private"));
        assert!(parse("other", &vaults, &devices).is_err());
        let duplicate = json!({"items":[{"id":"one","name":"a","revoked":0},{"id":"one","name":"b","revoked":0}]});
        assert!(parse("vault", &vaults, &duplicate).is_err());
        for revoked in [json!(2), json!("false"), Value::Null] {
            assert!(parse(
                "vault",
                &vaults,
                &json!({"items":[{"id":"one","name":"a","revoked":revoked}]})
            )
            .is_err());
        }
        let mut bad = vaults.clone();
        bad["items"][0]["used"] = json!(-1);
        assert!(parse("vault", &bad, &devices).is_err());
    }
}
