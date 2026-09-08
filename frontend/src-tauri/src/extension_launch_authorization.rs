//! Derive launch bytes from a verified permit and Host-bound entry/context.
//! This preparation step does not authorize resume or establish sandbox readiness.
use crate::{
    credentials::{CredentialBroker, CredentialId, Scope},
    extension_launch_data::LaunchData,
    extension_permit::{Authority, Claims, Environment, ExecutionKind, Permit},
    extension_pinned::BoundEntry,
    workspace::{HostError, Result},
};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::Path, sync::atomic::Ordering};
use zeroize::Zeroize;

/// Only the Host's selected workspace, policy and container supply these values.
pub struct Context<'a> {
    pub vault_id: &'a str,
    pub policy_version: &'a str,
    pub system_root: &'a Path,
    pub container_data: &'a Path,
    pub scratch: &'a Path,
}
struct EnvironmentValues(BTreeMap<String, String>);
impl Drop for EnvironmentValues {
    fn drop(&mut self) {
        for value in self.0.values_mut() {
            value.zeroize();
        }
    }
}
/// Credential setup and execution must use the same derived identity. The
/// reference is an opaque ID inside this package's domain, never a caller scope.
pub fn credential_id(claims: &Claims, reference: &str) -> Result<CredentialId> {
    if reference.is_empty()
        || reference.len() > 128
        || !reference
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err(HostError::new("CREDENTIAL_ID_INVALID"));
    }
    let source = reqwest::Url::parse(&claims.source)
        .map_err(|_| HostError::new("EXTENSION_PERMIT_INVALID"))?;
    if source.scheme() != "https"
        || source.host_str().is_none()
        || !source.username().is_empty()
        || source.password().is_some()
        || source.query().is_some()
        || source.fragment().is_some()
    {
        return Err(HostError::new("EXTENSION_PERMIT_INVALID"));
    }
    let mut digest = Sha256::new();
    digest.update(b"OpenNexus extension credential owner v1\0");
    for value in [source.as_str(), &claims.namespace, &claims.package_id] {
        digest.update((value.len() as u64).to_be_bytes());
        digest.update(value.as_bytes());
    }
    let owner = format!("ext.{:x}", digest.finalize());
    let scope = match claims.kind {
        ExecutionKind::Plugin => Scope::Plugin(owner),
        ExecutionKind::Mcp => Scope::Mcp(owner),
    };
    Ok(CredentialId {
        scope,
        id: reference.to_owned(),
    })
}
impl Context<'_> {
    /// Caller must still recheck live trust/permit/session state immediately
    /// before resume; returning encoded data is not an execution lease.
    pub fn build(
        &self,
        authority: &Authority,
        permit: &Permit,
        claims: &Claims,
        entry: &BoundEntry<'_>,
        broker: &CredentialBroker,
        now_ms: u64,
    ) -> Result<LaunchData> {
        authority.verify(permit, claims, now_ms)?;
        if claims.vault_id != self.vault_id
            || claims.policy_version != self.policy_version
            || claims.platform != std::env::consts::OS
        {
            return Err(HostError::new("EXTENSION_EXECUTION_CONTEXT_CHANGED"));
        }
        if claims.entry != entry.relative_name() || claims.tree_sha256 != entry.tree_sha256() {
            return Err(HostError::new("EXTENSION_ENTRY_PERMIT_MISMATCH"));
        }
        let signal = broker.lock_signal();
        let epoch = signal.load(Ordering::SeqCst);
        let mut values = EnvironmentValues(BTreeMap::new());
        let mut used_secret = false;
        for (name, declaration) in &claims.environment {
            match declaration {
                Environment::Literal(value) => {
                    values.0.insert(name.clone(), value.clone());
                }
                Environment::CredentialScope(reference) => {
                    used_secret = true;
                    let id = credential_id(claims, reference)?;
                    let value = broker
                        .resolve(&id.scope, &id)
                        .map_err(|code| {
                            HostError::new(match code.as_str() {
                                "CREDENTIALS_LOCKED" => "CREDENTIALS_LOCKED",
                                "CREDENTIAL_SCOPE_DENIED" => "CREDENTIAL_SCOPE_DENIED",
                                "CREDENTIAL_ID_INVALID" => "CREDENTIAL_ID_INVALID",
                                _ => "EXTENSION_CREDENTIAL_UNAVAILABLE",
                            })
                        })?
                        .ok_or_else(|| HostError::new("EXTENSION_CREDENTIAL_MISSING"))?;
                    let value = std::str::from_utf8(&value)
                        .map_err(|_| HostError::new("EXTENSION_CREDENTIAL_ENCODING_INVALID"))?;
                    // Insert directly into the cleaning owner, never an error or log.
                    values.0.insert(name.clone(), value.to_owned());
                }
            }
        }
        let data = LaunchData::new(
            entry.path(),
            &claims.arguments,
            self.system_root,
            self.container_data,
            self.scratch,
            &values.0,
        )?;
        if used_secret && (broker.is_locked() || signal.load(Ordering::SeqCst) != epoch) {
            return Err(HostError::new("CREDENTIALS_LOCKED"));
        }
        Ok(data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{extension_package::Inventory, extension_pinned::PinnedPackage};
    use zeroize::Zeroizing;
    #[test]
    fn permit_entry_context_and_package_scoped_secrets_drive_launch_data() {
        let temp = tempfile::tempdir().unwrap();
        let package = temp.path().join("package");
        std::fs::create_dir(&package).unwrap();
        std::fs::write(package.join("entry.exe"), b"verified").unwrap();
        let dir =
            cap_std::fs::Dir::open_ambient_dir(&package, cap_std::ambient_authority()).unwrap();
        let inventory = Inventory {
            files: [(
                "entry.exe".into(),
                format!("{:x}", Sha256::digest(b"verified")),
            )]
            .into_iter()
            .collect(),
            expanded_size: 8,
            manifest: "entry.exe".into(),
        };
        let tree = crate::extension_unpack::verify_tree(&dir, &inventory).unwrap();
        let pinned = PinnedPackage::open(&dir, &inventory, &tree).unwrap();
        let entry = pinned.bind_entry("entry.exe").unwrap();
        let claims = Claims {
            kind: ExecutionKind::Mcp,
            source: "https://catalog.example/".into(),
            namespace: "examples".into(),
            package_id: "reviewer".into(),
            version: "1.0.0".into(),
            archive_sha256: "a".repeat(64),
            tree_sha256: tree,
            signer_sha256: "b".repeat(64),
            entry: "entry.exe".into(),
            arguments: vec!["--stdio".into()],
            environment: [("TOKEN".into(), Environment::CredentialScope("token".into()))]
                .into_iter()
                .collect(),
            permissions: ["notes.read".into()].into_iter().collect(),
            vault_id: uuid::Uuid::new_v4().to_string(),
            platform: "windows".into(),
            policy_version: "1".into(),
            expires_at_ms: 1000,
        };
        let system = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap());
        let context = Context {
            vault_id: &claims.vault_id,
            policy_version: "1",
            system_root: &system,
            container_data: temp.path(),
            scratch: temp.path(),
        };
        let authority = Authority::default();
        let permit = authority.issue(&claims, 1).unwrap();
        let mut broker = CredentialBroker::new(temp.path().join("credentials.v1"));
        broker
            .unlock(Zeroizing::new(b"fixture passphrase 123".to_vec()))
            .unwrap();
        broker
            .put(
                &CredentialId {
                    scope: Scope::Provider,
                    id: "token".into(),
                },
                Zeroizing::new(b"provider-fixture-secret".to_vec()),
            )
            .unwrap();
        assert_eq!(
            context
                .build(&authority, &permit, &claims, &entry, &broker, 2)
                .err()
                .unwrap()
                .code,
            "EXTENSION_CREDENTIAL_MISSING"
        );
        let id = credential_id(&claims, "token").unwrap();
        broker
            .put(&id, Zeroizing::new(b"mcp-fixture-secret".to_vec()))
            .unwrap();
        let data = context
            .build(&authority, &permit, &claims, &entry, &broker, 2)
            .unwrap();
        let environment = Zeroizing::new(String::from_utf16(data.environment()).unwrap());
        assert!(environment.contains("TOKEN=mcp-fixture-secret\0"));
        assert!(!environment.contains("provider-fixture-secret"));
        drop(data);
        let mut changed = claims.clone();
        changed.arguments.push("different".into());
        assert_eq!(
            context
                .build(&authority, &permit, &changed, &entry, &broker, 2)
                .err()
                .unwrap()
                .code,
            "EXTENSION_PERMIT_MISMATCH"
        );
        for field in ["kind", "source", "namespace", "package_id"] {
            let mut changed = claims.clone();
            match field {
                "kind" => changed.kind = ExecutionKind::Plugin,
                "source" => changed.source = "https://other.example/".into(),
                "namespace" => changed.namespace = "others".into(),
                _ => changed.package_id = "another".into(),
            }
            let fresh = authority.issue(&changed, 1).unwrap();
            assert_eq!(
                context
                    .build(&authority, &fresh, &changed, &entry, &broker, 2)
                    .err()
                    .unwrap()
                    .code,
                "EXTENSION_CREDENTIAL_MISSING"
            );
        }
        for field in ["entry", "tree", "vault", "policy"] {
            let mut changed = claims.clone();
            match field {
                "entry" => changed.entry = "another.exe".into(),
                "tree" => changed.tree_sha256 = "c".repeat(64),
                "vault" => changed.vault_id = uuid::Uuid::new_v4().to_string(),
                _ => changed.policy_version = "2".into(),
            }
            let fresh = authority.issue(&changed, 1).unwrap();
            let code = context
                .build(&authority, &fresh, &changed, &entry, &broker, 2)
                .err()
                .unwrap()
                .code;
            assert_eq!(
                code,
                if field == "entry" || field == "tree" {
                    "EXTENSION_ENTRY_PERMIT_MISMATCH"
                } else {
                    "EXTENSION_EXECUTION_CONTEXT_CHANGED"
                }
            );
        }
        broker.put(&id, Zeroizing::new(vec![0xff])).unwrap();
        assert_eq!(
            context
                .build(&authority, &permit, &claims, &entry, &broker, 2)
                .err()
                .unwrap()
                .code,
            "EXTENSION_CREDENTIAL_ENCODING_INVALID"
        );
        broker.lock();
        assert_eq!(
            context
                .build(&authority, &permit, &claims, &entry, &broker, 2)
                .err()
                .unwrap()
                .code,
            "CREDENTIALS_LOCKED"
        );
        assert_eq!(
            context
                .build(&authority, &permit, &claims, &entry, &broker, 1000)
                .err()
                .unwrap()
                .code,
            "EXTENSION_PERMIT_EXPIRED"
        );
    }
}
