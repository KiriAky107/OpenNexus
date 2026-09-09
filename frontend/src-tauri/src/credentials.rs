//! Device-local Stronghold broker. No public IPC returns secret bytes.
//!
//! Stronghold Store contains AEAD ciphertext, including while unlocked. Snapshot
//! and salt are one atomic envelope, so password changes cannot tear two files.
use argon2::{Algorithm, Argon2, Params, Version};
use chacha20poly1305::{
    aead::{Aead, Payload},
    ChaCha20Poly1305, KeyInit, Nonce,
};
use iota_stronghold::{KeyProvider, SnapshotPath, Stronghold};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use zeroize::Zeroizing;

type Result<T> = std::result::Result<T, String>;
const CLIENT: &[u8] = b"opennexus.credentials.v1";
const MAGIC: &[u8] = b"ONXCRED1";
const MAX_FILE: u64 = 16 * 1024 * 1024;

#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "owner", rename_all = "snake_case")]
pub enum Scope {
    Provider,
    Plugin(String),
    Mcp(String),
    Sync(String),
}

#[derive(Clone, Serialize, Deserialize)]
pub struct CredentialId {
    pub scope: Scope,
    pub id: String,
}

impl CredentialId {
    /// Preserve opaque legacy references. Hashed Plugin/MCP IDs remain isolated
    /// from Provider IDs; only the trusted Core adapter can use these aliases.
    pub fn legacy(id: &str) -> Self {
        let scope = if let Some(owner) = id.strip_prefix("plugin.") {
            Scope::Plugin(owner.into())
        } else if let Some(owner) = id.strip_prefix("mcp.") {
            Scope::Mcp(owner.into())
        } else {
            Scope::Provider
        };
        Self {
            scope,
            id: id.into(),
        }
    }
    fn key(&self) -> Result<Vec<u8>> {
        fn valid(value: &str) -> bool {
            !value.is_empty()
                && value.len() <= 128
                && value
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
        }
        if !valid(&self.id) {
            return Err("CREDENTIAL_ID_INVALID".into());
        }
        match &self.scope {
            Scope::Provider
                if self.id.to_lowercase().starts_with("plugin.")
                    || self.id.to_lowercase().starts_with("mcp.") =>
            {
                return Err("CREDENTIAL_SCOPE_DENIED".into())
            }
            Scope::Plugin(owner) | Scope::Mcp(owner) | Scope::Sync(owner) if !valid(owner) => {
                return Err("CREDENTIAL_SCOPE_DENIED".into())
            }
            _ => {}
        }
        serde_json::to_vec(self).map_err(|_| "CREDENTIAL_ID_INVALID".into())
    }
}

struct Unlocked {
    stronghold: Stronghold,
    key: Zeroizing<Vec<u8>>,
    salt: [u8; 32],
}

impl Drop for Unlocked {
    fn drop(&mut self) {
        let _ = self.stronghold.clear();
    }
}

impl Unlocked {
    fn derive(password: &[u8], salt: [u8; 32]) -> Result<Self> {
        if password.len() < 12 || password.len() > 1024 {
            return Err("CREDENTIAL_PASSWORD_LENGTH".into());
        }
        let params = Params::new(65536, 3, 1, Some(32)).map_err(|_| "CREDENTIAL_KDF_FAILED")?;
        let mut key = Zeroizing::new(vec![0; 32]);
        Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
            .hash_password_into(password, &salt, &mut key)
            .map_err(|_| "CREDENTIAL_KDF_FAILED")?;
        Ok(Self {
            stronghold: Stronghold::default(),
            key,
            salt,
        })
    }
    fn cipher(&self) -> Result<ChaCha20Poly1305> {
        let mut hash = Sha256::new();
        hash.update(self.key.as_slice());
        hash.update(b"opennexus.credential-record.v1");
        let key = Zeroizing::new(hash.finalize().to_vec());
        ChaCha20Poly1305::new_from_slice(&key).map_err(|_| "CREDENTIAL_CIPHER_FAILED".into())
    }
    fn provider(&self) -> Result<KeyProvider> {
        KeyProvider::try_from(Zeroizing::new(self.key.to_vec()))
            .map_err(|_| "CREDENTIAL_KDF_FAILED".into())
    }
    fn store(&self) -> Result<iota_stronghold::Store> {
        self.stronghold
            .get_client(CLIENT)
            .map(|c| c.store())
            .map_err(|_| "CREDENTIAL_STORE_FAILED".into())
    }
    fn read(&self, key: &[u8]) -> Result<Option<Zeroizing<Vec<u8>>>> {
        let Some(data) = self
            .store()?
            .get(key)
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?
        else {
            return Ok(None);
        };
        if data.len() < 28 {
            return Err("CREDENTIAL_STORE_CORRUPT".into());
        }
        self.cipher()?
            .decrypt(
                Nonce::from_slice(&data[..12]),
                Payload {
                    msg: &data[12..],
                    aad: key,
                },
            )
            .map(Zeroizing::new)
            .map(Some)
            .map_err(|_| "CREDENTIAL_STORE_CORRUPT".into())
    }
    fn write(&self, key: Vec<u8>, value: &[u8]) -> Result<()> {
        if value.is_empty() || value.len() > 65536 {
            return Err("CREDENTIAL_VALUE_INVALID".into());
        }
        let mut nonce = [0u8; 12];
        rand::rngs::OsRng
            .try_fill_bytes(&mut nonce)
            .map_err(|_| "CREDENTIAL_ENTROPY_FAILED")?;
        let ciphertext = self
            .cipher()?
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: value,
                    aad: &key,
                },
            )
            .map_err(|_| "CREDENTIAL_CIPHER_FAILED")?;
        let mut record = nonce.to_vec();
        record.extend(ciphertext);
        self.store()?
            .insert(key, record, None)
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
        Ok(())
    }
    fn persist(&self, path: &Path) -> Result<()> {
        let parent = path.parent().ok_or("CREDENTIAL_PATH_INVALID")?;
        fs::create_dir_all(parent).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        let staging = tempfile::tempdir_in(parent).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        let snapshot = staging.path().join("snapshot");
        self.stronghold
            .write_client(CLIENT)
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
        self.stronghold
            .commit_with_keyprovider(&SnapshotPath::from_path(&snapshot), &self.provider()?)
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
        let bytes = fs::read(&snapshot).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        let mut target =
            tempfile::NamedTempFile::new_in(parent).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        target
            .write_all(MAGIC)
            .and_then(|_| target.write_all(&self.salt))
            .and_then(|_| target.write_all(&bytes))
            .and_then(|_| target.as_file().sync_all())
            .map_err(|_| "CREDENTIAL_IO_FAILED")?;
        target.persist(path).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        Ok(())
    }
}

