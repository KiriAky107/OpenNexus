//! Read-only catalog transport; the renderer keeps an IPC-only network policy.
use crate::{
    extension_trust::Client,
    workspace::{HostError, Result},
};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub source: String,
    pub path: String,
    pub max_bytes: usize,
    pub if_none_match: Option<String>,
}
#[derive(Serialize)]
pub struct Reply {
    pub status: u16,
    pub body_base64: Option<String>,
    pub etag: Option<String>,
}
fn invalid() -> HostError {
    HostError::new("COMMUNITY_REQUEST_INVALID")
}
fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
        && !matches!(value, "." | "..")
}
fn validate(request: &Request) -> Result<Client> {
    if request.path.len() > 4096 || !(1..=4 * 1024 * 1024).contains(&request.max_bytes) {
        return Err(invalid());
    }
    let source = reqwest::Url::parse(&request.source).map_err(|_| invalid())?;
    let client = Client::new(&request.source)?;
    let url = source.join(&request.path).map_err(|_| invalid())?;
    if url.origin() != source.origin()
        || url.fragment().is_some()
        || !request.path.starts_with("/catalog/v1/")
    {
        return Err(invalid());
    }
    let parts: Vec<_> = url.path().split('/').collect();
    let fields: &[&str] = match parts.as_slice() {
        ["", "catalog", "v1", "sources"] => &[],
        ["", "catalog", "v1", "packages"] => &["q", "type", "offset", "limit"],
        ["", "catalog", "v1", "packages", namespace, package, "releases"]
            if identifier(namespace) && identifier(package) =>
        {
            &["version", "offset", "limit"]
        }
        ["", "catalog", "v1", "releases", release] if identifier(release) => &[],
        _ => return Err(invalid()),
    };
    let mut seen = std::collections::BTreeSet::new();
    for (name, value) in url.query_pairs() {
        if !fields.contains(&name.as_ref()) || !seen.insert(name.into_owned()) || value.len() > 1024
        {
            return Err(invalid());
        }
    }
    if let Some(etag) = &request.if_none_match {
        if etag.len() > 1024 || reqwest::header::HeaderValue::from_str(etag).is_err() {
            return Err(invalid());
        }
    }
    Ok(client)
}
pub async fn fetch(request: &Request) -> Result<Reply> {
    validate(request)?
        .catalog_request(
            &request.path,
            request.max_bytes,
            request.if_none_match.as_deref(),
        )
        .await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_public_bounded_catalog_gets_are_allowed() {
        for (path, accepted) in [
            ("/catalog/v1/sources", true),
            (
                "/catalog/v1/packages?q=%E4%B8%AD%23%25&offset=30&limit=30",
                true,
            ),
            (
                "/catalog/v1/packages/examples/course/releases?version=1.0.0%2Bbuild.1&limit=100",
                true,
            ),
            ("/catalog/v1/releases/native-0001", true),
            ("//other.example/catalog/v1/sources", false),
            ("/catalog/v1/../../admin", false),
            ("/catalog/v1/%2e%2e/admin", false),
            ("/catalog/v1/packages/a%2fb/c/releases", false),
            ("/catalog/v1/publish/submissions", false),
            ("/catalog/v1/moderation/reviews", false),
            ("/catalog/v1/releases/native-0001/download", false),
            ("/catalog/v1/sources#fragment", false),
            ("/catalog/v1/packages?token=value", false),
            ("/catalog/v1/packages?limit=30&limit=100", false),
        ] {
            let request = Request {
                source: "https://catalog.example/".into(),
                path: path.into(),
                max_bytes: 1024,
                if_none_match: None,
            };
            assert_eq!(validate(&request).is_ok(), accepted, "{path}");
        }
        for (source, max_bytes, etag) in [
            ("http://127.0.0.1/", 1024, None),
            ("https://user:password@catalog.example/", 1024, None),
            ("https://catalog.example/?token=value", 1024, None),
            ("https://catalog.example/", 0, None),
            ("https://catalog.example/", 4 * 1024 * 1024 + 1, None),
            (
                "https://catalog.example/",
                1024,
                Some("\"tag\"\r\nAuthorization: injected".into()),
            ),
        ] {
            assert!(validate(&Request {
                source: source.into(),
                path: "/catalog/v1/sources".into(),
                max_bytes,
                if_none_match: etag
            })
            .is_err());
        }
        assert!(serde_json::from_value::<Request>(serde_json::json!({"source":"https://catalog.example/","path":"/catalog/v1/sources","max_bytes":1024,"if_none_match":null,"authorization":"injected"})).is_err());
    }
}
