//! Core 发布包的签名校验、兼容组合指针和崩溃恢复事务。

use crate::{
    core::verify_bundle,
    workspace::{hash, HostError, Result},
};
use ed25519_dalek::{Signature, VerifyingKey};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReleaseManifest {
    protocol: u32,
    product: String,
    host_version: String,
    core_version: String,
    files: BTreeMap<String, String>,
    #[serde(default)]
    lock_sha256: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Release {
    pub host_version: String,
    pub core_version: String,
    pub protocol: u32,
    pub root: String,
    pub manifest: String,
    pub manifest_sha256: String,
    pub signer_sha256: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ActiveRelease {
    pub release: Release,
    pub pending_operation: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Receipt {
    pub operation_id: String,
    pub state: String,
}

pub struct Store {
    db: Connection,
    host_version: String,
    trusted_key: [u8; 32],
}

fn invalid(code: &str) -> HostError {
    HostError::new(code)
}

fn encode<T: Serialize>(value: &T) -> Result<String> {
    serde_json::to_string(value).map_err(|_| invalid("CORE_UPDATE_STATE_INVALID"))
}

impl Store {
    pub fn open(path: &Path, host_version: &str, trusted_key: [u8; 32]) -> Result<Self> {
        semver::Version::parse(host_version).map_err(|_| invalid("CORE_UPDATE_HOST_INVALID"))?;
        VerifyingKey::from_bytes(&trusted_key).map_err(|_| invalid("CORE_UPDATE_KEY_INVALID"))?;
        let db = Connection::open(path)?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
             CREATE TABLE IF NOT EXISTS core_active(
               singleton INTEGER PRIMARY KEY CHECK(singleton=1),
               release TEXT NOT NULL,
               pending_operation TEXT
             );
             CREATE TABLE IF NOT EXISTS core_updates(
               id TEXT PRIMARY KEY,
               fingerprint TEXT NOT NULL,
               before_release TEXT,
               after_release TEXT NOT NULL,
               state TEXT NOT NULL
             );",
        )?;
        let mut store = Self {
            db,
            host_version: host_version.to_owned(),
            trusted_key,
        };
        store.recover()?;
        Ok(store)
    }

    pub fn active(&self) -> Result<Option<ActiveRelease>> {
        let row: Option<(String, Option<String>)> = self
            .db
            .query_row(
                "SELECT release,pending_operation FROM core_active WHERE singleton=1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        row.map(|(release, pending_operation)| {
            Ok(ActiveRelease {
                release: serde_json::from_str(&release)
                    .map_err(|_| invalid("CORE_UPDATE_STATE_INVALID"))?,
                pending_operation,
            })
        })
        .transpose()
    }

    fn verify_release(&self, root: &Path, manifest: &[u8], signature: &[u8]) -> Result<Release> {
        let parsed: ReleaseManifest = serde_json::from_slice(manifest)
            .map_err(|_| invalid("CORE_UPDATE_MANIFEST_INVALID"))?;
        if parsed.product != "OpenNexus"
            || parsed.protocol != 1
            || parsed.host_version != self.host_version
            || parsed.files.is_empty()
            || semver::Version::parse(&parsed.core_version).is_err()
            || parsed.lock_sha256.as_ref().is_some_and(|digest| {
                digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit())
            })
        {
            return Err(invalid("CORE_UPDATE_INCOMPATIBLE"));
        }
        let signature = Signature::from_slice(signature)
            .map_err(|_| invalid("CORE_UPDATE_SIGNATURE_INVALID"))?;
        let key = VerifyingKey::from_bytes(&self.trusted_key)
            .map_err(|_| invalid("CORE_UPDATE_KEY_INVALID"))?;
        key.verify_strict(manifest, &signature)
            .map_err(|_| invalid("CORE_UPDATE_SIGNATURE_INVALID"))?;
        let canonical = root
            .canonicalize()
            .map_err(|_| invalid("CORE_UPDATE_BUNDLE_INVALID"))?;
        if !canonical.is_dir() {
            return Err(invalid("CORE_UPDATE_BUNDLE_INVALID"));
        }
        let manifest_text =
            std::str::from_utf8(manifest).map_err(|_| invalid("CORE_UPDATE_MANIFEST_INVALID"))?;
        verify_bundle(&canonical, manifest_text)
            .map_err(|_| invalid("CORE_UPDATE_BUNDLE_INVALID"))?;
        Ok(Release {
            host_version: parsed.host_version,
            core_version: parsed.core_version,
            protocol: parsed.protocol,
            root: canonical.to_string_lossy().into_owned(),
            manifest: manifest_text.to_owned(),
            manifest_sha256: hash(manifest),
            signer_sha256: hash(&self.trusted_key),
        })
    }

    pub fn install(
        &mut self,
        operation: &str,
        root: &Path,
        manifest: &[u8],
        signature: &[u8],
    ) -> Result<Receipt> {
        let release = self.verify_release(root, manifest, signature)?;
        self.switch(operation, &release, |_| Ok(()))
    }

    #[cfg(test)]
    fn install_with_checkpoint(
        &mut self,
        operation: &str,
        root: &Path,
        manifest: &[u8],
        signature: &[u8],
        checkpoint: impl FnMut(&str) -> Result<()>,
    ) -> Result<Receipt> {
        let release = self.verify_release(root, manifest, signature)?;
        self.switch(operation, &release, checkpoint)
    }

    fn switch(
        &mut self,
        operation: &str,
        release: &Release,
        mut checkpoint: impl FnMut(&str) -> Result<()>,
    ) -> Result<Receipt> {
        if uuid::Uuid::parse_str(operation).is_err() || release.host_version != self.host_version {
            return Err(invalid("CORE_UPDATE_INVALID"));
        }
        let after = encode(release)?;
        let fingerprint = hash(after.as_bytes());
        let tx = self.db.transaction()?;
        let prior: Option<(String, String)> = tx
            .query_row(
                "SELECT fingerprint,state FROM core_updates WHERE id=?1",
                [operation],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        if let Some((old_fingerprint, state)) = prior {
            if old_fingerprint != fingerprint {
                return Err(invalid("OPERATION_REUSED"));
            }
            return Ok(Receipt {
                operation_id: operation.to_owned(),
                state,
            });
        }
        let before = tx
            .query_row(
                "SELECT release FROM core_active WHERE singleton=1",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        tx.execute(
            "INSERT INTO core_updates VALUES(?1,?2,?3,?4,'checking')",
            params![operation, fingerprint, before, after],
        )?;
        checkpoint("journal_recorded")?;
        tx.execute(
            "INSERT INTO core_active VALUES(1,?1,?2)
             ON CONFLICT(singleton) DO UPDATE SET release=excluded.release,pending_operation=excluded.pending_operation",
            params![after, operation],
        )?;
        checkpoint("pointer_recorded")?;
        tx.commit()?;
        checkpoint("switch_committed")?;
        Ok(Receipt {
            operation_id: operation.to_owned(),
            state: "checking".to_owned(),
        })
    }

    pub fn finish(&mut self, operation: &str, healthy: bool) -> Result<Receipt> {
        let tx = self.db.transaction()?;
        let (before, after, state): (Option<String>, String, String) = tx.query_row(
            "SELECT before_release,after_release,state FROM core_updates WHERE id=?1",
            [operation],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        if state != "checking" {
            return Ok(Receipt {
                operation_id: operation.to_owned(),
                state,
            });
        }
        let active: (String, Option<String>) = tx.query_row(
            "SELECT release,pending_operation FROM core_active WHERE singleton=1",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        if active.0 != after || active.1.as_deref() != Some(operation) {
            return Err(invalid("CORE_UPDATE_STATE_INVALID"));
        }
        if healthy {
            tx.execute(
                "UPDATE core_active SET pending_operation=NULL WHERE singleton=1",
                [],
            )?;
        } else if let Some(previous) = before {
            tx.execute(
                "UPDATE core_active SET release=?1,pending_operation=NULL WHERE singleton=1",
                [previous],
            )?;
        } else {
            tx.execute("DELETE FROM core_active WHERE singleton=1", [])?;
        }
        let state = if healthy { "complete" } else { "rolled_back" };
        tx.execute(
            "UPDATE core_updates SET state=?2 WHERE id=?1",
            params![operation, state],
        )?;
        tx.commit()?;
        Ok(Receipt {
            operation_id: operation.to_owned(),
            state: state.to_owned(),
        })
    }

    pub fn recover(&mut self) -> Result<usize> {
        let ids: Vec<String> = self
            .db
            .prepare("SELECT id FROM core_updates WHERE state='checking' ORDER BY id")?
            .query_map([], |row| row.get(0))?
            .collect::<std::result::Result<_, _>>()?;
        for id in &ids {
            self.finish(id, false)?;
        }
        Ok(ids.len())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    use std::{
        fs,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };

    fn signed_bundle(root: &Path, key: &SigningKey, host: &str, core: &str) -> (Vec<u8>, Vec<u8>) {
        fs::create_dir_all(root).unwrap();
        fs::write(root.join("opennexus-core.exe"), core.as_bytes()).unwrap();
        let manifest = serde_json::to_vec(&serde_json::json!({
            "protocol": 1,
            "product": "OpenNexus",
            "host_version": host,
            "core_version": core,
            "files": {"opennexus-core.exe": hash(core.as_bytes())}
        }))
        .unwrap();
        let signature = key.sign(&manifest).to_bytes().to_vec();
        (manifest, signature)
    }

    fn release(root: &Path, core_version: &str, key: &SigningKey) -> Release {
        Release {
            host_version: "0.3.0-alpha.1".into(),
            core_version: core_version.into(),
            protocol: 1,
            root: root.to_string_lossy().into_owned(),
            manifest: core_version.into(),
            manifest_sha256: hash(core_version.as_bytes()),
            signer_sha256: hash(&key.verifying_key().to_bytes()),
        }
    }

    fn seed(path: &Path, root: &Path, key: &SigningKey) {
        let mut store = Store::open(path, "0.3.0-alpha.1", key.verifying_key().to_bytes()).unwrap();
        let old = release(root, "0.3.0-alpha.1", key);
        let operation = uuid::Uuid::new_v4().to_string();
        store.switch(&operation, &old, |_| Ok(())).unwrap();
        store.finish(&operation, true).unwrap();
    }

    #[test]
    fn signed_compatible_release_switches_and_failed_health_rolls_back() {
        let temp = tempfile::tempdir().unwrap();
        let key = SigningKey::from_bytes(&[41; 32]);
        let mut store = Store::open(
            &temp.path().join("state.sqlite3"),
            "0.3.0-alpha.1",
            key.verifying_key().to_bytes(),
        )
        .unwrap();
        let old = temp.path().join("old");
        let new = temp.path().join("new");
        let (old_manifest, old_signature) =
            signed_bundle(&old, &key, "0.3.0-alpha.1", "0.3.0-alpha.1");
        let first = uuid::Uuid::new_v4().to_string();
        store
            .install(&first, &old, &old_manifest, &old_signature)
            .unwrap();
        store.finish(&first, true).unwrap();
        let (new_manifest, new_signature) =
            signed_bundle(&new, &key, "0.3.0-alpha.1", "0.3.0-alpha.2");
        let update = uuid::Uuid::new_v4().to_string();
        store
            .install(&update, &new, &new_manifest, &new_signature)
            .unwrap();
        store.finish(&update, false).unwrap();
        assert_eq!(
            store.active().unwrap().unwrap().release.core_version,
            "0.3.0-alpha.1"
        );
        let bad = signed_bundle(&new, &key, "9.0.0", "9.0.0");
        assert_eq!(
            store
                .install(&uuid::Uuid::new_v4().to_string(), &new, &bad.0, &bad.1)
                .unwrap_err()
                .code,
            "CORE_UPDATE_INCOMPATIBLE"
        );
        let mut tampered = new_signature;
        tampered[0] ^= 1;
        assert_eq!(
            store
                .install(
                    &uuid::Uuid::new_v4().to_string(),
                    &new,
                    &new_manifest,
                    &tampered,
                )
                .unwrap_err()
                .code,
            "CORE_UPDATE_SIGNATURE_INVALID"
        );
    }

    #[test]
    fn injected_switch_failures_recover_twenty_times_to_a_complete_combination() {
        for boundary in ["journal_recorded", "pointer_recorded", "switch_committed"] {
            for _ in 0..20 {
                let temp = tempfile::tempdir().unwrap();
                let key = SigningKey::from_bytes(&[42; 32]);
                let path = temp.path().join("state.sqlite3");
                seed(&path, &temp.path().join("old"), &key);
                let mut store =
                    Store::open(&path, "0.3.0-alpha.1", key.verifying_key().to_bytes()).unwrap();
                let new = release(&temp.path().join("new"), "0.3.0-alpha.2", &key);
                let update = uuid::Uuid::new_v4().to_string();
                assert!(store
                    .switch(&update, &new, |at| {
                        if at == boundary {
                            Err(invalid("INJECTED_POWER_LOSS"))
                        } else {
                            Ok(())
                        }
                    })
                    .is_err());
                drop(store);
                let started = Instant::now();
                let reopened =
                    Store::open(&path, "0.3.0-alpha.1", key.verifying_key().to_bytes()).unwrap();
                assert!(started.elapsed().as_secs() < 10);
                let active = reopened.active().unwrap().unwrap();
                assert_eq!(active.release.host_version, "0.3.0-alpha.1");
                assert_eq!(active.release.core_version, "0.3.0-alpha.1");
                assert!(active.pending_operation.is_none());
            }
        }
    }

    #[test]
    #[ignore = "父验收测试会在指定持久化边界强制终止该进程"]
    fn power_cut_worker() {
        let Some(path) = std::env::var_os("OPENNEXUS_A04_DATABASE") else {
            return;
        };
        let boundary = std::env::var("OPENNEXUS_A04_BOUNDARY").unwrap();
        let marker = std::path::PathBuf::from(std::env::var_os("OPENNEXUS_A04_MARKER").unwrap());
        let key = SigningKey::from_bytes(&[42; 32]);
        let mut store = Store::open(
            Path::new(&path),
            "0.3.0-alpha.1",
            key.verifying_key().to_bytes(),
        )
        .unwrap();
        let bundle = marker.parent().unwrap().join("new-core");
        let (manifest, signature) = signed_bundle(&bundle, &key, "0.3.0-alpha.1", "0.3.0-alpha.2");
        let operation = uuid::Uuid::new_v4().to_string();
        let _ = store.install_with_checkpoint(&operation, &bundle, &manifest, &signature, |at| {
            if at == boundary {
                let file = fs::File::create(&marker).unwrap();
                file.sync_all().unwrap();
                loop {
                    thread::sleep(Duration::from_secs(60));
                }
            }
            Ok(())
        });
        panic!("断电夹具越过了指定边界");
    }

    #[test]
    fn every_switch_boundary_survives_twenty_real_process_terminations() {
        for boundary in ["journal_recorded", "pointer_recorded", "switch_committed"] {
            for round in 0..20 {
                let temp = tempfile::tempdir().unwrap();
                let path = temp.path().join("state.sqlite3");
                let key = SigningKey::from_bytes(&[42; 32]);
                seed(&path, &temp.path().join("old"), &key);
                let marker = temp.path().join(format!("{boundary}-{round}.ready"));
                let mut child = Command::new(std::env::current_exe().unwrap())
                    .args([
                        "--ignored",
                        "--exact",
                        "core_update::tests::power_cut_worker",
                        "--nocapture",
                    ])
                    .env("OPENNEXUS_A04_DATABASE", &path)
                    .env("OPENNEXUS_A04_BOUNDARY", boundary)
                    .env("OPENNEXUS_A04_MARKER", &marker)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .unwrap();
                let started = Instant::now();
                while !marker.is_file() {
                    assert!(child.try_wait().unwrap().is_none());
                    assert!(started.elapsed() < Duration::from_secs(10));
                    thread::sleep(Duration::from_millis(5));
                }
                child.kill().unwrap();
                assert!(!child.wait().unwrap().success());
                let reopened =
                    Store::open(&path, "0.3.0-alpha.1", key.verifying_key().to_bytes()).unwrap();
                let active = reopened.active().unwrap().unwrap();
                assert_eq!(active.release.host_version, "0.3.0-alpha.1");
                assert_eq!(active.release.core_version, "0.3.0-alpha.1");
                assert!(active.pending_operation.is_none());
            }
        }
    }
}