pub struct CredentialBroker {
    path: PathBuf,
    unlocked: Option<Unlocked>,
    // Separate stable inode: snapshots are atomically replaced, so locking the
    // snapshot itself would not protect the next writer after replacement.
    ownership: Option<fs::File>,
    lock_epoch: Arc<AtomicU64>,
    unlocked_epoch: u64,
}

impl CredentialBroker {
    /// Source comes from the native file picker, never a raw WebView path.
    /// Import is idempotent; conflicting IDs stop the entire transaction.
    pub fn import_fernet(
        &mut self,
        directory: &Path,
        environment_key: Option<Zeroizing<String>>,
    ) -> Result<usize> {
        use fs2::FileExt;
        let directory = directory
            .canonicalize()
            .map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        let source = directory.join("credentials.json");
        let key_path = directory.join("master.key");
        if !fs::symlink_metadata(&source)
            .map_err(|_| "MIGRATION_SOURCE_INVALID")?
            .is_file()
        {
            return Err("MIGRATION_SOURCE_INVALID".into());
        }
        let lock = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(directory.join(".migration.lock"))
            .map_err(|_| "MIGRATION_SOURCE_BUSY")?;
        lock.try_lock_exclusive()
            .map_err(|_| "MIGRATION_SOURCE_BUSY")?;
        if fs::metadata(&source)
            .map_err(|_| "MIGRATION_SOURCE_INVALID")?
            .len()
            > MAX_FILE
        {
            return Err("MIGRATION_SOURCE_INVALID".into());
        }
        let source_bytes = fs::read(&source).map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        let source_hash = format!("{:x}", Sha256::digest(&source_bytes));
        let tokens: BTreeMap<String, String> =
            serde_json::from_slice(&source_bytes).map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        if tokens.len() > 10000 {
            return Err("MIGRATION_SOURCE_INVALID".into());
        }
        let local_key = if environment_key.is_none() {
            if !fs::symlink_metadata(&key_path)
                .map_err(|_| "MIGRATION_KEY_MISSING")?
                .is_file()
            {
                return Err("MIGRATION_KEY_MISSING".into());
            }
            Some(Zeroizing::new(
                fs::read_to_string(&key_path).map_err(|_| "MIGRATION_KEY_MISSING")?,
            ))
        } else {
            None
        };
        let key = environment_key
            .as_ref()
            .or(local_key.as_ref())
            .ok_or("MIGRATION_KEY_MISSING")?;
        let fernet =
            Zeroizing::new(fernet::Fernet::new(key.trim()).ok_or("MIGRATION_KEY_INVALID")?);
        let session = self.session()?;
        let mut decoded = Vec::new();
        for (id, token) in &tokens {
            let key = CredentialId::legacy(id).key()?;
            let value = Zeroizing::new(
                fernet
                    .decrypt(token)
                    .map_err(|_| "MIGRATION_DECRYPT_FAILED")?,
            );
            if value.is_empty() || value.len() > 65536 || std::str::from_utf8(&value).is_err() {
                return Err("MIGRATION_VALUE_INVALID".into());
            }
            if let Some(existing) = session.read(&key)? {
                if existing.as_slice() != value.as_slice() {
                    return Err("MIGRATION_CONFLICT".into());
                }
            }
            decoded.push((key, value));
        }
        let migration_id = format!(
            "{:x}",
            Sha256::digest(directory.to_string_lossy().as_bytes())
        );
        let backup = self
            .path
            .parent()
            .ok_or("CREDENTIAL_PATH_INVALID")?
            .join("migration-backups")
            .join(&migration_id);
        fs::create_dir_all(&backup).map_err(|_| "MIGRATION_BACKUP_FAILED")?;
        // Backups contain ciphertext; the legacy key is sealed under the already
        // unlocked device key, rather than adding another plaintext master.key.
        let mut nonce = [0u8; 12];
        rand::rngs::OsRng
            .try_fill_bytes(&mut nonce)
            .map_err(|_| "CREDENTIAL_ENTROPY_FAILED")?;
        let sealed_key = session
            .cipher()?
            .encrypt(Nonce::from_slice(&nonce), key.as_bytes())
            .map_err(|_| "MIGRATION_BACKUP_FAILED")?;
        let mut key_backup = b"ONXFBK1".to_vec();
        key_backup.extend(session.salt);
        key_backup.extend(nonce);
        key_backup.extend(sealed_key);
        for (name, bytes) in [
            ("credentials.json", source_bytes.as_slice()),
            ("master-key.sealed", key_backup.as_slice()),
        ] {
            let mut temporary =
                tempfile::NamedTempFile::new_in(&backup).map_err(|_| "MIGRATION_BACKUP_FAILED")?;
            temporary
                .write_all(bytes)
                .and_then(|_| temporary.as_file().sync_all())
                .map_err(|_| "MIGRATION_BACKUP_FAILED")?;
            temporary
                .persist(backup.join(name))
                .map_err(|_| "MIGRATION_BACKUP_FAILED")?;
        }
        let result = (|| {
            for (key, value) in &decoded {
                session.write(key.clone(), value)?;
            }
            session.persist(&self.path)?;
            // Re-open the committed Stronghold snapshot, not the in-memory cache.
            let envelope = fs::read(&self.path).map_err(|_| "MIGRATION_VERIFY_FAILED")?;
            let mut temporary =
                tempfile::NamedTempFile::new_in(&backup).map_err(|_| "MIGRATION_VERIFY_FAILED")?;
            temporary
                .write_all(&envelope[40..])
                .map_err(|_| "MIGRATION_VERIFY_FAILED")?;
            let verified = Unlocked {
                stronghold: Stronghold::default(),
                key: Zeroizing::new(session.key.to_vec()),
                salt: session.salt,
            };
            verified
                .stronghold
                .load_client_from_snapshot(
                    CLIENT,
                    &verified.provider()?,
                    &SnapshotPath::from_path(temporary.path()),
                )
                .map_err(|_| "MIGRATION_VERIFY_FAILED")?;
            for (key, value) in &decoded {
                if verified.read(key)?.as_deref().map(|v| v.as_slice()) != Some(value.as_slice()) {
                    return Err("MIGRATION_VERIFY_FAILED".into());
                }
            }
            if fs::read(&source).map_err(|_| "MIGRATION_SOURCE_CHANGED")? != source_bytes {
                return Err("MIGRATION_SOURCE_CHANGED".into());
            }
            let marker = serde_json::json!({"schema":1,"owner":"OpenNexus","state":"switched","source_sha256":source_hash,"count":decoded.len(),"environment_key":environment_key.is_some()});
            let bytes = serde_json::to_vec(&marker).map_err(|_| "MIGRATION_VERIFY_FAILED")?;
            let mut marker_file = tempfile::NamedTempFile::new_in(&directory)
                .map_err(|_| "MIGRATION_SWITCH_FAILED")?;
            marker_file
                .write_all(&bytes)
                .and_then(|_| marker_file.as_file().sync_all())
                .map_err(|_| "MIGRATION_SWITCH_FAILED")?;
            marker_file
                .persist(directory.join(".opennexus-owner.json"))
                .map_err(|_| "MIGRATION_SWITCH_FAILED")?;
            Ok(decoded.len())
        })();
        if result.is_err() {
            self.lock();
        }
        result
    }
    pub fn dispatch(&mut self, request: &serde_json::Value) -> Result<serde_json::Value> {
        let method = request["rpc"].as_str().ok_or("HOST_REQUEST_INVALID")?;
        let params = &request["params"];
        if method == "credentials.delete_many" || method == "credentials.move_many" {
            let session = self.session()?;
            let result = (|| {
                let mut removed = Vec::new();
                if method.ends_with("delete_many") {
                    let ids = params["ids"].as_array().ok_or("HOST_REQUEST_INVALID")?;
                    let keys: Vec<_> = ids
                        .iter()
                        .map(|id| {
                            let id = id.as_str().ok_or("HOST_REQUEST_INVALID")?;
                            Ok((id.to_string(), CredentialId::legacy(id).key()?))
                        })
                        .collect::<Result<_>>()?;
                    for (id, key) in keys {
                        if session
                            .store()?
                            .delete(&key)
                            .map_err(|_| "CREDENTIAL_STORE_FAILED")?
                            .is_some()
                        {
                            removed.push(id);
                        }
                    }
                } else {
                    let replacements = params["replacements"]
                        .as_object()
                        .ok_or("HOST_REQUEST_INVALID")?;
                    let mut moves = Vec::new();
                    for (old, new) in replacements {
                        let source = CredentialId::legacy(old);
                        let target =
                            CredentialId::legacy(new.as_str().ok_or("HOST_REQUEST_INVALID")?);
                        // ID migrations cannot change Provider/Plugin/MCP families.
                        if std::mem::discriminant(&source.scope)
                            != std::mem::discriminant(&target.scope)
                        {
                            return Err("CREDENTIAL_SCOPE_DENIED".into());
                        }
                        let source_key = source.key()?;
                        let target_key = target.key()?;
                        if let Some(value) = session.read(&source_key)? {
                            if let Some(existing) = session.read(&target_key)? {
                                if existing.as_slice() != value.as_slice() {
                                    return Err("MIGRATION_CONFLICT".into());
                                }
                            }
                            moves.push((source_key, target_key, value));
                        }
                    }
                    // Reject cycles/overlapping source+destination rather than deleting
                    // a newly written value midway through a multi-ID migration.
                    if moves.iter().any(|(old, new, _)| {
                        old != new && moves.iter().any(|(source, _, _)| source == new)
                    }) {
                        return Err("MIGRATION_CONFLICT".into());
                    }
                    for (old, new, value) in moves {
                        session.write(new.clone(), &value)?;
                        if old != new {
                            session
                                .store()?
                                .delete(&old)
                                .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
                        }
                    }
                }
                session.persist(&self.path)?;
                Ok(serde_json::json!(removed))
            })();
            if result.is_err() {
                self.lock();
            }
            return result;
        }
        let id = CredentialId::legacy(params["id"].as_str().ok_or("HOST_REQUEST_INVALID")?);
        match method {
            "credentials.resolve" => self
                .resolve(&id.scope, &id)?
                .map(|v| {
                    String::from_utf8(v.to_vec())
                        .map(serde_json::Value::String)
                        .map_err(|_| "CREDENTIAL_ENCODING_INVALID".into())
                })
                .unwrap_or(Ok(serde_json::Value::Null)),
            "credentials.has" => Ok(serde_json::json!(self.resolve(&id.scope, &id)?.is_some())),
            "credentials.put" => {
                let value = params["secret"].as_str().ok_or("HOST_REQUEST_INVALID")?;
                self.put(&id, Zeroizing::new(value.as_bytes().to_vec()))?;
                Ok(serde_json::Value::Null)
            }
            "credentials.delete" => {
                let existed = self.resolve(&id.scope, &id)?.is_some();
                self.delete(&id)?;
                Ok(serde_json::json!(existed))
            }
            _ => Err("HOST_METHOD_DENIED".into()),
        }
    }
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            unlocked: None,
            ownership: None,
            lock_epoch: Arc::new(AtomicU64::new(0)),
            unlocked_epoch: 0,
        }
    }
    pub fn is_locked(&self) -> bool {
        self.unlocked.is_none() || self.lock_epoch.load(Ordering::SeqCst) != self.unlocked_epoch
    }
    pub fn lock_signal(&self) -> Arc<AtomicU64> {
        self.lock_epoch.clone()
    }
    fn session(&self) -> Result<&Unlocked> {
        if self.is_locked() {
            return Err("CREDENTIALS_LOCKED".into());
        }
        self.unlocked.as_ref().ok_or("CREDENTIALS_LOCKED".into())
    }
    pub fn lock(&mut self) {
        self.lock_epoch.fetch_add(1, Ordering::SeqCst);
        self.unlocked.take();
        self.ownership.take();
    }
    pub fn unlock(&mut self, password: Zeroizing<Vec<u8>>) -> Result<()> {
        self.lock();
        let epoch = self.lock_epoch.load(Ordering::SeqCst);
        let ownership = Self::acquire_ownership(&self.path)?;
        let session = if self.path.exists() {
            Self::load_snapshot(&self.path, &password)?
        } else {
            let mut salt = [0u8; 32];
            rand::rngs::OsRng
                .try_fill_bytes(&mut salt)
                .map_err(|_| "CREDENTIAL_ENTROPY_FAILED")?;
            let session = Unlocked::derive(&password, salt)?;
            session
                .stronghold
                .create_client(CLIENT)
                .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
            session.persist(&self.path)?;
            session
        };
        if self.lock_epoch.load(Ordering::SeqCst) != epoch {
            return Err("CREDENTIALS_LOCKED".into());
        }
        self.unlocked_epoch = epoch;
        self.unlocked = Some(session);
        self.ownership = Some(ownership);
        Ok(())
    }
    fn acquire_ownership(path: &Path) -> Result<fs::File> {
        use fs2::FileExt;
        let parent = path.parent().ok_or("CREDENTIAL_PATH_INVALID")?;
        fs::create_dir_all(parent).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        let mut lock_name = path
            .file_name()
            .ok_or("CREDENTIAL_PATH_INVALID")?
            .to_os_string();
        lock_name.push(".lock");
        let lock_path = parent.join(lock_name);
        if let Ok(metadata) = fs::symlink_metadata(&lock_path) {
            if !metadata.is_file() || metadata.file_type().is_symlink() {
                return Err("CREDENTIAL_PATH_INVALID".into());
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if metadata.file_attributes() & 0x400 != 0 {
                    return Err("CREDENTIAL_PATH_INVALID".into());
                }
            }
        }
        let mut options = fs::OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.share_mode(0x1 | 0x2); // Do not allow replacing the held lock file.
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let ownership = options
            .open(&lock_path)
            .map_err(|_| "CREDENTIAL_IO_FAILED")?;
        ownership
            .try_lock_exclusive()
            .map_err(|_| "CREDENTIALS_BUSY")?;
        Ok(ownership)
    }
    fn load_snapshot(path: &Path, password: &[u8]) -> Result<Unlocked> {
        let metadata = fs::symlink_metadata(path).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        if !metadata.is_file() || metadata.len() > MAX_FILE {
            return Err("CREDENTIAL_STORE_CORRUPT".into());
        }
        let data = fs::read(path).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        if data.len() < 40 || &data[..8] != MAGIC {
            return Err("SCHEMA_INCOMPATIBLE".into());
        }
        let mut salt = [0u8; 32];
        salt.copy_from_slice(&data[8..40]);
        let session = Unlocked::derive(password, salt)?;
        // Backups can be on read-only media. This temporary file contains ciphertext only.
        let mut temp = tempfile::NamedTempFile::new().map_err(|_| "CREDENTIAL_IO_FAILED")?;
        temp.write_all(&data[40..])
            .map_err(|_| "CREDENTIAL_IO_FAILED")?;
        session
            .stronghold
            .load_client_from_snapshot(
                CLIENT,
                &session.provider()?,
                &SnapshotPath::from_path(temp.path()),
            )
            .map_err(|_| "CREDENTIAL_UNLOCK_FAILED")?;
        Ok(session)
    }

    /// Native picker selected destination; backup is encrypted and never overwrites.
    pub fn backup(&self, destination: &Path) -> Result<()> {
        self.session()?;
        let parent = destination.parent().ok_or("CREDENTIAL_PATH_INVALID")?;
        let mut target =
            tempfile::NamedTempFile::new_in(parent).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        let bytes = fs::read(&self.path).map_err(|_| "CREDENTIAL_IO_FAILED")?;
        target
            .write_all(&bytes)
            .and_then(|_| target.as_file().sync_all())
            .map_err(|_| "CREDENTIAL_IO_FAILED")?;
        target
            .persist_noclobber(destination)
            .map_err(|_| "CREDENTIAL_BACKUP_EXISTS")?;
        Ok(())
    }

    /// Validate every record before atomic replacement; preserve the previous encrypted file.
    /// Caller must obtain explicit confirmation through the native dialog.
    pub fn restore(&mut self, source: &Path, password: Zeroizing<Vec<u8>>) -> Result<usize> {
        if !self.is_locked() {
            return Err("CREDENTIALS_MUST_LOCK".into());
        }
        self.lock();
        let _ownership = Self::acquire_ownership(&self.path)?;
        let session = Self::load_snapshot(source, &password)?;
        let keys = session
            .store()?
            .keys()
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
        for key in &keys {
            let id: CredentialId =
                serde_json::from_slice(key).map_err(|_| "CREDENTIAL_STORE_CORRUPT")?;
            if id.key()? != *key || session.read(key)?.is_none() {
                return Err("CREDENTIAL_STORE_CORRUPT".into());
            }
        }
        let parent = self.path.parent().ok_or("CREDENTIAL_PATH_INVALID")?;
        if self.path.exists() {
            let metadata = fs::symlink_metadata(&self.path).map_err(|_| "CREDENTIAL_IO_FAILED")?;
            if !metadata.is_file() || metadata.len() > MAX_FILE {
                return Err("CREDENTIAL_STORE_CORRUPT".into());
            }
            let mut previous = tempfile::Builder::new()
                .prefix("pre-restore-")
                .suffix(".onxcred")
                .tempfile_in(parent)
                .map_err(|_| "CREDENTIAL_IO_FAILED")?;
            previous
                .write_all(&fs::read(&self.path).map_err(|_| "CREDENTIAL_IO_FAILED")?)
                .and_then(|_| previous.as_file().sync_all())
                .map_err(|_| "CREDENTIAL_IO_FAILED")?;
            previous.keep().map_err(|_| "CREDENTIAL_IO_FAILED")?;
        }
        session.persist(&self.path)?;
        // Restoration deliberately leaves the vault locked; no implicit permission grant.
        Ok(keys.len())
    }

    pub fn list(&self) -> Result<Vec<CredentialId>> {
        self.session()?
            .store()?
            .keys()
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?
            .iter()
            .map(|key| serde_json::from_slice(key).map_err(|_| "CREDENTIAL_STORE_CORRUPT".into()))
            .collect()
    }
    pub fn put(&mut self, id: &CredentialId, value: Zeroizing<Vec<u8>>) -> Result<()> {
        let session = self.session()?;
        let result = session
            .write(id.key()?, &value)
            .and_then(|_| session.persist(&self.path));
        if result.is_err() {
            self.lock();
        } // Never serve uncommitted memory after disk failure.
        result
    }
    pub fn delete(&mut self, id: &CredentialId) -> Result<()> {
        let session = self.session()?;
        session
            .store()?
            .delete(&id.key()?)
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
        let result = session.persist(&self.path);
        if result.is_err() {
            self.lock();
        }
        result
    }
    /// Internal consumers must supply the scope established by the Host dispatcher.
    /// This method must never be registered as a Tauri command.
    pub fn resolve(&self, caller: &Scope, id: &CredentialId) -> Result<Option<Zeroizing<Vec<u8>>>> {
        if caller != &id.scope {
            return Err("CREDENTIAL_SCOPE_DENIED".into());
        }
        self.session()?.read(&id.key()?)
    }
    pub fn change_password(&mut self, password: Zeroizing<Vec<u8>>) -> Result<()> {
        let previous = self.session()?;
        let mut salt = [0u8; 32];
        rand::rngs::OsRng
            .try_fill_bytes(&mut salt)
            .map_err(|_| "CREDENTIAL_ENTROPY_FAILED")?;
        let next = Unlocked::derive(&password, salt)?;
        next.stronghold
            .create_client(CLIENT)
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?;
        for key in previous
            .store()?
            .keys()
            .map_err(|_| "CREDENTIAL_STORE_FAILED")?
        {
            let value = previous.read(&key)?.ok_or("CREDENTIAL_STORE_CORRUPT")?;
            next.write(key, &value)?;
        }
        next.persist(&self.path)?;
        self.unlocked = Some(next);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn password() -> Zeroizing<Vec<u8>> {
        Zeroizing::new(b"test-only-password-123".to_vec())
    }
    #[test]
    fn encrypted_backup_restores_corrupt_store_without_overwrite_on_failure() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("credentials.v1");
        let backup = temp.path().join("backup.onxcred");
        let mut broker = CredentialBroker::new(path.clone());
        broker.unlock(password()).unwrap();
        let id = CredentialId::legacy("test-provider");
        broker
            .put(&id, Zeroizing::new(b"backup-test-secret".to_vec()))
            .unwrap();
        broker.backup(&backup).unwrap();
        assert_eq!(
            broker.backup(&backup).unwrap_err(),
            "CREDENTIAL_BACKUP_EXISTS"
        );
        assert_eq!(
            broker.restore(&backup, password()).unwrap_err(),
            "CREDENTIALS_MUST_LOCK"
        );
        broker.lock();
        fs::write(&path, b"corrupt-original").unwrap();
        assert!(broker
            .restore(&backup, Zeroizing::new(b"wrong-test-password".to_vec()))
            .is_err());
        assert_eq!(fs::read(&path).unwrap(), b"corrupt-original");
        assert_eq!(broker.restore(&backup, password()).unwrap(), 1);
        assert!(broker.is_locked());
        let saved = fs::read_dir(temp.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .find(|e| e.file_name().to_string_lossy().starts_with("pre-restore-"))
            .unwrap();
        assert_eq!(fs::read(saved.path()).unwrap(), b"corrupt-original");
        broker.unlock(password()).unwrap();
        assert_eq!(
            broker
                .resolve(&Scope::Provider, &id)
                .unwrap()
                .unwrap()
                .as_slice(),
            b"backup-test-secret"
        );
        assert!(!fs::read(backup)
            .unwrap()
            .windows(18)
            .any(|w| w == b"backup-test-secret"));
    }
    #[test]
    fn session_revocation_denies_new_resolves_and_mutations() {
        let temp = tempfile::tempdir().unwrap();
        let mut broker = CredentialBroker::new(temp.path().join("credentials.v1"));
        broker.unlock(password()).unwrap();
        let id = CredentialId::legacy("test-provider");
        broker
            .put(&id, Zeroizing::new(b"test-value".to_vec()))
            .unwrap();
        broker.lock_signal().fetch_add(1, Ordering::SeqCst);
        assert!(broker.is_locked());
        assert_eq!(
            broker.resolve(&Scope::Provider, &id).unwrap_err(),
            "CREDENTIALS_LOCKED"
        );
        assert_eq!(
            broker
                .put(&id, Zeroizing::new(b"new-value".to_vec()))
                .unwrap_err(),
            "CREDENTIALS_LOCKED"
        );
        broker.unlock(password()).unwrap();
        assert!(!broker.is_locked());
    }
    #[test]
    fn b02_fernet_migration_matrix_is_atomic_verified_and_idempotent() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/fernet-python.json")).unwrap();
        let temp = tempfile::tempdir().unwrap();
        let old = temp.path().join("legacy");
        fs::create_dir(&old).unwrap();
        let source = serde_json::to_vec(&fixture["tokens"]).unwrap();
        let source_path = old.join("credentials.json");
        let key_path = old.join("master.key");
        let legacy_key = fixture["key"].as_str().unwrap();
        fs::write(&source_path, &source).unwrap();
        fs::write(&key_path, legacy_key).unwrap();
        let mut broker = CredentialBroker::new(temp.path().join("new/stronghold.v1"));
        broker.unlock(password()).unwrap();
        for _ in 0..4 {
            assert_eq!(broker.import_fernet(&old, None).unwrap(), 100);
            assert_eq!(broker.list().unwrap().len(), 100);
        }
        assert_eq!(fs::read(&source_path).unwrap(), source);
        assert_eq!(fs::read_to_string(&key_path).unwrap(), legacy_key);
        let marker: serde_json::Value =
            serde_json::from_slice(&fs::read(old.join(".opennexus-owner.json")).unwrap()).unwrap();
        assert_eq!(marker["state"], "switched");
        assert_eq!(marker["count"], 100);
        assert_eq!(marker["environment_key"], false);
        assert_eq!(
            marker["source_sha256"],
            format!("{:x}", Sha256::digest(&source))
        );
        broker.lock();
        broker.unlock(password()).unwrap();
        for (id, value) in fixture["values"].as_object().unwrap() {
            assert_eq!(
                broker
                    .resolve(&Scope::Provider, &CredentialId::legacy(id))
                    .unwrap()
                    .unwrap()
                    .as_slice(),
                value.as_str().unwrap().as_bytes()
            );
        }

        let environment_source = temp.path().join("environment-source");
        fs::create_dir(&environment_source).unwrap();
        fs::write(environment_source.join("credentials.json"), &source).unwrap();
        let mut environment_broker =
            CredentialBroker::new(temp.path().join("environment-target/stronghold.v1"));
        environment_broker.unlock(password()).unwrap();
        assert_eq!(
            environment_broker
                .import_fernet(
                    &environment_source,
                    Some(Zeroizing::new(legacy_key.to_string())),
                )
                .unwrap(),
            100
        );
        assert_eq!(environment_broker.list().unwrap().len(), 100);
        assert!(!environment_source.join("master.key").exists());
        assert_eq!(
            fs::read(environment_source.join("credentials.json")).unwrap(),
            source
        );
        let environment_marker: serde_json::Value = serde_json::from_slice(
            &fs::read(environment_source.join(".opennexus-owner.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(environment_marker["environment_key"], true);

        let empty_source = temp.path().join("empty-source");
        fs::create_dir(&empty_source).unwrap();
        fs::write(empty_source.join("credentials.json"), b"{}").unwrap();
        fs::write(empty_source.join("master.key"), legacy_key).unwrap();
        let mut empty_broker =
            CredentialBroker::new(temp.path().join("empty-target/stronghold.v1"));
        empty_broker.unlock(password()).unwrap();
        assert_eq!(empty_broker.import_fernet(&empty_source, None).unwrap(), 0);
        assert!(empty_broker.list().unwrap().is_empty());
        assert_eq!(
            fs::read(empty_source.join("credentials.json")).unwrap(),
            b"{}"
        );
        let empty_marker: serde_json::Value =
            serde_json::from_slice(&fs::read(empty_source.join(".opennexus-owner.json")).unwrap())
                .unwrap();
        assert_eq!(empty_marker["count"], 0);
        assert_eq!(empty_marker["state"], "switched");

        let missing_key_source = temp.path().join("missing-key-source");
        fs::create_dir(&missing_key_source).unwrap();
        fs::write(missing_key_source.join("credentials.json"), &source).unwrap();
        let mut missing_key_broker =
            CredentialBroker::new(temp.path().join("missing-key-target/stronghold.v1"));
        missing_key_broker.unlock(password()).unwrap();
        let missing_target_before = fs::read(&missing_key_broker.path).ok();
        assert_eq!(
            missing_key_broker
                .import_fernet(&missing_key_source, None)
                .unwrap_err(),
            "MIGRATION_KEY_MISSING"
        );
        assert_eq!(
            fs::read(&missing_key_broker.path).ok(),
            missing_target_before
        );
        assert_eq!(
            fs::read(missing_key_source.join("credentials.json")).unwrap(),
            source
        );
        assert!(!missing_key_source.join(".opennexus-owner.json").exists());

        let bad_source = temp.path().join("bad-token-source");
        fs::create_dir(&bad_source).unwrap();
        let mut bad_tokens = fixture["tokens"].clone();
        bad_tokens["provider-050"] = serde_json::json!("invalid-fernet-token");
        let bad_bytes = serde_json::to_vec(&bad_tokens).unwrap();
        fs::write(bad_source.join("credentials.json"), &bad_bytes).unwrap();
        fs::write(bad_source.join("master.key"), legacy_key).unwrap();
        let mut bad_broker =
            CredentialBroker::new(temp.path().join("bad-token-target/stronghold.v1"));
        bad_broker.unlock(password()).unwrap();
        let bad_target_before = fs::read(&bad_broker.path).ok();
        assert_eq!(
            bad_broker.import_fernet(&bad_source, None).unwrap_err(),
            "MIGRATION_DECRYPT_FAILED"
        );
        assert_eq!(fs::read(&bad_broker.path).ok(), bad_target_before);
        assert_eq!(
            fs::read(bad_source.join("credentials.json")).unwrap(),
            bad_bytes
        );
        assert!(!bad_source.join(".opennexus-owner.json").exists());

        let conflict_source = temp.path().join("conflict-source");
        fs::create_dir(&conflict_source).unwrap();
        fs::write(conflict_source.join("credentials.json"), &source).unwrap();
        fs::write(conflict_source.join("master.key"), legacy_key).unwrap();
        let mut conflict_broker =
            CredentialBroker::new(temp.path().join("conflict-target/stronghold.v1"));
        conflict_broker.unlock(password()).unwrap();
        conflict_broker
            .put(
                &CredentialId::legacy("provider-000"),
                Zeroizing::new(b"changed-new-value".to_vec()),
            )
            .unwrap();
        let conflict_target_before = fs::read(&conflict_broker.path).unwrap();
        assert_eq!(
            conflict_broker
                .import_fernet(&conflict_source, None)
                .unwrap_err(),
            "MIGRATION_CONFLICT"
        );
        assert_eq!(
            fs::read(&conflict_broker.path).unwrap(),
            conflict_target_before
        );
        assert_eq!(
            fs::read(conflict_source.join("credentials.json")).unwrap(),
            source
        );
        assert!(!conflict_source.join(".opennexus-owner.json").exists());
        assert_eq!(
            conflict_broker
                .resolve(&Scope::Provider, &CredentialId::legacy("provider-000"))
                .unwrap()
                .unwrap()
                .as_slice(),
            b"changed-new-value"
        );
    }
    #[test]
    fn stronghold_roundtrip_scope_lock_and_password_rotation() {
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("credentials.v1");
        let mut broker = CredentialBroker::new(file.clone());
        broker.unlock(password()).unwrap();
        let id = CredentialId {
            scope: Scope::Provider,
            id: "provider-one".into(),
        };
        broker
            .put(&id, Zeroizing::new(b"fixture-secret-do-not-log".to_vec()))
            .unwrap();
        assert!(broker.resolve(&Scope::Mcp("x".into()), &id).is_err());
        assert!(!fs::read(&file)
            .unwrap()
            .windows(b"fixture-secret-do-not-log".len())
            .any(|w| w == b"fixture-secret-do-not-log"));
        broker.lock();
        assert_eq!(
            broker.resolve(&Scope::Provider, &id).unwrap_err(),
            "CREDENTIALS_LOCKED"
        );
        broker.unlock(password()).unwrap();
        assert_eq!(
            broker
                .resolve(&Scope::Provider, &id)
                .unwrap()
                .unwrap()
                .as_slice(),
            b"fixture-secret-do-not-log"
        );
        broker
            .change_password(Zeroizing::new(b"second-test-password".to_vec()))
            .unwrap();
        broker.lock();
        assert!(broker.unlock(password()).is_err());
        broker
            .unlock(Zeroizing::new(b"second-test-password".to_vec()))
            .unwrap();
        assert_eq!(broker.list().unwrap().len(), 1);
        broker.delete(&id).unwrap();
        assert!(broker.resolve(&Scope::Provider, &id).unwrap().is_none());
    }
    #[test]
    fn corrupt_store_is_not_recreated() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("credentials.v1");
        fs::write(&path, b"corrupt").unwrap();
        let mut broker = CredentialBroker::new(path.clone());
        assert!(broker.unlock(password()).is_err());
        assert_eq!(fs::read(path).unwrap(), b"corrupt");
    }
}
