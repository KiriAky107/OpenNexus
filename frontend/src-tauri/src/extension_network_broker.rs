//! 由 Host 执行的扩展 HTTPS 代理。沙箱进程本身始终不获得网络能力。
use crate::{
    extension_permit::{Claims, Lease},
    workspace::{HostError, Result},
};
use reqwest::{blocking::Client, redirect::Policy, Method, Url};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Read,
    net::{IpAddr, SocketAddr, ToSocketAddrs},
    time::{Duration, Instant},
};

const PERMISSION_PREFIX: &str = "network.https:";
const ADDRESS_PREFIX: &str = "network.https-address:";
const MAX_REQUEST_BYTES: usize = 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const MAX_CALLS_PER_MINUTE: usize = 60;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FetchRequest {
    pub url: String,
    #[serde(default = "default_method")]
    pub method: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub content_type: Option<String>,
}

fn default_method() -> String {
    "GET".into()
}

#[derive(Serialize)]
pub struct FetchResponse {
    pub status: u16,
    pub body: String,
    pub content_type: Option<String>,
}

pub struct Broker {
    lease: Lease,
    origins: BTreeSet<String>,
    addresses: BTreeMap<String, Vec<IpAddr>>,
    calls: Vec<Instant>,
}

impl Broker {
    pub fn new(lease: Lease, claims: &Claims) -> Result<Self> {
        let mut origins = BTreeSet::new();
        let mut addresses: BTreeMap<String, Vec<IpAddr>> = BTreeMap::new();
        for permission in &claims.permissions {
            if let Some(value) = permission.strip_prefix(PERMISSION_PREFIX) {
                origins.insert(canonical_origin(value)?);
            } else if let Some(value) = permission.strip_prefix(ADDRESS_PREFIX) {
                let (host, address) = value
                    .split_once('=')
                    .ok_or_else(|| HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"))?;
                let normalized = normalized_host(host)?;
                let address: IpAddr = address
                    .parse()
                    .map_err(|_| HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"))?;
                if prohibited(address) {
                    return Err(HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"));
                }
                addresses.entry(normalized).or_default().push(address);
            }
        }
        if addresses
            .keys()
            .any(|host| !origins.iter().any(|allowed| origin_host(allowed) == host))
        {
            return Err(HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"));
        }
        Ok(Self {
            lease,
            origins,
            addresses,
            calls: Vec::new(),
        })
    }

    pub fn fetch(&mut self, request: FetchRequest) -> Result<FetchResponse> {
        self.lease.check()?;
        let now = Instant::now();
        self.calls
            .retain(|called| now.duration_since(*called) < Duration::from_secs(60));
        if self.calls.len() >= MAX_CALLS_PER_MINUTE {
            return Err(HostError::new("EXTENSION_NETWORK_RATE_LIMITED"));
        }
        self.calls.push(now);

        if request.url.len() > 4096
            || request.body.len() > MAX_REQUEST_BYTES
            || request
                .content_type
                .as_ref()
                .is_some_and(|value| value.len() > 128 || value.chars().any(char::is_control))
        {
            return Err(HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"));
        }
        let url = Url::parse(&request.url)
            .map_err(|_| HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"))?;
        validate_url(&url)?;
        if !self.origins.contains(&origin(&url)?) {
            return Err(HostError::new("EXTENSION_NETWORK_PERMISSION_DENIED"));
        }
        let host = url
            .host_str()
            .ok_or_else(|| HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"))?;
        let port = url
            .port_or_known_default()
            .ok_or_else(|| HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"))?;
        let addresses: Vec<SocketAddr> = if let Some(pinned) = self.addresses.get(host) {
            pinned
                .iter()
                .map(|address| SocketAddr::new(*address, port))
                .collect()
        } else {
            (host, port)
                .to_socket_addrs()
                .map_err(|_| HostError::new("EXTENSION_NETWORK_DNS_FAILED"))?
                .collect()
        };
        validate_addresses(&addresses)?;

        let client = Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(15))
            .resolve_to_addrs(host, &addresses)
            .build()
            .map_err(|_| HostError::new("EXTENSION_NETWORK_UNAVAILABLE"))?;
        let method = match request.method.as_str() {
            "GET" => Method::GET,
            "POST" => Method::POST,
            _ => return Err(HostError::new("EXTENSION_NETWORK_METHOD_DENIED")),
        };
        if method == Method::GET && !request.body.is_empty() {
            return Err(HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"));
        }
        let mut builder = client.request(method, url);
        if !request.body.is_empty() {
            builder = builder.body(request.body);
        }
        if let Some(content_type) = request.content_type {
            builder = builder.header(reqwest::header::CONTENT_TYPE, content_type);
        }
        let response = builder
            .send()
            .map_err(|_| HostError::new("EXTENSION_NETWORK_REQUEST_FAILED"))?;
        let status = response.status().as_u16();
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(|value| value.chars().take(128).collect());
        let mut bytes = Vec::new();
        response
            .take((MAX_RESPONSE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(|_| HostError::new("EXTENSION_NETWORK_RESPONSE_INVALID"))?;
        if bytes.len() > MAX_RESPONSE_BYTES {
            return Err(HostError::new("EXTENSION_NETWORK_RESPONSE_TOO_LARGE"));
        }
        let body = String::from_utf8(bytes)
            .map_err(|_| HostError::new("EXTENSION_NETWORK_RESPONSE_INVALID"))?;
        self.lease.check()?;
        Ok(FetchResponse {
            status,
            body,
            content_type,
        })
    }
}

fn validate_addresses(addresses: &[SocketAddr]) -> Result<()> {
    if addresses.is_empty() || addresses.iter().any(|address| prohibited(address.ip())) {
        return Err(HostError::new("EXTENSION_NETWORK_ADDRESS_DENIED"));
    }
    Ok(())
}

fn canonical_origin(value: &str) -> Result<String> {
    let url =
        Url::parse(value).map_err(|_| HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"))?;
    validate_url(&url).map_err(|_| HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"))?;
    if url.path() != "/" || url.query().is_some() || url.fragment().is_some() {
        return Err(HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"));
    }
    origin(&url)
}

fn origin(url: &Url) -> Result<String> {
    let host = url
        .host_str()
        .ok_or_else(|| HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"))?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"))?;
    Ok(format!("https://{host}:{port}"))
}

fn origin_host(origin: &str) -> &str {
    origin
        .strip_prefix("https://")
        .unwrap_or(origin)
        .rsplit_once(':')
        .map_or(origin, |(host, _)| host)
}

fn normalized_host(value: &str) -> Result<String> {
    if value.is_empty() || value.contains(['/', '@', ':', '[', ']']) {
        return Err(HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"));
    }
    let url = Url::parse(&format!("https://{value}/"))
        .map_err(|_| HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"))?;
    url.host_str()
        .filter(|host| *host == value.to_ascii_lowercase())
        .map(str::to_owned)
        .ok_or_else(|| HostError::new("EXTENSION_NETWORK_PERMISSION_INVALID"))
}

fn validate_url(url: &Url) -> Result<()> {
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(HostError::new("EXTENSION_NETWORK_REQUEST_INVALID"));
    }
    Ok(())
}

fn prohibited(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => {
            let octets = ip.octets();
            ip.is_private()
                || ip.is_loopback()
                || ip.is_link_local()
                || ip.is_unspecified()
                || ip.is_multicast()
                || octets[0] == 0
                || octets[0] >= 224
                || (octets[0] == 100 && (64..=127).contains(&octets[1]))
                || (octets[0] == 192 && octets[1] == 0 && octets[2] == 0)
                || (octets[0] == 192 && octets[1] == 0 && octets[2] == 2)
                || (octets[0] == 192 && octets[1] == 88 && octets[2] == 99)
                || (octets[0] == 198 && (octets[1] == 18 || octets[1] == 19))
                || (octets[0] == 198 && octets[1] == 51 && octets[2] == 100)
                || (octets[0] == 203 && octets[1] == 0 && octets[2] == 113)
        }
        IpAddr::V6(ip) => {
            let segments = ip.segments();
            let first = segments[0];
            ip.is_loopback()
                || ip.is_unspecified()
                || ip.is_multicast()
                || (first & 0xe000) != 0x2000
                || (first & 0xfe00) == 0xfc00
                || (first & 0xffc0) == 0xfe80
                || (segments[0] == 0x2001 && segments[1] == 0x0db8)
                || ip
                    .to_ipv4_mapped()
                    .is_some_and(|mapped| prohibited(mapped.into()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extension_permit::{Authority, ExecutionKind};
    use std::collections::BTreeMap;

    fn claims(origin: &str, address: Option<&str>) -> Claims {
        let host = Url::parse(origin).unwrap().host_str().unwrap().to_owned();
        let mut permissions = BTreeSet::from([format!("{PERMISSION_PREFIX}{origin}")]);
        if let Some(address) = address {
            permissions.insert(format!("{ADDRESS_PREFIX}{host}={address}"));
        }
        Claims {
            kind: ExecutionKind::Mcp,
            source: "https://catalog.example/".into(),
            namespace: "examples".into(),
            package_id: "network-probe".into(),
            version: "1.0.0".into(),
            archive_sha256: "a".repeat(64),
            tree_sha256: "b".repeat(64),
            signer_sha256: "c".repeat(64),
            entry: "probe.exe".into(),
            arguments: Vec::new(),
            environment: BTreeMap::new(),
            permissions,
            vault_id: uuid::Uuid::new_v4().to_string(),
            platform: "windows".into(),
            policy_version: "1".into(),
            expires_at_ms: 120_000,
        }
    }

    #[test]
    fn only_exact_https_origins_are_accepted() {
        assert_eq!(
            canonical_origin("https://example.com/").unwrap(),
            "https://example.com:443"
        );
        assert_eq!(
            canonical_origin("https://example.com:8443/").unwrap(),
            "https://example.com:8443"
        );
        for value in [
            "http://example.com/",
            "https://user@example.com/",
            "https://example.com/path",
        ] {
            assert_eq!(
                canonical_origin(value).unwrap_err().code,
                "EXTENSION_NETWORK_PERMISSION_INVALID"
            );
        }
    }

    #[test]
    fn local_metadata_and_special_addresses_are_denied() {
        for value in [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "100.64.0.1",
            "169.254.169.254",
            "192.0.2.1",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "0.0.0.0",
            "::1",
            "fc00::1",
            "fe80::1",
            "2001:db8::1",
        ] {
            assert!(prohibited(value.parse().unwrap()), "{value}");
        }
        assert!(!prohibited("8.8.8.8".parse().unwrap()));
        assert!(!prohibited("2606:4700:4700::1111".parse().unwrap()));
        for _ in 0..100 {
            let rebound = [
                "160.202.254.170:443".parse().unwrap(),
                "169.254.169.254:443".parse().unwrap(),
            ];
            assert_eq!(
                validate_addresses(&rebound).unwrap_err().code,
                "EXTENSION_NETWORK_ADDRESS_DENIED"
            );
        }
    }

    #[test]
    #[ignore = "需要公开 DNS 和 TLS；由 C-02 生产验收驱动显式执行"]
    fn authorized_public_https_and_redirect_policy_pass_production_matrix() {
        let authority = Authority::default();
        let claims = claims("https://acm.kronecker.cc:18443/", Some("160.202.254.170"));
        let make = || {
            let lease = authority
                .lease(&authority.issue(&claims, 1).unwrap(), &claims, 1)
                .unwrap();
            Broker::new(lease, &claims).unwrap()
        };
        let mut broker = make();
        for _ in 0..20 {
            let response = broker
                .fetch(FetchRequest {
                    url: "https://acm.kronecker.cc:18443/ok".into(),
                    method: "GET".into(),
                    body: String::new(),
                    content_type: None,
                })
                .unwrap();
            assert_eq!(response.status, 200);
            assert_eq!(response.body, "OpenNexus C-02 controlled TLS endpoint");
        }
        for _ in 0..2 {
            let mut broker = make();
            for _ in 0..50 {
                let response = broker
                    .fetch(FetchRequest {
                        url: "https://acm.kronecker.cc:18443/redirect".into(),
                        method: "GET".into(),
                        body: String::new(),
                        content_type: None,
                    })
                    .unwrap();
                assert_eq!(response.status, 302);
                assert!(response.body.is_empty());
            }
        }
    }
}
