//! Durable verified-package staging. Staging never enables a package or grants permissions.
use crate::{
    extension_package::Release,
    workspace::{hash, HostError, Result},
};
use fs2::FileExt;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

pub struct Signer<'a> {
    pub public_key: &'a [u8; 32],
    pub key_id: &'a str,
    pub namespace: &'a str,
    pub revoked: bool,
}
pub struct Stage<'a> {
    pub operation_id: &'a str,
    pub source: &'a str,
    pub signer: Signer<'a>,
    pub release: &'a Release,
    pub withdrawn: bool,
    pub archive: &'a [u8],
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Receipt {
    pub operation_id: String,
    pub package_key: String,
    pub archive_sha256: String,
    pub version: String,
    pub state: String,
}
#[derive(Debug, Serialize)]
pub struct StagedPackage {
    pub package_key: String,
    pub source: String,
    pub namespace: String,
    pub package_id: String,
    pub version: String,
    pub archive_sha256: String,
    pub state: String,
}
pub struct ExtensionStore {
    root: PathBuf,
    db: Connection,
    _lock: File,
}
fn ordinary(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() {
        return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
        }
    }
    if metadata.is_file() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if metadata.nlink() != 1 {
                return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::Storage::FileSystem::{
                GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
            };
            let file = File::open(path)?;
            let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
            if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
                || info.nNumberOfLinks != 1
            {
                return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
            }
        }
    }
    Ok(())
}
fn source(value: &str) -> Result<String> {
    let mut url =
        reqwest::Url::parse(value).map_err(|_| HostError::new("EXTENSION_SOURCE_INVALID"))?;
    if value.len() > 2048
        || url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(HostError::new("EXTENSION_SOURCE_INVALID"));
    }
    if !url.path().ends_with('/') {
        url.set_path(&format!("{}/", url.path()));
    }
    Ok(url.to_string())
}
impl ExtensionStore {
    /// Creates a lock preview from local staged packages. This does not replace online revocation checks.
    pub fn dependency_plan(
        &self,
        root_key: &str,
        app_version: &str,
        platform: &str,
        architecture: &str,
    ) -> Result<crate::extension_dependencies::Plan> {
        use crate::extension_dependencies::{plan, Candidate};
        let source: String = self.db.query_row(
            "SELECT source FROM versions WHERE package_key=?1 AND state='staged'",
            [root_key],
            |r| r.get(0),
        )?;
        let mut statement=self.db.prepare("SELECT package_key,release,signer FROM versions WHERE source=?1 AND state='staged' ORDER BY package_key LIMIT 4097")?;
        let mut rows = statement.query([&source])?;
        let mut candidates = Vec::new();
        let mut keys = std::collections::BTreeMap::new();
        let mut total = 0usize;
        while let Some(row) = rows.next()? {
            let key: String = row.get(0)?;
            let raw: String = row.get(1)?;
            let signer: Vec<u8> = row.get(2)?;
            total = total.saturating_add(raw.len());
            if raw.len() > 256 * 1024 || total > 64 * 1024 * 1024 || candidates.len() >= 4096 {
                return Err(HostError::new("EXTENSION_DEPENDENCY_COMPLEXITY"));
            }
            let public: [u8; 32] = signer
                .try_into()
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let release: Release = serde_json::from_str(&raw)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            if key
                != hash(
                    &serde_json::to_vec(&(
                        &source,
                        &release.namespace,
                        &release.package_id,
                        &release.version,
                    ))
                    .unwrap(),
                )
            {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            keys.insert(key.clone(), public);
            candidates.push(Candidate {
                package_key: key,
                source: source.clone(),
                release,
                signer_sha256: hash(&public),
            });
        }
        let result = plan(&candidates, root_key, app_version, platform, architecture)?;
        for locked in &result.packages {
            let candidate = candidates
                .iter()
                .find(|c| c.package_key == locked.package_key)
                .ok_or_else(|| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let release = &candidate.release;
            release.verify_package(
                &keys[&locked.package_key],
                &release.key_id,
                &release.namespace,
                false,
                false,
                &self.archive(&locked.package_key)?,
            )?;
        }
        Ok(result)
    }
    pub fn staged(&self, offset: u32, limit: u32) -> Result<Vec<StagedPackage>> {
        if limit == 0 || limit > 200 {
            return Err(HostError::new("EXTENSION_PAGE_INVALID"));
        }
        let mut statement = self.db.prepare("SELECT package_key,source,namespace,package_id,version,archive_hash,state FROM versions ORDER BY package_key LIMIT ?1 OFFSET ?2")?;
        let rows = statement.query_map(params![limit, offset], |r| {
            Ok(StagedPackage {
                package_key: r.get(0)?,
                source: r.get(1)?,
                namespace: r.get(2)?,
                package_id: r.get(3)?,
                version: r.get(4)?,
                archive_sha256: r.get(5)?,
                state: r.get(6)?,
            })
        })?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }
    /// Host supplies an existing application-owned directory, never a package-supplied path.
    pub fn open(root: &Path) -> Result<Self> {
        ordinary(root)?;
        if !root.is_dir() {
            return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
        }
        let root = root.canonicalize()?;
        let lock_path = root.join("extensions.lock");
        if lock_path.exists() {
            ordinary(&lock_path)?;
        }
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)?;
        lock.try_lock_exclusive()
            .map_err(|_| HostError::new("EXTENSION_STORE_BUSY"))?;
        let objects = root.join("objects");
        if objects.exists() {
            ordinary(&objects)?;
        } else {
            fs::create_dir(&objects)?;
        }
        let database = root.join("extensions.sqlite3");
        if database.exists() {
            ordinary(&database)?;
        }
        for suffix in ["-wal", "-shm", "-journal"] {
            let sidecar = root.join(format!("extensions.sqlite3{suffix}"));
            if sidecar.exists() {
                ordinary(&sidecar)?;
            }
        }
        let db = Connection::open(database)?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")?;
        let version: i64 = db.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version > 1 {
            return Err(HostError::new("EXTENSION_SCHEMA_INCOMPATIBLE"));
        }
        db.execute_batch("BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS versions (package_key TEXT PRIMARY KEY,source TEXT NOT NULL,namespace TEXT NOT NULL,package_id TEXT NOT NULL,version TEXT NOT NULL,fingerprint TEXT NOT NULL,release TEXT NOT NULL,manifest TEXT NOT NULL,inventory TEXT NOT NULL,archive_hash TEXT NOT NULL,size INTEGER NOT NULL,signer BLOB NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stage_operations (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,receipt TEXT NOT NULL);
            PRAGMA user_version=1; COMMIT;")?;
        Ok(Self {
            root,
            db,
            _lock: lock,
        })
    }
    pub fn stage(&mut self, request: Stage<'_>) -> Result<Receipt> {
        self.stage_inner(request, |_| Ok(()))
    }
    fn stage_inner(
        &mut self,
        request: Stage<'_>,
        mut checkpoint: impl FnMut(&str) -> Result<()>,
    ) -> Result<Receipt> {
        Uuid::parse_str(request.operation_id)
            .map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        let source = source(request.source)?;
        let release = request.release;
        let (inventory, manifest) = release.verify_package(
            request.signer.public_key,
            request.signer.key_id,
            request.signer.namespace,
            request.signer.revoked,
            request.withdrawn,
            request.archive,
        )?;
        let key = hash(
            serde_json::to_vec(&(
                &source,
                &release.namespace,
                &release.package_id,
                &release.version,
            ))
            .unwrap()
            .as_slice(),
        );
        let fingerprint = hash(
            serde_json::to_vec(&(&source, release, request.signer.public_key.as_slice()))
                .unwrap()
                .as_slice(),
        );
        if let Some((old, receipt)) = self
            .db
            .query_row(
                "SELECT fingerprint,receipt FROM stage_operations WHERE id=?1",
                [request.operation_id],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()?
        {
            if old != fingerprint {
                return Err(HostError::new("OPERATION_REUSED"));
            }
            let receipt: Receipt = serde_json::from_str(&receipt)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            self.verify_object(&receipt.archive_sha256, release.size)?;
            return Ok(receipt);
        }
        let old: Option<String> = self
            .db
            .query_row(
                "SELECT fingerprint FROM versions WHERE package_key=?1",
                [&key],
                |r| r.get(0),
            )
            .optional()?;
        if old.is_some_and(|old| old != fingerprint) {
            return Err(HostError::new("EXTENSION_VERSION_IMMUTABLE"));
        }
        let target = self.object_path(&release.sha256)?;
        if target.exists() {
            self.verify_object(&release.sha256, release.size)?;
        } else {
            let mut file = tempfile::NamedTempFile::new_in(target.parent().unwrap())?;
            file.write_all(request.archive)?;
            file.as_file().sync_all()?;
            file.persist_noclobber(&target)
                .map_err(|_| HostError::new("EXTENSION_STORE_WRITE_FAILED"))?;
            #[cfg(unix)]
            File::open(target.parent().unwrap())?.sync_all()?;
        }
        checkpoint("object_stored")?;
        let receipt = Receipt {
            operation_id: request.operation_id.into(),
            package_key: key.clone(),
            archive_sha256: release.sha256.clone(),
            version: release.version.clone(),
            state: "staged".into(),
        };
        let tx = self.db.transaction()?;
        tx.execute("INSERT OR IGNORE INTO versions VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,'staged')",params![key,source,release.namespace,release.package_id,release.version,fingerprint,serde_json::to_string(release).unwrap(),serde_json::to_string(&manifest).unwrap(),serde_json::to_string(&inventory.files).unwrap(),release.sha256,release.size,request.signer.public_key.as_slice()])?;
        checkpoint("version_recorded")?;
        tx.execute(
            "INSERT INTO stage_operations VALUES (?1,?2,?3)",
            params![
                request.operation_id,
                fingerprint,
                serde_json::to_string(&receipt).unwrap()
            ],
        )?;
        checkpoint("receipt_recorded")?;
        tx.commit()?;
        checkpoint("committed")?;
        Ok(receipt)
    }
    fn object_path(&self, digest: &str) -> Result<PathBuf> {
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        ordinary(&self.root)?;
        ordinary(&self.root.join("objects"))?;
        Ok(self.root.join("objects").join(digest))
    }
    fn verify_object(&self, digest: &str, size: u64) -> Result<()> {
        let path = self.object_path(digest)?;
        ordinary(&path)?;
        crate::payloads::verify(&path, digest, size)
            .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))
    }
    pub fn archive(&self, package_key: &str) -> Result<Vec<u8>> {
        let (digest, size): (String, u64) = self.db.query_row(
            "SELECT archive_hash,size FROM versions WHERE package_key=?1",
            [package_key],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        if size > 10 * 1024 * 1024 {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        self.verify_object(&digest, size)?;
        let mut bytes = Vec::new();
        File::open(self.object_path(&digest)?)?
            .take(size + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() as u64 != size || hash(&bytes) != digest {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        Ok(bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{engine::general_purpose::STANDARD, Engine};
    use ed25519_dalek::{Signer as _, SigningKey};
    use serde_json::Value;
    fn fixture() -> (Release, Vec<u8>, [u8; 32]) {
        let mut data: Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        for key in ["release_id", "withdrawn", "download_path"] {
            data["release"].as_object_mut().unwrap().remove(key);
        }
        let mut release: Release = serde_json::from_value(data["release"].clone()).unwrap();
        let signer = SigningKey::from_bytes(&[7; 32]);
        release.signature =
            STANDARD.encode(signer.sign(&release.signed_payload().unwrap()).to_bytes());
        (
            release,
            STANDARD
                .decode(data["archive_base64"].as_str().unwrap())
                .unwrap(),
            signer.verifying_key().to_bytes(),
        )
    }
    fn request<'a>(
        id: &'a str,
        release: &'a Release,
        archive: &'a [u8],
        key: &'a [u8; 32],
    ) -> Stage<'a> {
        Stage {
            operation_id: id,
            source: "https://catalog.example",
            signer: Signer {
                public_key: key,
                key_id: "test-key",
                namespace: "examples",
                revoked: false,
            },
            release,
            withdrawn: false,
            archive,
        }
    }
    #[test]
    fn staged_dependency_preview_survives_reopen_and_rechecks_archive_integrity() {
        let (mut release, archive, key) = fixture();
        let root = tempfile::tempdir().unwrap();
        let mut store = ExtensionStore::open(root.path()).unwrap();
        for version in ["1.0.0", "2.0.0"] {
            release.package_id = "dependency".into();
            release.version = version.into();
            release.signature = STANDARD.encode(
                SigningKey::from_bytes(&[7; 32])
                    .sign(&release.signed_payload().unwrap())
                    .to_bytes(),
            );
            store
                .stage(request(
                    &Uuid::new_v4().to_string(),
                    &release,
                    &archive,
                    &key,
                ))
                .unwrap();
        }
        release.package_id = "test-package".into();
        release.version = "1.0.0".into();
        release
            .dependencies
            .insert("dependency".into(), "^1.0.0".into());
        release.signature = STANDARD.encode(
            SigningKey::from_bytes(&[7; 32])
                .sign(&release.signed_payload().unwrap())
                .to_bytes(),
        );
        let receipt = store
            .stage(request(
                &Uuid::new_v4().to_string(),
                &release,
                &archive,
                &key,
            ))
            .unwrap();
        let plan = store
            .dependency_plan(&receipt.package_key, "0.3.0", "windows", "x86_64")
            .unwrap();
        assert_eq!(plan.packages.len(), 2);
        assert_eq!(plan.packages[0].package_id, "dependency");
        assert_eq!(plan.packages[0].version, "1.0.0");
        drop(store);
        let store = ExtensionStore::open(root.path()).unwrap();
        assert_eq!(
            plan.fingerprint,
            store
                .dependency_plan(&receipt.package_key, "0.3.0", "windows", "x86_64")
                .unwrap()
                .fingerprint
        );
        assert!(store
            .staged(0, 100)
            .unwrap()
            .iter()
            .all(|v| v.state == "staged"));
        fs::write(store.object_path(&release.sha256).unwrap(), b"changed").unwrap();
        assert!(store
            .dependency_plan(&receipt.package_key, "0.3.0", "windows", "x86_64")
            .is_err());
    }
    #[test]
    fn staging_recovers_each_durable_boundary_twenty_times() {
        let (release, archive, key) = fixture();
        for boundary in [
            "object_stored",
            "version_recorded",
            "receipt_recorded",
            "committed",
        ] {
            for _ in 0..20 {
                let root = tempfile::tempdir().unwrap();
                let mut store = ExtensionStore::open(root.path()).unwrap();
                let id = Uuid::new_v4().to_string();
                assert!(store
                    .stage_inner(request(&id, &release, &archive, &key), |point| {
                        if point == boundary {
                            Err(HostError::new("INJECTED_FAILURE"))
                        } else {
                            Ok(())
                        }
                    })
                    .is_err());
                drop(store);
                let mut store = ExtensionStore::open(root.path()).unwrap();
                let receipt = store.stage(request(&id, &release, &archive, &key)).unwrap();
                assert_eq!(receipt.state, "staged");
                let items = store.staged(0, 100).unwrap();
                assert_eq!(items.len(), 1);
                assert_eq!(items[0].package_key, receipt.package_key);
                assert_eq!(items[0].source, "https://catalog.example/");
                assert_eq!(items[0].state, "staged");
                assert!(store.staged(1, 100).unwrap().is_empty());
                assert_eq!(store.archive(&receipt.package_key).unwrap(), archive);
                assert_eq!(
                    store.stage(request(&id, &release, &archive, &key)).unwrap(),
                    receipt
                );
                assert_eq!(
                    store
                        .db
                        .query_row("SELECT COUNT(*) FROM versions", [], |r| r.get::<_, i64>(0))
                        .unwrap(),
                    1
                );
                assert_eq!(
                    store
                        .db
                        .query_row("SELECT COUNT(*) FROM stage_operations", [], |r| r
                            .get::<_, i64>(0))
                        .unwrap(),
                    1
                );
            }
        }
    }
    #[test]
    fn immutable_versions_and_revocation_are_not_bypassed_by_operation_replay() {
        let (release, archive, key) = fixture();
        let root = tempfile::tempdir().unwrap();
        let mut store = ExtensionStore::open(root.path()).unwrap();
        let id = Uuid::new_v4().to_string();
        let receipt = store.stage(request(&id, &release, &archive, &key)).unwrap();
        let mut revoked = request(&id, &release, &archive, &key);
        revoked.signer.revoked = true;
        assert_eq!(store.stage(revoked).unwrap_err().code, "EXTENSION_REVOKED");
        let mut changed = release.clone();
        changed.name = "Different signed release".into();
        changed.signature = STANDARD.encode(
            SigningKey::from_bytes(&[7; 32])
                .sign(&changed.signed_payload().unwrap())
                .to_bytes(),
        );
        assert_eq!(
            store
                .stage(request(&id, &changed, &archive, &key))
                .unwrap_err()
                .code,
            "OPERATION_REUSED"
        );
        assert_eq!(
            store
                .stage(request(
                    &Uuid::new_v4().to_string(),
                    &changed,
                    &archive,
                    &key
                ))
                .unwrap_err()
                .code,
            "EXTENSION_VERSION_IMMUTABLE"
        );
        fs::write(store.object_path(&release.sha256).unwrap(), b"corrupt").unwrap();
        assert!(store.archive(&receipt.package_key).is_err());
        assert!(store.stage(request(&id, &release, &archive, &key)).is_err());
    }
    #[test]
    fn store_ownership_and_preexisting_hardlinks_are_rejected() {
        let root = tempfile::tempdir().unwrap();
        let store = ExtensionStore::open(root.path()).unwrap();
        assert!(ExtensionStore::open(root.path()).is_err());
        drop(store);
        ExtensionStore::open(root.path()).unwrap();
        let outside = tempfile::NamedTempFile::new().unwrap();
        let linked = tempfile::tempdir().unwrap();
        fs::hard_link(outside.path(), linked.path().join("extensions.sqlite3")).unwrap();
        assert!(ExtensionStore::open(linked.path()).is_err());
        assert_eq!(outside.as_file().metadata().unwrap().len(), 0);
        for suffix in ["-wal", "-shm", "-journal"] {
            let linked = tempfile::tempdir().unwrap();
            fs::hard_link(
                outside.path(),
                linked.path().join(format!("extensions.sqlite3{suffix}")),
            )
            .unwrap();
            assert!(ExtensionStore::open(linked.path()).is_err());
        }
        assert!(source("https://user:password@example.com").is_err());
        assert!(source("https://example.com/?token=secret").is_err());
        assert_eq!(
            source("https://catalog.example").unwrap(),
            "https://catalog.example/"
        );
    }
}
