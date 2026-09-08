//! Fresh Community state checked against Host-pinned keys; never TOFU on refresh.
use crate::{
    extension_package::Release,
    workspace::{hash, HostError, Result},
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Deserialize;
use serde_json::Value;
use std::time::{Duration, Instant};

pub struct Client {
    source: reqwest::Url,
    http: reqwest::Client,
}
pub struct Pin<'a> {
    pub source_id: &'a str,
    pub key_id: &'a str,
    pub namespace: &'a str,
    pub public_key: &'a [u8; 32],
}
pub struct Checked {
    source: String,
    release_hash: String,
    key_hash: String,
    checked_at: Instant,
}
impl Checked {
    /// Monotonic freshness avoids a wall-clock rollback extending validity.
    pub fn matches(&self, source: &str, release: &Release, key: &[u8; 32]) -> Result<()> {
        if self.checked_at.elapsed() > Duration::from_secs(30) {
            return Err(HostError::new("EXTENSION_TRUST_STALE"));
        }
        let url =
            reqwest::Url::parse(source).map_err(|_| HostError::new("EXTENSION_SOURCE_INVALID"))?;
        if url.as_str() != self.source
            || hash(&serde_json::to_vec(release).unwrap()) != self.release_hash
            || hash(key) != self.key_hash
        {
            return Err(HostError::new("EXTENSION_TRUST_CHANGED"));
        }
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Key {
    key_id: String,
    namespace: String,
    public_key: String,
    revoked: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Source {
    schema_version: u32,
    source_id: String,
    keys: Vec<Key>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Releases {
    items: Vec<Value>,
}
fn unavailable() -> HostError {
    HostError::new("EXTENSION_TRUST_UNAVAILABLE")
}
impl Client {
    pub fn new(source: &str) -> Result<Self> {
        let mut url =
            reqwest::Url::parse(source).map_err(|_| HostError::new("EXTENSION_SOURCE_INVALID"))?;
        if url.scheme() != "https"
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || source.len() > 2048
        {
            return Err(HostError::new("EXTENSION_SOURCE_INVALID"));
        }
        let path = format!("{}/", url.path().trim_end_matches('/'));
        url.set_path(&path);
        let http = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(15))
            .build()
            .map_err(|_| unavailable())?;
        Ok(Self { source: url, http })
    }
    async fn json(&self, path: &str) -> Result<Value> {
        let url = self.source.join(path).map_err(|_| unavailable())?;
        let mut response = self
            .http
            .get(url)
            .header("Cache-Control", "no-cache, no-store")
            .send()
            .await
            .map_err(|_| unavailable())?;
        if response.status() != reqwest::StatusCode::OK
            || response
                .content_length()
                .is_some_and(|n| n > 4 * 1024 * 1024)
        {
            return Err(unavailable());
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| unavailable())? {
            if bytes.len() + chunk.len() > 4 * 1024 * 1024 {
                return Err(unavailable());
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&bytes).map_err(|_| unavailable())
    }
    pub async fn check(&self, pin: Pin<'_>, release: &Release) -> Result<Checked> {
        release.validate()?;
        let started = Instant::now();
        let source: Source = serde_json::from_value(self.json("catalog/v1/sources").await?)
            .map_err(|_| unavailable())?;
        if source.schema_version != 1
            || source.source_id != pin.source_id
            || source.keys.len() > 4096
        {
            return Err(HostError::new("EXTENSION_TRUST_CHANGED"));
        }
        let matching: Vec<_> = source
            .keys
            .iter()
            .filter(|k| k.key_id == pin.key_id)
            .collect();
        if matching.len() != 1 {
            return Err(HostError::new("EXTENSION_TRUST_CHANGED"));
        }
        let key = matching[0];
        if key.revoked {
            return Err(HostError::new("EXTENSION_REVOKED"));
        }
        if key.namespace != pin.namespace
            || release.namespace != pin.namespace
            || release.key_id != pin.key_id
            || STANDARD.decode(&key.public_key).ok().as_deref() != Some(pin.public_key.as_slice())
        {
            return Err(HostError::new("EXTENSION_TRUST_CHANGED"));
        }
        let list: Releases = serde_json::from_value(
            self.json(&format!(
                "catalog/v1/packages/{}/{}/releases",
                release.namespace, release.package_id
            ))
            .await?,
        )
        .map_err(|_| unavailable())?;
        if list.items.len() > 4096 {
            return Err(unavailable());
        }
        let mut found = Vec::new();
        for mut item in list.items {
            if item.get("version").and_then(Value::as_str) != Some(release.version.as_str()) {
                continue;
            }
            let map = item.as_object_mut().ok_or_else(unavailable)?;
            let withdrawn = map
                .remove("withdrawn")
                .and_then(|v| v.as_bool())
                .ok_or_else(unavailable)?;
            if withdrawn {
                return Err(HostError::new("EXTENSION_REVOKED"));
            }
            for field in ["release_id", "download_path"] {
                if map
                    .remove(field)
                    .and_then(|v| v.as_str().map(str::to_owned))
                    .is_none()
                {
                    return Err(unavailable());
                }
            }
            let remote: Release = serde_json::from_value(item).map_err(|_| unavailable())?;
            found.push(hash(&serde_json::to_vec(&remote).unwrap()));
        }
        let release_hash = hash(&serde_json::to_vec(release).unwrap());
        if found != [release_hash.clone()] {
            return Err(HostError::new("EXTENSION_TRUST_CHANGED"));
        }
        let checked = Checked {
            source: self.source.to_string(),
            release_hash,
            key_hash: hash(pin.public_key),
            checked_at: started,
        };
        checked.matches(self.source.as_str(), release, pin.public_key)?;
        Ok(checked)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{Read, Write},
        net::TcpListener,
    };
    #[tokio::test]
    async fn live_responses_require_pinned_key_exact_release_and_no_revocation() {
        let mut fixture: Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        let public: [u8; 32] = STANDARD
            .decode(fixture["key"]["public_key"].as_str().unwrap())
            .unwrap()
            .try_into()
            .unwrap();
        let display = fixture["release"].clone();
        for field in ["release_id", "withdrawn", "download_path"] {
            fixture["release"].as_object_mut().unwrap().remove(field);
        }
        let release: Release = serde_json::from_value(fixture["release"].clone()).unwrap();
        for case in [
            "ok",
            "revoked",
            "rotated",
            "withdrawn",
            "changed",
            "duplicate",
            "offline",
            "redirect",
        ] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let url = format!("http://{}/", listener.local_addr().unwrap());
            let mut source = json_source(&public);
            let mut remote = display.clone();
            if case == "revoked" {
                source["keys"][0]["revoked"] = true.into();
            }
            if case == "rotated" {
                source["keys"][0]["public_key"] = STANDARD.encode([9; 32]).into();
            }
            if case == "withdrawn" {
                remote["withdrawn"] = true.into();
            }
            if case == "changed" {
                remote["description"] = "changed".into();
            }
            let list = if case == "duplicate" {
                serde_json::json!({"items":[remote.clone(),remote]})
            } else {
                serde_json::json!({"items":[remote]})
            };
            let responses = if matches!(case, "revoked" | "rotated" | "offline" | "redirect") {
                vec![source]
            } else {
                vec![source, list]
            };
            let worker = std::thread::spawn(move || {
                for body in responses {
                    let (mut stream, _) = listener.accept().unwrap();
                    let mut bytes = [0; 8192];
                    stream.read(&mut bytes).unwrap();
                    let body = body.to_string();
                    let status = match case {
                        "offline" => "503 Unavailable",
                        "redirect" => "302 Found",
                        _ => "200 OK",
                    };
                    write!(stream,"HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len()).unwrap();
                }
            });
            let mut client = Client::new("https://catalog.example/").unwrap();
            client.source = reqwest::Url::parse(&url).unwrap();
            let result = client
                .check(
                    Pin {
                        source_id: "fixture",
                        key_id: "test-key",
                        namespace: "examples",
                        public_key: &public,
                    },
                    &release,
                )
                .await;
            assert_eq!(result.is_ok(), case == "ok", "{case}");
            if let Ok(mut checked) = result {
                checked.matches(&url, &release, &public).unwrap();
                checked.checked_at = Instant::now() - Duration::from_secs(31);
                assert!(checked.matches(&url, &release, &public).is_err());
            }
            worker.join().unwrap();
        }
        assert!(Client::new("http://catalog.example").is_err());
    }
    fn json_source(public: &[u8; 32]) -> Value {
        serde_json::json!({"schema_version":1,"source_id":"fixture","keys":[{"key_id":"test-key","namespace":"examples","public_key":STANDARD.encode(public),"revoked":false}]})
    }
}
