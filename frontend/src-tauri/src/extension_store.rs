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
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PreparedPackage {
    pub package_key: String,
    pub directory: String,
    pub tree_sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TrustSetting {
    pub source: String,
    pub source_id: String,
    pub namespace: String,
    pub key_id: String,
    pub public_key: [u8; 32],
    pub enabled: bool,
}
impl TrustSetting {
    pub fn fingerprint(&self) -> Result<String> {
        if source(&self.source)? != self.source
            || self.source_id.is_empty()
            || self.source_id.len() > 128
            || self.source_id.chars().any(char::is_control)
            || self.namespace.len() < 2
            || self.namespace.len() > 64
            || !self
                .namespace
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || !self.namespace.as_bytes()[0].is_ascii_alphanumeric()
            || self.key_id.is_empty()
            || self.key_id.len() > 128
            || self.key_id.chars().any(char::is_control)
            || ed25519_dalek::VerifyingKey::from_bytes(&self.public_key).is_err()
        {
            return Err(HostError::new("EXTENSION_TRUST_INVALID"));
        }
        Ok(hash(&serde_json::to_vec(self).unwrap()))
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallRequest {
    pub root_key: String,
    pub vault_id: String,
    pub app_version: String,
    pub platform: String,
    pub architecture: String,
    pub configurations: std::collections::BTreeMap<String, serde_json::Value>,
}
#[derive(Debug, Serialize)]
pub struct InstallPreview {
    pub fingerprint: String,
    pub dependencies: crate::extension_dependencies::Plan,
    pub changes: Vec<crate::extension_transaction::Change>,
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
        let mut db = Connection::open(database)?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")?;
        let version: i64 = db.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version > 6 {
            return Err(HostError::new("EXTENSION_SCHEMA_INCOMPATIBLE"));
        }
        if (1..6).contains(&version) {
            let backup = root.join(format!(
                "extensions.schema{version}.{}.sqlite3",
                Uuid::new_v4()
            ));
            db.execute("VACUUM INTO ?1", [backup.to_string_lossy().as_ref()])?;
            OpenOptions::new().write(true).open(backup)?.sync_all()?;
        }
        db.execute_batch("BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS versions (package_key TEXT PRIMARY KEY,source TEXT NOT NULL,namespace TEXT NOT NULL,package_id TEXT NOT NULL,version TEXT NOT NULL,fingerprint TEXT NOT NULL,release TEXT NOT NULL,manifest TEXT NOT NULL,inventory TEXT NOT NULL,archive_hash TEXT NOT NULL,size INTEGER NOT NULL,signer BLOB NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stage_operations (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,receipt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS prepared_packages (package_key TEXT PRIMARY KEY REFERENCES versions(package_key),directory TEXT NOT NULL UNIQUE,tree_sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS extension_active(slot TEXT PRIMARY KEY,target TEXT NOT NULL,revision TEXT NOT NULL,pending_operation TEXT);
            CREATE TABLE IF NOT EXISTS extension_transactions(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS extension_trust(source TEXT NOT NULL,namespace TEXT NOT NULL,key_id TEXT NOT NULL,setting TEXT NOT NULL,revision TEXT NOT NULL,PRIMARY KEY(source,namespace,key_id));
            CREATE TABLE IF NOT EXISTS extension_blocks(identity TEXT PRIMARY KEY,reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS extension_confirmations(operation_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,review_hash TEXT NOT NULL,changes TEXT NOT NULL);
            PRAGMA user_version=6; COMMIT;")?;
        crate::extension_transaction::recover(&mut db)?;
        Ok(Self {
            root,
            db,
            _lock: lock,
        })
    }
    pub fn trust_setting(
        &self,
        source_url: &str,
        namespace: &str,
        key_id: &str,
    ) -> Result<Option<TrustSetting>> {
        let row:Option<(String,String)>=self.db.query_row("SELECT setting,revision FROM extension_trust WHERE source=?1 AND namespace=?2 AND key_id=?3",
            params![source(source_url)?,namespace,key_id],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        row.map(|(json, revision)| {
            let setting: TrustSetting = serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            if setting.fingerprint()? != revision
                || setting.source != source(source_url)?
                || setting.namespace != namespace
                || setting.key_id != key_id
            {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            Ok(setting)
        })
        .transpose()
    }
    /// Called after the user confirms the displayed fingerprint. A concurrent
    /// setting change requires a fresh review; refresh never calls this method.
    pub fn confirm_trust(
        &mut self,
        setting: &TrustSetting,
        expected_revision: Option<&str>,
        confirmed_fingerprint: &str,
    ) -> Result<String> {
        let revision = setting.fingerprint()?;
        if confirmed_fingerprint != revision {
            return Err(HostError::new("EXTENSION_TRUST_CONFIRMATION"));
        }
        let old = self.trust_setting(&setting.source, &setting.namespace, &setting.key_id)?;
        let old_revision = old.as_ref().map(TrustSetting::fingerprint).transpose()?;
        if old.as_ref() == Some(setting) {
            return Ok(revision);
        }
        if old_revision.as_deref() != expected_revision {
            return Err(HostError::new("EXTENSION_TRUST_CONFLICT"));
        }
        // Prevent one canonical URL from silently acquiring a second source identity.
        let mut statement = self
            .db
            .prepare("SELECT setting FROM extension_trust WHERE source=?1")?;
        for row in statement.query_map([&setting.source], |r| r.get::<_, String>(0))? {
            let peer: TrustSetting = serde_json::from_str(&row?)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            if peer.source_id != setting.source_id {
                return Err(HostError::new("EXTENSION_TRUST_CHANGED"));
            }
        }
        drop(statement);
        self.db.execute("INSERT INTO extension_trust VALUES (?1,?2,?3,?4,?5) ON CONFLICT(source,namespace,key_id) DO UPDATE SET setting=excluded.setting,revision=excluded.revision",
            params![setting.source,setting.namespace,setting.key_id,serde_json::to_string(setting).unwrap(),revision])?;
        Ok(revision)
    }
    fn block_identity(
        source_url: &str,
        release: &Release,
        public_key: &[u8; 32],
        key: bool,
    ) -> Result<String> {
        let origin = source(source_url)?;
        let identity = if key {
            serde_json::json!([
                "key",
                origin,
                release.namespace,
                release.key_id,
                hash(public_key)
            ])
        } else {
            serde_json::json!([
                "release",
                origin,
                release.namespace,
                release.package_id,
                release.version
            ])
        };
        Ok(hash(&serde_json::to_vec(&identity).unwrap()))
    }
    /// A persisted denial is independent of rollback and renewed source consent.
    pub fn check_not_revoked(
        &self,
        source_url: &str,
        release: &Release,
        public_key: &[u8; 32],
    ) -> Result<()> {
        for key in [true, false] {
            let identity = Self::block_identity(source_url, release, public_key, key)?;
            let reason: Option<String> = self
                .db
                .query_row(
                    "SELECT reason FROM extension_blocks WHERE identity=?1",
                    [identity],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(reason) = reason {
                if !matches!(
                    reason.as_str(),
                    "EXTENSION_KEY_REVOKED" | "EXTENSION_RELEASE_WITHDRAWN"
                ) {
                    return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
                }
                return Err(HostError::new(&reason));
            }
        }
        Ok(())
    }
    fn remember_revocation(
        &mut self,
        source_url: &str,
        release: &Release,
        public_key: &[u8; 32],
        code: &str,
    ) -> Result<()> {
        let key = match code {
            "EXTENSION_KEY_REVOKED" => true,
            "EXTENSION_RELEASE_WITHDRAWN" => false,
            _ => return Ok(()),
        };
        let identity = Self::block_identity(source_url, release, public_key, key)?;
        self.db.execute(
            "INSERT INTO extension_blocks VALUES (?1,?2) ON CONFLICT(identity) DO NOTHING",
            params![identity, code],
        )?;
        Ok(())
    }
    /// Builds the complete consent payload; staging work is allowed, but active
    /// pointers, running instances and permissions are untouched.
    pub fn installation_preview(&mut self, request: &InstallRequest) -> Result<InstallPreview> {
        use crate::extension_transaction::{Change, Target};
        let vault = Uuid::parse_str(&request.vault_id)
            .map_err(|_| HostError::new("VAULT_INVALID"))?
            .to_string();
        if vault != request.vault_id || request.configurations.len() > 200 {
            return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
        }
        let dependencies = self.dependency_plan(
            &request.root_key,
            &request.app_version,
            &request.platform,
            &request.architecture,
        )?;
        for key in request.configurations.keys() {
            if !dependencies.packages.iter().any(|p| &p.package_key == key) {
                return Err(HostError::new("EXTENSION_CONFIG_INVALID"));
            }
        }
        let mut changes = Vec::new();
        let mut trust_revisions = Vec::new();
        for locked in &dependencies.packages {
            let (json, key): (String, Vec<u8>) = self.db.query_row(
                "SELECT release,signer FROM versions WHERE package_key=?1",
                [&locked.package_key],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            let release: Release = serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let key: [u8; 32] = key
                .try_into()
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            self.check_not_revoked(&locked.source, &release, &key)?;
            let trusted = self
                .trust_setting(&locked.source, &release.namespace, &release.key_id)?
                .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED"))?;
            if !trusted.enabled || trusted.public_key != key {
                return Err(HostError::new("EXTENSION_SOURCE_UNTRUSTED"));
            }
            trust_revisions.push(trusted.fingerprint()?);
            let configuration = request
                .configurations
                .get(&locked.package_key)
                .cloned()
                .unwrap_or_else(|| serde_json::json!({}));
            let archive = self.archive(&locked.package_key)?;
            let (_, manifest) = release.verify_package(
                &key,
                &release.key_id,
                &release.namespace,
                false,
                false,
                &archive,
            )?;
            crate::extension_config::validate(&manifest, &configuration)?;
            let prepared = self.prepare(
                &locked.package_key,
                Signer {
                    public_key: &key,
                    key_id: &release.key_id,
                    namespace: &release.namespace,
                    revoked: false,
                },
                false,
            )?;
            let slot = hash(
                &serde_json::to_vec(&(
                    &vault,
                    &locked.source,
                    &release.namespace,
                    &release.package_id,
                ))
                .unwrap(),
            );
            let current = self.active_installation(&slot)?;
            if current
                .as_ref()
                .is_some_and(|p| p.pending_operation.is_some())
            {
                return Err(HostError::new("EXTENSION_TRANSACTION_BUSY"));
            }
            changes.push(Change {
                target: Target {
                    slot,
                    package_key: locked.package_key.clone(),
                    directory: prepared.directory,
                    tree_sha256: prepared.tree_sha256,
                    configuration,
                },
                expected_revision: current.map(|p| p.revision),
            });
        }
        let bytes =
            serde_json::to_vec(&(request, &dependencies, &changes, trust_revisions)).unwrap();
        if bytes.len() > 4 * 1024 * 1024 {
            return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
        }
        Ok(InstallPreview {
            fingerprint: hash(&bytes),
            dependencies,
            changes,
        })
    }
    /// Recompute the exact reviewed payload before online checks. The main-window
    /// confirmation UI must supply this digest; this method alone is not consent.
    pub async fn install_confirmed(
        &mut self,
        operation: &str,
        request: &InstallRequest,
        confirmed_fingerprint: &str,
    ) -> Result<crate::extension_transaction::Receipt> {
        let changes = self.lock_confirmed_plan(operation, request, confirmed_fingerprint)?;
        self.switch_online(operation, &request.vault_id, &changes)
            .await
    }
    fn lock_confirmed_plan(
        &mut self,
        operation: &str,
        request: &InstallRequest,
        confirmed_fingerprint: &str,
    ) -> Result<Vec<crate::extension_transaction::Change>> {
        Uuid::parse_str(operation).map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        let request_bytes = serde_json::to_vec(request).unwrap();
        if request_bytes.len() > 4 * 1024 * 1024 {
            return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
        }
        let request_hash = hash(&request_bytes);
        let existing:Option<(String,String,String)>=self.db.query_row("SELECT request_hash,review_hash,changes FROM extension_confirmations WHERE operation_id=?1",[operation],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
        if let Some((old_request, old_review, json)) = existing {
            if old_request != request_hash || old_review != confirmed_fingerprint {
                return Err(HostError::new("OPERATION_REUSED"));
            }
            return serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        let preview = self.installation_preview(request)?;
        if preview.fingerprint != confirmed_fingerprint {
            return Err(HostError::new("EXTENSION_INSTALL_REVIEW_CHANGED"));
        }
        self.db.execute(
            "INSERT INTO extension_confirmations VALUES (?1,?2,?3,?4)",
            params![
                operation,
                request_hash,
                confirmed_fingerprint,
                serde_json::to_string(&preview.changes).unwrap()
            ],
        )?;
        Ok(preview.changes)
    }
    pub async fn stage_online(
        &mut self,
        operation: &str,
        source_url: &str,
        release: &Release,
    ) -> Result<Receipt> {
        self.stage_online_checked(operation, source_url, release, || Ok(()))
            .await
    }
    pub async fn stage_online_checked(
        &mut self,
        operation: &str,
        source_url: &str,
        release: &Release,
        checkpoint: impl Fn() -> Result<()>,
    ) -> Result<Receipt> {
        checkpoint()?;
        Uuid::parse_str(operation).map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        let origin = source(source_url)?;
        let trusted = self
            .trust_setting(&origin, &release.namespace, &release.key_id)?
            .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED"))?;
        if !trusted.enabled {
            return Err(HostError::new("EXTENSION_SOURCE_UNTRUSTED"));
        }
        self.check_not_revoked(&origin, release, &trusted.public_key)?;
        let client = crate::extension_trust::Client::new(&origin)?;
        let result = client
            .check(
                crate::extension_trust::Pin {
                    source_id: &trusted.source_id,
                    key_id: &trusted.key_id,
                    namespace: &trusted.namespace,
                    public_key: &trusted.public_key,
                },
                release,
            )
            .await;
        let checked = match result {
            Ok(checked) => checked,
            Err(error) => {
                self.remember_revocation(&origin, release, &trusted.public_key, &error.code)?;
                return Err(error);
            }
        };
        let archive = client
            .download(&checked, release, &trusted.public_key)
            .await?;
        self.stage_inner(
            Stage {
                operation_id: operation,
                source: &origin,
                release,
                archive: &archive,
                withdrawn: false,
                signer: Signer {
                    public_key: &trusted.public_key,
                    key_id: &trusted.key_id,
                    namespace: &trusted.namespace,
                    revoked: false,
                },
            },
            |_| checkpoint(),
        )
    }
    /// Online installation gate, using confirmed Host trust settings only.
    pub async fn switch_online(
        &mut self,
        operation: &str,
        vault_id: &str,
        changes: &[crate::extension_transaction::Change],
    ) -> Result<crate::extension_transaction::Receipt> {
        if changes.is_empty() || changes.len() > 200 {
            return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
        }
        let mut verified = Vec::new();
        let mut origin = None;
        for change in changes {
            let (source, json, key): (String, String, Vec<u8>) = self.db.query_row(
                "SELECT source,release,signer FROM versions WHERE package_key=?1",
                [&change.target.package_key],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )?;
            if origin.as_ref().is_some_and(|s| s != &source) {
                return Err(HostError::new("EXTENSION_SOURCE_INVALID"));
            }
            origin = Some(source.clone());
            let release: Release = serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let key: [u8; 32] = key
                .try_into()
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let trusted = self
                .trust_setting(&source, &release.namespace, &release.key_id)?
                .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED"))?;
            if !trusted.enabled || trusted.public_key != key {
                return Err(HostError::new("EXTENSION_SOURCE_UNTRUSTED"));
            }
            self.check_not_revoked(&source, &release, &key)?;
            let client = crate::extension_trust::Client::new(&source)?;
            let checked = client
                .check(
                    crate::extension_trust::Pin {
                        source_id: &trusted.source_id,
                        key_id: &release.key_id,
                        namespace: &release.namespace,
                        public_key: &key,
                    },
                    &release,
                )
                .await;
            let checked = match checked {
                Ok(checked) => checked,
                Err(error) => {
                    self.remember_revocation(&source, &release, &key, &error.code)?;
                    return Err(error);
                }
            };
            verified.push((checked, source, release, key));
        }
        self.switch_prepared_inner(operation, vault_id, changes, || {
            for (checked, source, release, key) in &verified {
                checked.matches(source, release, key)?;
            }
            Ok(())
        })
    }
    /// Atomically selects a prepared group after installer policy checks. This
    /// method does not stop processes, validate configuration schemas or issue permits.
    pub fn switch_prepared(
        &mut self,
        operation: &str,
        vault_id: &str,
        changes: &[crate::extension_transaction::Change],
    ) -> Result<crate::extension_transaction::Receipt> {
        self.switch_prepared_inner(operation, vault_id, changes, || Ok(()))
    }
    fn switch_prepared_inner(
        &mut self,
        operation: &str,
        vault_id: &str,
        changes: &[crate::extension_transaction::Change],
        check_fresh: impl FnOnce() -> Result<()>,
    ) -> Result<crate::extension_transaction::Receipt> {
        use cap_fs_ext::DirExt;
        let vault = Uuid::parse_str(vault_id)
            .map_err(|_| HostError::new("VAULT_INVALID"))?
            .to_string();
        if vault != vault_id || changes.is_empty() || changes.len() > 200 {
            return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
        }
        let root = cap_std::fs::Dir::open_ambient_dir(&self.root, cap_std::ambient_authority())?;
        let prepared = root.open_dir_nofollow("prepared")?;
        for change in changes {
            let (source, json, key, directory, tree): (String,String,Vec<u8>,String,String) = self.db.query_row(
                "SELECT v.source,v.release,v.signer,p.directory,p.tree_sha256 FROM versions v JOIN prepared_packages p ON p.package_key=v.package_key WHERE v.package_key=?1",
                [&change.target.package_key], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?)))?;
            let release: Release = serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let public: [u8; 32] = key
                .try_into()
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            self.check_not_revoked(&source, &release, &public)?;
            let (inventory, manifest) = release.verify_package(
                &public,
                &release.key_id,
                &release.namespace,
                false,
                false,
                &self.archive(&change.target.package_key)?,
            )?;
            crate::extension_config::validate(&manifest, &change.target.configuration)?;
            let slot = hash(
                &serde_json::to_vec(&(&vault, &source, &release.namespace, &release.package_id))
                    .unwrap(),
            );
            if change.target.slot != slot
                || change.target.directory != directory
                || change.target.tree_sha256 != tree
                || Uuid::parse_str(&directory)
                    .map(|v| v.to_string())
                    .ok()
                    .as_ref()
                    != Some(&directory)
            {
                return Err(HostError::new("EXTENSION_INSTALL_CONFLICT"));
            }
            if crate::extension_unpack::verify_tree(
                &prepared.open_dir_nofollow(&directory)?,
                &inventory,
            )? != tree
            {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
        }
        check_fresh()?;
        crate::extension_transaction::switch(&mut self.db, operation, changes)
    }
    pub fn finish_installation(
        &mut self,
        operation: &str,
        healthy: bool,
    ) -> Result<crate::extension_transaction::Receipt> {
        crate::extension_transaction::finish(&mut self.db, operation, healthy)
    }
    pub fn active_installation(
        &self,
        slot: &str,
    ) -> Result<Option<crate::extension_transaction::Active>> {
        crate::extension_transaction::active(&self.db, slot)
    }
    /// Prepare a verified staged package. The caller supplies current signer/revocation
    /// policy; persisted preparation does not bypass that policy on replay.
    pub fn prepare(
        &mut self,
        package_key: &str,
        signer: Signer<'_>,
        withdrawn: bool,
    ) -> Result<PreparedPackage> {
        self.prepare_inner(package_key, signer, withdrawn, |_| Ok(()))
    }
    fn prepare_inner(
        &mut self,
        package_key: &str,
        signer: Signer<'_>,
        withdrawn: bool,
        mut checkpoint: impl FnMut(&str) -> Result<()>,
    ) -> Result<PreparedPackage> {
        use cap_fs_ext::DirExt;
        let (json, stored_key): (String, Vec<u8>) = self.db.query_row(
            "SELECT release,signer FROM versions WHERE package_key=?1",
            [package_key],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        if stored_key.as_slice() != signer.public_key {
            return Err(HostError::new("EXTENSION_SIGNER_MISMATCH"));
        }
        let release: Release =
            serde_json::from_str(&json).map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
        let archive = self.archive(package_key)?;
        let (inventory, _) = release.verify_package(
            signer.public_key,
            signer.key_id,
            signer.namespace,
            signer.revoked,
            withdrawn,
            &archive,
        )?;
        let root = cap_std::fs::Dir::open_ambient_dir(&self.root, cap_std::ambient_authority())?;
        match root.create_dir("prepared") {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e.into()),
        }
        #[cfg(unix)]
        root.try_clone()?.into_std_file().sync_all()?;
        let staging = root.open_dir_nofollow("prepared")?;
        let existing: Option<(String, String)> = self
            .db
            .query_row(
                "SELECT directory,tree_sha256 FROM prepared_packages WHERE package_key=?1",
                [package_key],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if let Some((directory, tree_sha256)) = existing {
            // Database data never supplies an arbitrary relative path.
            if Uuid::parse_str(&directory)
                .map(|id| id.to_string())
                .ok()
                .as_ref()
                != Some(&directory)
            {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            let actual = crate::extension_unpack::verify_tree(
                &staging.open_dir_nofollow(&directory)?,
                &inventory,
            )?;
            if actual != tree_sha256 {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            return Ok(PreparedPackage {
                package_key: package_key.into(),
                directory,
                tree_sha256,
            });
        }
        let prepared = crate::extension_unpack::prepare(
            &staging,
            &release,
            signer.public_key,
            signer.key_id,
            signer.namespace,
            &archive,
        )?;
        checkpoint("tree_prepared")?;
        let receipt = PreparedPackage {
            package_key: package_key.into(),
            directory: prepared.directory,
            tree_sha256: prepared.tree_sha256,
        };
        let transaction = self.db.transaction()?;
        transaction.execute(
            "INSERT INTO prepared_packages VALUES (?1,?2,?3)",
            params![receipt.package_key, receipt.directory, receipt.tree_sha256],
        )?;
        checkpoint("preparation_recorded")?;
        transaction.commit()?;
        checkpoint("preparation_committed")?;
        Ok(receipt)
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
    pub fn stage_receipt(&self, operation: &str) -> Result<Option<Receipt>> {
        Uuid::parse_str(operation).map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        let json: Option<String> = self
            .db
            .query_row(
                "SELECT receipt FROM stage_operations WHERE id=?1",
                [operation],
                |r| r.get(0),
            )
            .optional()?;
        json.map(|v| {
            serde_json::from_str(&v).map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))
        })
        .transpose()
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
    fn cancelled_stage_reports_durable_commit_race_truthfully() {
        let (release, archive, key) = fixture();
        for boundary in ["object_stored", "receipt_recorded", "committed"] {
            let temp = tempfile::tempdir().unwrap();
            let mut store = ExtensionStore::open(temp.path()).unwrap();
            let operation = Uuid::new_v4().to_string();
            assert_eq!(
                store
                    .stage_inner(request(&operation, &release, &archive, &key), |at| {
                        if at == boundary {
                            Err(HostError::new("REQUEST_CANCELLED"))
                        } else {
                            Ok(())
                        }
                    })
                    .unwrap_err()
                    .code,
                "REQUEST_CANCELLED"
            );
            drop(store);
            let store = ExtensionStore::open(temp.path()).unwrap();
            assert_eq!(
                store.stage_receipt(&operation).unwrap().is_some(),
                boundary == "committed"
            );
        }
    }
    #[tokio::test]
    async fn installation_preview_binds_target_state_and_rejects_stale_confirmation_before_network()
    {
        let temp = tempfile::tempdir().unwrap();
        let (release, archive, key) = fixture();
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        let staged = store
            .stage(request(
                &Uuid::new_v4().to_string(),
                &release,
                &archive,
                &key,
            ))
            .unwrap();
        let setting = TrustSetting {
            source: "https://catalog.example/".into(),
            source_id: "catalog".into(),
            namespace: release.namespace.clone(),
            key_id: release.key_id.clone(),
            public_key: key,
            enabled: true,
        };
        store
            .confirm_trust(&setting, None, &setting.fingerprint().unwrap())
            .unwrap();
        let mut req = InstallRequest {
            root_key: staged.package_key,
            vault_id: Uuid::new_v4().to_string(),
            app_version: "1.0.0".into(),
            platform: "windows".into(),
            architecture: "x86_64".into(),
            configurations: Default::default(),
        };
        let preview = store.installation_preview(&req).unwrap();
        assert_eq!(
            preview.fingerprint,
            store.installation_preview(&req).unwrap().fingerprint
        );
        drop(store);
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        assert_eq!(
            preview.fingerprint,
            store.installation_preview(&req).unwrap().fingerprint
        );
        assert_eq!(
            store
                .install_confirmed(&Uuid::new_v4().to_string(), &req, "stale")
                .await
                .unwrap_err()
                .code,
            "EXTENSION_INSTALL_REVIEW_CHANGED"
        );
        req.app_version = "1.0.1".into();
        assert_ne!(
            preview.fingerprint,
            store.installation_preview(&req).unwrap().fingerprint
        );
        req.app_version = "1.0.0".into();
        let confirmed_operation = Uuid::new_v4().to_string();
        let locked = store
            .lock_confirmed_plan(&confirmed_operation, &req, &preview.fingerprint)
            .unwrap();
        let id = Uuid::new_v4().to_string();
        store
            .switch_prepared(&id, &req.vault_id, &preview.changes)
            .unwrap();
        assert!(store.installation_preview(&req).is_err());
        store.finish_installation(&id, true).unwrap();
        assert_eq!(
            locked,
            store
                .lock_confirmed_plan(&confirmed_operation, &req, &preview.fingerprint)
                .unwrap()
        );
        drop(store);
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        assert_eq!(
            locked,
            store
                .lock_confirmed_plan(&confirmed_operation, &req, &preview.fingerprint)
                .unwrap()
        );
        assert!(store
            .lock_confirmed_plan(&confirmed_operation, &req, "different")
            .is_err());
        assert_ne!(
            preview.fingerprint,
            store.installation_preview(&req).unwrap().fingerprint
        );
        assert_eq!(
            store
                .install_confirmed(&Uuid::new_v4().to_string(), &req, &preview.fingerprint)
                .await
                .unwrap_err()
                .code,
            "EXTENSION_INSTALL_REVIEW_CHANGED"
        );
        req.configurations
            .insert("unknown-package".into(), serde_json::json!({}));
        assert!(store.installation_preview(&req).is_err());
    }
    #[test]
    fn revocations_survive_restart_and_consent_without_overblocking_other_releases() {
        let temp = tempfile::tempdir().unwrap();
        let (release, _, key) = fixture();
        let source = "https://catalog.example/";
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        store
            .remember_revocation(source, &release, &key, "EXTENSION_TRUST_UNAVAILABLE")
            .unwrap();
        store.check_not_revoked(source, &release, &key).unwrap();
        store
            .remember_revocation(source, &release, &key, "EXTENSION_RELEASE_WITHDRAWN")
            .unwrap();
        drop(store);
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        let setting = TrustSetting {
            source: source.into(),
            source_id: "catalog".into(),
            namespace: release.namespace.clone(),
            key_id: release.key_id.clone(),
            public_key: key,
            enabled: true,
        };
        store
            .confirm_trust(&setting, None, &setting.fingerprint().unwrap())
            .unwrap();
        assert_eq!(
            store
                .check_not_revoked(source, &release, &key)
                .unwrap_err()
                .code,
            "EXTENSION_RELEASE_WITHDRAWN"
        );
        let mut next = release.clone();
        next.version = "2.0.0".into();
        store.check_not_revoked(source, &next, &key).unwrap();
        store
            .remember_revocation(source, &release, &key, "EXTENSION_KEY_REVOKED")
            .unwrap();
        assert_eq!(
            store
                .check_not_revoked(source, &next, &key)
                .unwrap_err()
                .code,
            "EXTENSION_KEY_REVOKED"
        );
        store
            .check_not_revoked("https://other.example/", &next, &key)
            .unwrap();
        let replacement = SigningKey::from_bytes(&[8; 32]).verifying_key().to_bytes();
        store
            .check_not_revoked(source, &next, &replacement)
            .unwrap();
        assert!(store
            .check_not_revoked(source, &release, &replacement)
            .is_err());
        drop(store);
        let store = ExtensionStore::open(temp.path()).unwrap();
        assert!(store.check_not_revoked(source, &next, &key).is_err());
    }
    #[tokio::test]
    async fn online_switch_never_trusts_the_staged_signer_implicitly() {
        let temp = tempfile::tempdir().unwrap();
        let (release, archive, key) = fixture();
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        let staged = store
            .stage(request(
                &Uuid::new_v4().to_string(),
                &release,
                &archive,
                &key,
            ))
            .unwrap();
        let changes = vec![crate::extension_transaction::Change {
            target: crate::extension_transaction::Target {
                slot: "a".repeat(64),
                package_key: staged.package_key,
                directory: Uuid::new_v4().to_string(),
                tree_sha256: "a".repeat(64),
                configuration: serde_json::json!({}),
            },
            expected_revision: None,
        }];
        let id = Uuid::new_v4().to_string();
        let vault = Uuid::new_v4().to_string();
        assert_eq!(
            store
                .switch_online(&id, &vault, &changes)
                .await
                .unwrap_err()
                .code,
            "EXTENSION_SOURCE_UNTRUSTED"
        );
        let mut setting = TrustSetting {
            source: "https://catalog.example/".into(),
            source_id: "catalog".into(),
            namespace: "examples".into(),
            key_id: "test-key".into(),
            public_key: key,
            enabled: false,
        };
        let disabled = setting.fingerprint().unwrap();
        store.confirm_trust(&setting, None, &disabled).unwrap();
        assert_eq!(
            store
                .switch_online(&id, &vault, &changes)
                .await
                .unwrap_err()
                .code,
            "EXTENSION_SOURCE_UNTRUSTED"
        );
        setting.enabled = true;
        setting.public_key = SigningKey::from_bytes(&[8; 32]).verifying_key().to_bytes();
        store
            .confirm_trust(&setting, Some(&disabled), &setting.fingerprint().unwrap())
            .unwrap();
        assert_eq!(
            store
                .switch_online(&id, &vault, &changes)
                .await
                .unwrap_err()
                .code,
            "EXTENSION_SOURCE_UNTRUSTED"
        );
        assert_eq!(
            store
                .db
                .query_row("SELECT COUNT(*) FROM extension_transactions", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
    }
    #[test]
    fn confirmed_trust_survives_reopen_and_rotation_requires_matching_review() {
        let temp = tempfile::tempdir().unwrap();
        let (_, _, key) = fixture();
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        let mut setting = TrustSetting {
            source: "https://catalog.example/".into(),
            source_id: "catalog".into(),
            namespace: "examples".into(),
            key_id: "test-key".into(),
            public_key: key,
            enabled: true,
        };
        let fingerprint = setting.fingerprint().unwrap();
        assert!(store
            .trust_setting(&setting.source, &setting.namespace, &setting.key_id)
            .unwrap()
            .is_none());
        assert!(store
            .confirm_trust(&setting, None, "not-confirmed")
            .is_err());
        store.confirm_trust(&setting, None, &fingerprint).unwrap();
        drop(store);
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        assert_eq!(
            store
                .trust_setting(&setting.source, &setting.namespace, &setting.key_id)
                .unwrap(),
            Some(setting.clone())
        );
        setting.public_key = SigningKey::from_bytes(&[8; 32]).verifying_key().to_bytes();
        let rotated = setting.fingerprint().unwrap();
        assert!(store.confirm_trust(&setting, None, &rotated).is_err());
        assert!(store
            .confirm_trust(&setting, Some(&fingerprint), &fingerprint)
            .is_err());
        store
            .confirm_trust(&setting, Some(&fingerprint), &rotated)
            .unwrap();
        setting.enabled = false;
        let disabled = setting.fingerprint().unwrap();
        store
            .confirm_trust(&setting, Some(&rotated), &disabled)
            .unwrap();
        assert!(
            !store
                .trust_setting(&setting.source, &setting.namespace, &setting.key_id)
                .unwrap()
                .unwrap()
                .enabled
        );
        setting.key_id = "another-key".into();
        setting.source_id = "different".into();
        assert!(store
            .confirm_trust(&setting, None, &setting.fingerprint().unwrap())
            .is_err());
    }
    #[test]
    fn prepared_switch_rechecks_content_vault_binding_and_recovers_on_open() {
        use crate::extension_transaction::{Change, Target};
        let temp = tempfile::tempdir().unwrap();
        let (release, archive, key) = fixture();
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        let staged = store
            .stage(request(
                &Uuid::new_v4().to_string(),
                &release,
                &archive,
                &key,
            ))
            .unwrap();
        let prepared = store
            .prepare(
                &staged.package_key,
                Signer {
                    public_key: &key,
                    key_id: "test-key",
                    namespace: "examples",
                    revoked: false,
                },
                false,
            )
            .unwrap();
        let vault = Uuid::new_v4().to_string();
        let slot = hash(
            &serde_json::to_vec(&(
                &vault,
                "https://catalog.example/",
                &release.namespace,
                &release.package_id,
            ))
            .unwrap(),
        );
        let changes = vec![Change {
            target: Target {
                slot: slot.clone(),
                package_key: staged.package_key,
                directory: prepared.directory.clone(),
                tree_sha256: prepared.tree_sha256,
                configuration: serde_json::json!({}),
            },
            expected_revision: None,
        }];
        assert!(store
            .switch_prepared(
                &Uuid::new_v4().to_string(),
                &Uuid::new_v4().to_string(),
                &changes
            )
            .is_err());
        let mut invalid = changes.clone();
        invalid[0].target.configuration = serde_json::json!({"password":"never-persist-this"});
        assert!(store
            .switch_prepared(&Uuid::new_v4().to_string(), &vault, &invalid)
            .is_err());
        assert_eq!(
            store
                .db
                .query_row("SELECT COUNT(*) FROM extension_transactions", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
        let operation = Uuid::new_v4().to_string();
        store.switch_prepared(&operation, &vault, &changes).unwrap();
        assert!(store
            .active_installation(&slot)
            .unwrap()
            .unwrap()
            .pending_operation
            .is_some());
        drop(store);
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        assert!(store.active_installation(&slot).unwrap().is_none());
        assert_eq!(
            store.finish_installation(&operation, true).unwrap().state,
            "rolled_back"
        );
        let operation = Uuid::new_v4().to_string();
        store.switch_prepared(&operation, &vault, &changes).unwrap();
        store.finish_installation(&operation, true).unwrap();
        drop(store);
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        assert_eq!(
            store.active_installation(&slot).unwrap().unwrap().target,
            changes[0].target
        );
        fs::write(
            temp.path()
                .join("prepared")
                .join(&prepared.directory)
                .join("persona.json"),
            b"bad",
        )
        .unwrap();
        assert!(store.switch_prepared(&operation, &vault, &changes).is_err());
    }
    #[test]
    fn schema_one_upgrade_preserves_versions_and_creates_readable_backup() {
        let temp = tempfile::tempdir().unwrap();
        let (release, archive, key) = fixture();
        let mut store = ExtensionStore::open(temp.path()).unwrap();
        let receipt = store
            .stage(request(
                &Uuid::new_v4().to_string(),
                &release,
                &archive,
                &key,
            ))
            .unwrap();
        store
            .db
            .execute_batch("DROP TABLE prepared_packages; PRAGMA user_version=1;")
            .unwrap();
        drop(store);
        let store = ExtensionStore::open(temp.path()).unwrap();
        assert_eq!(store.archive(&receipt.package_key).unwrap(), archive);
        let backups: Vec<_> = fs::read_dir(temp.path())
            .unwrap()
            .map(|e| e.unwrap().path())
            .filter(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("extensions.schema1.")
            })
            .collect();
        assert_eq!(backups.len(), 1);
        let backup = Connection::open(&backups[0]).unwrap();
        assert_eq!(
            backup
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert_eq!(
            backup
                .query_row("SELECT COUNT(*) FROM versions", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
        drop(backup);
        drop(store);
        let _store = ExtensionStore::open(temp.path()).unwrap();
        assert_eq!(
            fs::read_dir(temp.path())
                .unwrap()
                .filter(|e| e
                    .as_ref()
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with("extensions.schema1."))
                .count(),
            1
        );
    }
    #[test]
    fn preparation_replays_checks_policy_and_recovers_each_boundary() {
        let (release, archive, key) = fixture();
        for boundary in [
            "tree_prepared",
            "preparation_recorded",
            "preparation_committed",
        ] {
            for _ in 0..20 {
                let root = tempfile::tempdir().unwrap();
                let mut store = ExtensionStore::open(root.path()).unwrap();
                let staged = store
                    .stage(request(
                        &Uuid::new_v4().to_string(),
                        &release,
                        &archive,
                        &key,
                    ))
                    .unwrap();
                let signer = || Signer {
                    public_key: &key,
                    key_id: "test-key",
                    namespace: "examples",
                    revoked: false,
                };
                assert!(store
                    .prepare_inner(&staged.package_key, signer(), false, |at| {
                        if at == boundary {
                            Err(HostError::new("INJECTED"))
                        } else {
                            Ok(())
                        }
                    })
                    .is_err());
                drop(store);
                let mut store = ExtensionStore::open(root.path()).unwrap();
                let prepared = store.prepare(&staged.package_key, signer(), false).unwrap();
                assert_eq!(
                    prepared,
                    store.prepare(&staged.package_key, signer(), false).unwrap()
                );
                assert_eq!(
                    store
                        .db
                        .query_row("SELECT COUNT(*) FROM prepared_packages", [], |r| r
                            .get::<_, i64>(0))
                        .unwrap(),
                    1
                );
                assert!(store.prepare(&staged.package_key, signer(), true).is_err());
                let mut revoked = signer();
                revoked.revoked = true;
                assert!(store.prepare(&staged.package_key, revoked, false).is_err());
                fs::write(
                    root.path()
                        .join("prepared")
                        .join(&prepared.directory)
                        .join("persona.json"),
                    b"changed",
                )
                .unwrap();
                assert!(store.prepare(&staged.package_key, signer(), false).is_err());
            }
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
