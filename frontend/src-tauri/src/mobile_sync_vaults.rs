//! Read-only remote vault metadata through the selected native auth session.
//! No endpoint, HTTP method, credentials or arbitrary provider JSON is exposed.
use crate::{
    mobile_sync_auth::{Auth, SecretStore},
    mobile_sync_foreground::Lease,
    mobile_sync_session::{Scope, Selection},
    sync_client::{identifier, SyncError},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

type Result<T> = std::result::Result<T, SyncError>;
const MAX_VAULTS: usize = 1000;
const MAX_SAFE_INTEGER: i64 = 9_007_199_254_740_991;

#[derive(Deserialize, Serialize)]
pub struct Vault {
    pub id: String,
    pub name: String,
    pub sequence: i64,
    pub used: i64,
    pub quota: i64,
}
#[derive(Serialize)]
pub struct Page {
    pub selection: Selection,
    pub items: Vec<Vault>,
    pub total: usize,
    pub offset: usize,
    pub limit: usize,
}

fn page(value: Value, selection: Selection, offset: usize, limit: usize) -> Result<Page> {
    let rows = value["items"]
        .as_array()
        .filter(|rows| rows.len() <= MAX_VAULTS)
        .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
    let mut identifiers = HashSet::new();
    let mut vaults = Vec::with_capacity(rows.len());
    for row in rows {
        // Deserialize only this whitelist. Unknown provider fields never enter
        // an IPC response, diagnostics or persistent WebView storage.
        let vault: Vault = serde_json::from_value(row.clone())
            .map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        if identifier(&vault.id).is_err()
            || !identifiers.insert(vault.id.clone())
            || vault.name.is_empty()
            || vault.name.chars().count() > 100
            || [vault.sequence, vault.used, vault.quota]
                .iter()
                .any(|number| !(0..=MAX_SAFE_INTEGER).contains(number))
        {
            return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
        }
        vaults.push(vault);
    }
    // The v1 service returns an unpaged list without a specified row order.
    // Stable native pages do not depend on provider/database insertion order.
    vaults.sort_by(|a, b| a.id.cmp(&b.id));
    let total = vaults.len();
    Ok(Page {
        selection,
        items: vaults.into_iter().skip(offset).take(limit).collect(),
        total,
        offset,
        limit,
    })
}

pub async fn list<S: SecretStore>(
    auth: &mut Auth<S>,
    lease: &Lease,
    expected: Selection,
    offset: usize,
    limit: usize,
    now: u64,
) -> Result<Page> {
    if offset > MAX_VAULTS || !(1..=50).contains(&limit) {
        return Err(SyncError::new("SYNC_VAULT_PAGE_INVALID"));
    }
    // Compare the complete frozen native selection, including its revision.
    // Switching away and back cannot authorize a stale discovery request.
    if auth.selection(lease)?.as_ref() != Some(&expected) {
        return Err(SyncError::new("SYNC_AUTH_SELECTION_CHANGED"));
    }
    let scope = Scope::new(&expected.endpoint, &expected.account)?;
    let operation = expected.operation_id.clone();
    auth.authorized(lease, &scope, &operation, now, move |client| async move {
        let value = client
            .json(reqwest::Method::GET, "sync/v1/vaults", None)
            .await?;
        page(value, expected, offset, limit)
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use zeroize::Zeroizing;

    fn selection() -> Selection {
        Selection {
            revision: 1,
            endpoint: "https://sync.example/".into(),
            account: "reader-a".into(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            generation: 1,
        }
    }
    fn row(id: &str) -> Value {
        json!({"id":id,"name":"原名称 😀 <script>","sequence":3,"used":12,"quota":100})
    }
    #[test]
    fn whitelist_preserves_original_names_and_stably_pages_without_provider_private_fields() {
        let mut first = row("remote-b");
        first["private_provider_value"] = json!("not-for-ipc");
        let result = page(json!({"items":[first,row("remote-a")]}), selection(), 1, 1).unwrap();
        assert_eq!(result.total, 2);
        assert_eq!(result.items[0].id, "remote-b");
        assert_eq!(result.items[0].name, "原名称 😀 <script>");
        let encoded = serde_json::to_string(&result).unwrap();
        assert!(!encoded.contains("private_provider_value"));
        assert!(!encoded.contains("not-for-ipc"));
        assert_eq!(result.offset, 1);
        assert_eq!(result.limit, 1);
    }
    #[test]
    fn empty_list_is_distinct_from_malformed_duplicate_or_excessive_provider_rows() {
        assert_eq!(
            page(json!({"items":[]}), selection(), 0, 20).unwrap().total,
            0
        );
        for value in [
            json!({}),
            json!({"items":null}),
            json!({"items":[row("remote-a"),row("remote-a")]}),
            json!({"items":vec![row("remote-a");MAX_VAULTS+1]}),
        ] {
            assert_eq!(
                page(value, selection(), 0, 20).err().unwrap().code,
                "SYNC_RESPONSE_INVALID"
            );
        }
    }
    #[test]
    fn unsafe_identifiers_names_and_numbers_do_not_cross_the_native_boundary() {
        for (field, value) in [
            ("id", json!("../foreign")),
            ("name", json!("")),
            ("name", json!("字".repeat(101))),
            ("sequence", json!(-1)),
            ("used", json!(MAX_SAFE_INTEGER + 1)),
            ("quota", json!(1.5)),
            ("used", json!("12")),
        ] {
            let mut invalid = row("remote-a");
            invalid[field] = value;
            assert_eq!(
                page(json!({"items":[invalid]}), selection(), 0, 20)
                    .err()
                    .unwrap()
                    .code,
                "SYNC_RESPONSE_INVALID"
            );
        }
    }
    struct NoSecrets;
    impl SecretStore for NoSecrets {
        fn read(&self, _: &Scope, _: &str) -> crate::workspace::Result<Option<Zeroizing<Vec<u8>>>> {
            panic!("Stale or unbounded discovery decrypted a session")
        }
        fn put(&self, _: &Scope, _: &str, _: Zeroizing<Vec<u8>>) -> crate::workspace::Result<()> {
            panic!("Read-only discovery stored a session")
        }
        fn remove(&self, _: &Scope, _: &str) -> crate::workspace::Result<()> {
            panic!("Read-only discovery erased a session")
        }
    }
    #[tokio::test]
    async fn invalid_pages_and_stale_selection_are_refused_before_decryption_or_network() {
        let root = tempfile::tempdir().unwrap();
        let mut auth = Auth::open(root.path(), NoSecrets).unwrap();
        let foreground = crate::mobile_sync_foreground::Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        for (offset, limit) in [(1001, 20), (0, 0), (0, 51)] {
            assert_eq!(
                list(&mut auth, &lease, selection(), offset, limit, 100)
                    .await
                    .err()
                    .unwrap()
                    .code,
                "SYNC_VAULT_PAGE_INVALID"
            );
        }
        assert_eq!(
            list(&mut auth, &lease, selection(), 0, 20, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        foreground.on_stop();
        assert_eq!(
            list(&mut auth, &lease, selection(), 0, 20, 100)
                .await
                .err()
                .unwrap()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
    }
}
