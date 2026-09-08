//! Host-only execution permit binding. No IPC caller can mint these permits.
//! The installer must complete user consent and current trust checks before issue.
use crate::workspace::{HostError, Result};
use hmac::{Hmac, Mac};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::{BTreeMap, BTreeSet};
use std::{
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use zeroize::Zeroize;

#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "value", deny_unknown_fields)]
pub enum Environment {
    Literal(String),
    // An opaque credential reference in the Host-derived package scope, never plaintext.
    CredentialScope(String),
}

#[derive(Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionKind {
    Plugin,
    Mcp,
}

#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Claims {
    pub kind: ExecutionKind,
    pub source: String,
    pub namespace: String,
    pub package_id: String,
    pub version: String,
    pub archive_sha256: String,
    pub tree_sha256: String,
    pub signer_sha256: String,
    pub entry: String,
    pub arguments: Vec<String>,
    pub environment: BTreeMap<String, Environment>,
    pub permissions: BTreeSet<String>,
    pub vault_id: String,
    pub platform: String,
    pub policy_version: String,
    pub expires_at_ms: u64,
}

/// Opaque authenticator; the Host retains claims separately. No paths, arguments
/// or credential declarations need to be passed to a renderer with the token.
pub struct Permit {
    mac: [u8; 32],
    generation: u64,
}
#[derive(Clone)]
pub struct Lease {
    generation: (Arc<AtomicU64>, u64),
    credential: Option<(Arc<AtomicU64>, u64)>,
    expires: Instant,
}
impl Lease {
    pub fn check(&self) -> Result<()> {
        if self.generation.0.load(Ordering::SeqCst) != self.generation.1 {
            return Err(HostError::new("EXTENSION_PERMIT_REVOKED"));
        }
        if self
            .credential
            .as_ref()
            .is_some_and(|(signal, epoch)| signal.load(Ordering::SeqCst) != *epoch)
        {
            return Err(HostError::new("CREDENTIALS_LOCKED"));
        }
        if Instant::now() >= self.expires {
            return Err(HostError::new("EXTENSION_PERMIT_EXPIRED"));
        }
        Ok(())
    }
    #[cfg(windows)]
    pub(crate) fn bind_credential(&mut self, signal: Arc<AtomicU64>) {
        let epoch = signal.load(Ordering::SeqCst);
        self.credential = Some((signal, epoch));
    }
}
pub struct Authority {
    key: [u8; 32],
    generation: Arc<AtomicU64>,
}
impl Drop for Authority {
    fn drop(&mut self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        self.key.zeroize();
    }
}
impl Default for Authority {
    fn default() -> Self {
        let mut key = [0; 32];
        rand::rngs::OsRng.fill_bytes(&mut key);
        Self {
            key,
            generation: Arc::new(AtomicU64::new(0)),
        }
    }
}
impl Claims {
    fn encoded(&self, now_ms: u64) -> Result<Vec<u8>> {
        let bad = || HostError::new("EXTENSION_PERMIT_INVALID");
        if self.expires_at_ms <= now_ms {
            return Err(HostError::new("EXTENSION_PERMIT_EXPIRED"));
        }
        let url = reqwest::Url::parse(&self.source).map_err(|_| bad())?;
        if url.scheme() != "https"
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || self.source.len() > 2048
        {
            return Err(bad());
        }
        for slug in [&self.namespace, &self.package_id] {
            if !(2..=64).contains(&slug.len())
                || !slug.as_bytes()[0].is_ascii_alphanumeric()
                || !slug
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            {
                return Err(bad());
            }
        }
        if semver::Version::parse(&self.version).is_err()
            || uuid::Uuid::parse_str(&self.vault_id).is_err()
        {
            return Err(bad());
        }
        for digest in [&self.archive_sha256, &self.tree_sha256, &self.signer_sha256] {
            if digest.len() != 64
                || !digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(bad());
            }
        }
        if self.entry.len() > 1024
            || self.entry.split('/').any(|p| {
                p.is_empty()
                    || p == "."
                    || p == ".."
                    || p.contains(['\\', ':'])
                    || p.chars().any(char::is_control)
            })
        {
            return Err(bad());
        }
        if self.arguments.len() > 128
            || self.environment.len() > 128
            || self.permissions.len() > 128
            || self
                .arguments
                .iter()
                .any(|s| s.len() > 8192 || s.contains('\0'))
            || self
                .permissions
                .iter()
                .any(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))
            || self.platform.is_empty()
            || self.platform.len() > 64
            || self.policy_version.is_empty()
            || self.policy_version.len() > 128
        {
            return Err(bad());
        }
        for (name, declaration) in &self.environment {
            if name.is_empty()
                || name.len() > 128
                || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
            {
                return Err(bad());
            }
            let value = match declaration {
                Environment::Literal(v) | Environment::CredentialScope(v) => v,
            };
            if value.len() > 8192 || value.contains('\0') {
                return Err(bad());
            }
        }
        let bytes = serde_json::to_vec(self).map_err(|_| bad())?;
        if bytes.len() > 256 * 1024 {
            return Err(bad());
        }
        Ok(bytes)
    }
}
impl Authority {
    /// Call only after consent and live trust validation. This authenticates the
    /// decision; it does not establish sandbox availability or grant broker access.
    pub fn issue(&self, claims: &Claims, now_ms: u64) -> Result<Permit> {
        let generation = self.generation.load(Ordering::SeqCst);
        let encoded = claims.encoded(now_ms)?;
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.key).expect("HMAC key");
        mac.update(b"OpenNexus execution permit v2\0");
        mac.update(&generation.to_be_bytes());
        mac.update(&encoded);
        Ok(Permit {
            mac: mac.finalize().into_bytes().into(),
            generation,
        })
    }
    pub fn verify(&self, permit: &Permit, actual: &Claims, now_ms: u64) -> Result<()> {
        let generation = self.generation.load(Ordering::SeqCst);
        if generation != permit.generation {
            return Err(HostError::new("EXTENSION_PERMIT_REVOKED"));
        }
        let encoded = actual.encoded(now_ms)?;
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.key).expect("HMAC key");
        mac.update(b"OpenNexus execution permit v2\0");
        mac.update(&generation.to_be_bytes());
        mac.update(&encoded);
        mac.verify_slice(&permit.mac)
            .map_err(|_| HostError::new("EXTENSION_PERMIT_MISMATCH"))?;
        if generation != self.generation.load(Ordering::SeqCst) {
            return Err(HostError::new("EXTENSION_PERMIT_REVOKED"));
        }
        Ok(())
    }
    pub fn lease(&self, permit: &Permit, claims: &Claims, now_ms: u64) -> Result<Lease> {
        let started = Instant::now();
        self.verify(permit, claims, now_ms)?;
        let expires = started
            .checked_add(Duration::from_millis(claims.expires_at_ms - now_ms))
            .ok_or_else(|| HostError::new("EXTENSION_PERMIT_INVALID"))?;
        let lease = Lease {
            generation: (Arc::clone(&self.generation), permit.generation),
            credential: None,
            expires,
        };
        lease.check()?;
        Ok(lease)
    }
    /// Lock/logout/policy invalidation may discard all permits. Restart creates a
    /// fresh key, so an old process token cannot silently revive authorization.
    pub fn invalidate_all(&mut self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        self.key.zeroize();
        rand::rngs::OsRng.fill_bytes(&mut self.key);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn claims() -> Claims {
        Claims {
            kind: ExecutionKind::Mcp,
            source: "https://catalog.example/".into(),
            namespace: "examples".into(),
            package_id: "note-reviewer".into(),
            version: "1.0.0".into(),
            archive_sha256: "a".repeat(64),
            tree_sha256: "b".repeat(64),
            signer_sha256: "c".repeat(64),
            entry: "main.py".into(),
            arguments: vec!["--stdio".into()],
            environment: BTreeMap::from([("MODE".into(), Environment::Literal("test".into()))]),
            permissions: BTreeSet::from(["notes.read".into()]),
            vault_id: uuid::Uuid::new_v4().to_string(),
            platform: "windows".into(),
            policy_version: "1".into(),
            expires_at_ms: 1000,
        }
    }
    #[test]
    fn every_claim_is_bound_expiry_and_key_rotation_fail_closed() {
        let mut authority = Authority::default();
        let original = claims();
        let token = authority.issue(&original, 100).unwrap();
        authority.verify(&token, &original, 999).unwrap();
        let value = serde_json::to_value(&original).unwrap();
        for field in value.as_object().unwrap().keys() {
            let mut changed = value.clone();
            match &mut changed[field] {
                serde_json::Value::String(s) => {
                    *s = match field.as_str() {
                        "kind" => "plugin".into(),
                        "source" => "https://other.example/".into(),
                        "version" => "1.0.1".into(),
                        "archive_sha256" | "tree_sha256" | "signer_sha256" => "d".repeat(64),
                        "vault_id" => uuid::Uuid::new_v4().to_string(),
                        _ => format!("{s}x"),
                    };
                }
                serde_json::Value::Number(n) => *n = 1001.into(),
                serde_json::Value::Array(a) => a.push("extra".into()),
                serde_json::Value::Object(o) => {
                    o.clear();
                }
                _ => panic!("unexpected claim type"),
            }
            let changed: Claims = serde_json::from_value(changed).unwrap();
            changed.encoded(100).unwrap();
            assert!(
                authority.verify(&token, &changed, 100).is_err(),
                "unbound field {field}"
            );
        }
        assert!(authority.verify(&token, &original, 1000).is_err());
        assert!(Authority::default().verify(&token, &original, 100).is_err());
        authority.invalidate_all();
        assert!(authority.verify(&token, &original, 100).is_err());
    }
    #[test]
    fn invalid_paths_environment_and_oversize_decisions_are_rejected() {
        let authority = Authority::default();
        for entry in ["../main.py", "C:/main.py", "\\main.py", "a//b", ""] {
            let mut c = claims();
            c.entry = entry.into();
            assert!(authority.issue(&c, 100).is_err());
        }
        let mut c = claims();
        c.environment
            .insert("BAD=KEY".into(), Environment::Literal("v".into()));
        assert!(authority.issue(&c, 100).is_err());
        let mut c = claims();
        c.arguments = vec!["x".repeat(8192); 128];
        assert!(authority.issue(&c, 100).is_err());
        let mut c = claims();
        c.source = "http://catalog.example".into();
        assert!(authority.issue(&c, 100).is_err());
    }
}
