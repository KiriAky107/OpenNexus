//! 设备本地 Stronghold 代理；任何公开 IPC 都不会返回机密字节。
//!
//! Stronghold 存储即使在解锁期间也只包含 AEAD 密文。快照与盐构成一个原子整体，
//! 避免密码变更使两个文件处于不一致状态。
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

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct MigrationState {
    schema: u32,
    owner: String,
    state: String,
    source_sha256: String,
    count: usize,
    environment_key: bool,
    migration_id: String,
}

impl MigrationState {
    fn validate(&self) -> Result<()> {
        if self.schema != 1
            || self.owner != "OpenNexus"
            || !matches!(
                self.state.as_str(),
                "backed_up"
                    | "copied"
                    | "verified"
                    | "switched"
                    | "cleanup_authorized"
                    | "cleanup_confirmed"
            )
            || self.source_sha256.len() != 64
            || !self
                .source_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || self.migration_id.len() != 64
            || !self
                .migration_id
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("MIGRATION_STATE_INVALID".into());
        }
        Ok(())
    }

    fn same_migration(&self, other: &Self) -> bool {
        self.schema == other.schema
            && self.owner == other.owner
            && self.source_sha256 == other.source_sha256
            && self.count == other.count
            && self.environment_key == other.environment_key
            && self.migration_id == other.migration_id
    }
}

#[derive(Clone, Serialize)]
pub struct MigrationCleanupPreview {
    pub source_sha256: String,
    pub count: usize,
    pub environment_key: bool,
    pub cleanup_complete: bool,
}

fn is_reparse_point(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        let _ = metadata;
        false
    }
}

fn ordinary_file(path: &Path, maximum: u64, error: &str) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path).map_err(|_| error.to_string())?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || is_reparse_point(&metadata)
        || metadata.len() > maximum
    {
        return Err(error.into());
    }
    Ok(metadata)
}

fn ordinary_directory(path: &Path, error: &str) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path).map_err(|_| error.to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() || is_reparse_point(&metadata) {
        return Err(error.into());
    }
    Ok(metadata)
}

fn persist_state(path: &Path, state: &MigrationState) -> Result<()> {
    state.validate()?;
    let parent = path.parent().ok_or("MIGRATION_STATE_INVALID")?;
    fs::create_dir_all(parent).map_err(|_| "MIGRATION_SWITCH_FAILED")?;
    let bytes = serde_json::to_vec(state).map_err(|_| "MIGRATION_STATE_INVALID")?;
    let mut file =
        tempfile::NamedTempFile::new_in(parent).map_err(|_| "MIGRATION_SWITCH_FAILED")?;
    file.write_all(&bytes)
        .and_then(|_| file.as_file().sync_all())
        .map_err(|_| "MIGRATION_SWITCH_FAILED")?;
    file.persist(path).map_err(|_| "MIGRATION_SWITCH_FAILED")?;
    Ok(())
}

fn read_state(path: &Path) -> Result<MigrationState> {
    ordinary_file(path, 64 * 1024, "MIGRATION_STATE_INVALID")?;
    let state: MigrationState =
        serde_json::from_slice(&fs::read(path).map_err(|_| "MIGRATION_STATE_INVALID")?)
            .map_err(|_| "MIGRATION_STATE_INVALID")?;
    state.validate()?;
    Ok(state)
}

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
    /// 保留不透明的遗留引用。散列 Plugin/MCP ID 与提供商 ID 保持隔离；只有受信任的 Core 适配器才能使用这些别名。
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

fn verify_migration_backup(
    session: &Unlocked,
    backup: &Path,
    source_sha256: &str,
) -> Result<Zeroizing<Vec<u8>>> {
    ordinary_directory(backup, "MIGRATION_BACKUP_FAILED")?;
    let source_backup = backup.join("credentials.json");
    let key_backup = backup.join("master-key.sealed");
    ordinary_file(&source_backup, MAX_FILE, "MIGRATION_BACKUP_FAILED")?;
    ordinary_file(&key_backup, MAX_FILE, "MIGRATION_BACKUP_FAILED")?;
    if format!(
        "{:x}",
        Sha256::digest(fs::read(source_backup).map_err(|_| "MIGRATION_BACKUP_FAILED")?)
    ) != source_sha256
    {
        return Err("MIGRATION_BACKUP_FAILED".into());
    }
    let sealed = fs::read(key_backup).map_err(|_| "MIGRATION_BACKUP_FAILED")?;
    if sealed.len() <= 51 || &sealed[..7] != b"ONXFBK1" || sealed[7..39] != session.salt {
        return Err("MIGRATION_BACKUP_FAILED".into());
    }
    session
        .cipher()?
        .decrypt(Nonce::from_slice(&sealed[39..51]), &sealed[51..])
        .map(Zeroizing::new)
        .map_err(|_| "MIGRATION_BACKUP_FAILED".into())
}

pub struct CredentialBroker {
    path: PathBuf,
    unlocked: Option<Unlocked>,
    // 单独的稳定索引节点：快照被原子替换，因此锁定快照本身不会保护替换后的下一个写入者。
    ownership: Option<fs::File>,
    lock_epoch: Arc<AtomicU64>,
    unlocked_epoch: u64,
}

impl CredentialBroker {
    /// 源来自本机文件选择器，而不是原始 WebView 路径。导入是幂等的；冲突的 ID 会停止整个事务。
    pub fn import_fernet(
        &mut self,
        directory: &Path,
        environment_key: Option<Zeroizing<String>>,
    ) -> Result<usize> {
        self.import_fernet_inner(directory, environment_key, |_| Ok(()))
    }
    fn import_fernet_inner(
        &mut self,
        directory: &Path,
        environment_key: Option<Zeroizing<String>>,
        mut checkpoint: impl FnMut(&str) -> Result<()>,
    ) -> Result<usize> {
        use fs2::FileExt;
        let directory = directory
            .canonicalize()
            .map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        let source = directory.join("credentials.json");
        let key_path = directory.join("master.key");
        ordinary_file(&source, MAX_FILE, "MIGRATION_SOURCE_INVALID")?;
        let lock = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(directory.join(".migration.lock"))
            .map_err(|_| "MIGRATION_SOURCE_BUSY")?;
        lock.try_lock_exclusive()
            .map_err(|_| "MIGRATION_SOURCE_BUSY")?;
        let source_bytes = fs::read(&source).map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        let source_hash = format!("{:x}", Sha256::digest(&source_bytes));
        let tokens: BTreeMap<String, String> =
            serde_json::from_slice(&source_bytes).map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        if tokens.len() > 10000 {
            return Err("MIGRATION_SOURCE_INVALID".into());
        }
        let local_key = if environment_key.is_none() {
            ordinary_file(&key_path, 4096, "MIGRATION_KEY_MISSING")?;
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
        let journal = self
            .path
            .parent()
            .ok_or("CREDENTIAL_PATH_INVALID")?
            .join("migration-state")
            .join(format!("{migration_id}.json"));
        let owner_path = directory.join(".opennexus-owner.json");
        if owner_path.exists() {
            let owner = read_state(&owner_path)?;
            if owner.state == "cleanup_authorized" || owner.state == "cleanup_confirmed" {
                return Err("MIGRATION_CLEANUP_ALREADY_AUTHORIZED".into());
            }
            if owner.state != "switched"
                || owner.source_sha256 != source_hash
                || owner.count != decoded.len()
                || owner.environment_key != environment_key.is_some()
                || owner.migration_id != migration_id
            {
                return Err("MIGRATION_SOURCE_CHANGED".into());
            }
        }
        fs::create_dir_all(&backup).map_err(|_| "MIGRATION_BACKUP_FAILED")?;
        // 备份包含密文；旧密钥被密封在已解锁的设备密钥下，而不是添加另一个明文 master.key。
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
        let mut state = MigrationState {
            schema: 1,
            owner: "OpenNexus".into(),
            state: "backed_up".into(),
            source_sha256: source_hash.clone(),
            count: decoded.len(),
            environment_key: environment_key.is_some(),
            migration_id: migration_id.clone(),
        };
        persist_state(&journal, &state)?;
        checkpoint("backed_up")?;
        let result = (|| {
            for (key, value) in &decoded {
                session.write(key.clone(), value)?;
            }
            session.persist(&self.path)?;
            state.state = "copied".into();
            persist_state(&journal, &state)?;
            checkpoint("copied")?;
            // 重新打开提交的 Stronghold 快照，而不是内存缓存。
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
            state.state = "verified".into();
            persist_state(&journal, &state)?;
            checkpoint("verified")?;
            let mut owner = state.clone();
            owner.state = "switched".into();
            persist_state(&owner_path, &owner)?;
            checkpoint("ownership_committed")?;
            state.state = "switched".into();
            persist_state(&journal, &state)?;
            checkpoint("switched")?;
            Ok(decoded.len())
        })();
        if result.is_err() {
            self.lock();
        }
        result
    }

    /// 仅删除已验证的旧文件和此迁移的加密备份。本机 Host 必须首先获得 source_sha256 的明确用户确认。
    pub fn cleanup_fernet(
        &mut self,
        directory: &Path,
        confirmed_source_sha256: &str,
    ) -> Result<()> {
        self.cleanup_fernet_inner(directory, confirmed_source_sha256, |_| Ok(()))
    }

    pub fn cleanup_fernet_preview(&self, directory: &Path) -> Result<MigrationCleanupPreview> {
        self.session()?;
        let directory = directory
            .canonicalize()
            .map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        ordinary_directory(&directory, "MIGRATION_SOURCE_INVALID")?;
        let state = read_state(&directory.join(".opennexus-owner.json"))?;
        if !matches!(
            state.state.as_str(),
            "switched" | "cleanup_authorized" | "cleanup_confirmed"
        ) {
            return Err("MIGRATION_STATE_INVALID".into());
        }
        Ok(MigrationCleanupPreview {
            source_sha256: state.source_sha256,
            count: state.count,
            environment_key: state.environment_key,
            cleanup_complete: state.state == "cleanup_confirmed",
        })
    }

    fn cleanup_fernet_inner(
        &mut self,
        directory: &Path,
        confirmed_source_sha256: &str,
        mut checkpoint: impl FnMut(&str) -> Result<()>,
    ) -> Result<()> {
        use fs2::FileExt;
        let session = self.session()?;
        let directory = directory
            .canonicalize()
            .map_err(|_| "MIGRATION_SOURCE_INVALID")?;
        ordinary_directory(&directory, "MIGRATION_SOURCE_INVALID")?;
        let lock_path = directory.join(".migration.lock");
        let lock = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&lock_path)
            .map_err(|_| "MIGRATION_SOURCE_BUSY")?;
        lock.try_lock_exclusive()
            .map_err(|_| "MIGRATION_SOURCE_BUSY")?;
        let owner_path = directory.join(".opennexus-owner.json");
        let mut state = read_state(&owner_path)?;
        if state.state == "cleanup_confirmed" {
            return Ok(());
        }
        if !matches!(state.state.as_str(), "switched" | "cleanup_authorized")
            || state.source_sha256 != confirmed_source_sha256
        {
            return Err("MIGRATION_CLEANUP_CONFIRMATION".into());
        }
        let source = directory.join("credentials.json");
        let parent = self.path.parent().ok_or("CREDENTIAL_PATH_INVALID")?;
        let journal = parent
            .join("migration-state")
            .join(format!("{}.json", state.migration_id));
        let journal_state = read_state(&journal)?;
        if !journal_state.same_migration(&state)
            || !matches!(
                journal_state.state.as_str(),
                "switched" | "cleanup_authorized" | "cleanup_confirmed"
            )
        {
            return Err("MIGRATION_STATE_INVALID".into());
        }
        let backup = parent.join("migration-backups").join(&state.migration_id);
        let sealed_key = if backup.exists() {
            Some(verify_migration_backup(
                session,
                &backup,
                &state.source_sha256,
            )?)
        } else {
            None
        };
        if state.state == "switched" && sealed_key.is_none() {
            return Err("MIGRATION_BACKUP_FAILED".into());
        }
        if source.exists() {
            ordinary_file(&source, MAX_FILE, "MIGRATION_SOURCE_CHANGED")?;
            if format!(
                "{:x}",
                Sha256::digest(fs::read(&source).map_err(|_| "MIGRATION_SOURCE_CHANGED")?)
            ) != state.source_sha256
            {
                return Err("MIGRATION_SOURCE_CHANGED".into());
            }
        } else if state.state == "switched" {
            return Err("MIGRATION_SOURCE_CHANGED".into());
        }
        let key_path = directory.join("master.key");
        if !state.environment_key && key_path.exists() {
            ordinary_file(&key_path, 4096, "MIGRATION_CLEANUP_FAILED")?;
            let expected_key = sealed_key.as_ref().ok_or("MIGRATION_CLEANUP_FAILED")?;
            if fs::read(&key_path).map_err(|_| "MIGRATION_CLEANUP_FAILED")?
                != expected_key.as_slice()
            {
                return Err("MIGRATION_CLEANUP_FAILED".into());
            }
        }
        if state.state == "cleanup_authorized"
            && sealed_key.is_none()
            && (source.exists() || (!state.environment_key && key_path.exists()))
        {
            return Err("MIGRATION_CLEANUP_FAILED".into());
        }
        if state.state == "switched" {
            state.state = "cleanup_authorized".into();
            persist_state(&owner_path, &state)?;
            checkpoint("cleanup_authorized")?;
            persist_state(&journal, &state)?;
        }
        if source.exists() {
            ordinary_file(&source, MAX_FILE, "MIGRATION_CLEANUP_FAILED")?;
            fs::remove_file(&source).map_err(|_| "MIGRATION_CLEANUP_FAILED")?;
        }
        checkpoint("source_removed")?;
        if !state.environment_key && key_path.exists() {
            ordinary_file(&key_path, 4096, "MIGRATION_CLEANUP_FAILED")?;
            fs::remove_file(key_path).map_err(|_| "MIGRATION_CLEANUP_FAILED")?;
        }
        checkpoint("key_removed")?;
        if backup.exists() {
            ordinary_directory(&backup, "MIGRATION_CLEANUP_FAILED")?;
            fs::remove_dir_all(&backup).map_err(|_| "MIGRATION_CLEANUP_FAILED")?;
        }
        checkpoint("backup_removed")?;
        state.state = "cleanup_confirmed".into();
        persist_state(&journal, &state)?;
        checkpoint("cleanup_recorded")?;
        persist_state(&owner_path, &state)?;
        checkpoint("cleanup_confirmed")?;
        Ok(())
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
                        // ID 迁移无法更改提供商/Plugin/MCP 系列。
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
                    // 拒绝循环/重叠源+目标，而不是在多 ID 迁移中途删除新写入的值。
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
            options.share_mode(0x1 | 0x2); // 不允许替换保留的锁定文件。
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
        // 备份可以位于只读介质上。该临时文件仅包含密文。
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

    /// 本机选择器选择的目的地；备份已加密并且永远不会覆盖。
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

    /// 在原子替换之前验证每条记录；保留之前的加密文件。调用者必须通过本机对话框获得明确的确认。
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
        // 恢复特意将金库锁定；没有隐式许可授予。
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
        } // 磁盘故障后切勿服务未提交的内存。
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
    /// 内部消费者必须提供 Host 调度程序建立的范围。此方法绝不能注册为 Tauri 命令。
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
    fn b04_fixture() -> (Vec<u8>, String, BTreeMap<String, String>) {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/fernet-python.json")).unwrap();
        let mut tokens = BTreeMap::new();
        let mut values = BTreeMap::new();
        for id in ["provider-000", "provider-001", "provider-002"] {
            tokens.insert(
                id.to_string(),
                fixture["tokens"][id].as_str().unwrap().to_string(),
            );
            values.insert(
                id.to_string(),
                fixture["values"][id].as_str().unwrap().to_string(),
            );
        }
        (
            serde_json::to_vec(&tokens).unwrap(),
            fixture["key"].as_str().unwrap().to_string(),
            values,
        )
    }
    fn b04_write_source(directory: &Path, source: &[u8], key: &str) {
        fs::create_dir_all(directory).unwrap();
        fs::write(directory.join("credentials.json"), source).unwrap();
        fs::write(directory.join("master.key"), key).unwrap();
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
    #[ignore = "parent acceptance oracle hard-terminates this helper at a durable boundary"]
    fn b04_migration_boundary_worker() {
        let Some(source) = std::env::var_os("OPENNEXUS_B04_SOURCE") else {
            return;
        };
        let target = PathBuf::from(std::env::var_os("OPENNEXUS_B04_TARGET").unwrap());
        let boundary = std::env::var("OPENNEXUS_B04_BOUNDARY").unwrap();
        let marker = PathBuf::from(std::env::var_os("OPENNEXUS_B04_MARKER").unwrap());
        let mut broker = CredentialBroker::new(target);
        broker.unlock(password()).unwrap();
        let _ = broker.import_fernet_inner(Path::new(&source), None, |at| {
            if at == boundary {
                let file = fs::File::create(&marker).unwrap();
                file.sync_all().unwrap();
                loop {
                    std::thread::sleep(std::time::Duration::from_secs(60));
                }
            }
            Ok(())
        });
        panic!("B-04 helper passed the requested boundary");
    }
    #[test]
    fn b04_migration_survives_twenty_hard_terminations_per_boundary() {
        let (source, legacy_key, expected) = b04_fixture();
        for boundary in [
            "backed_up",
            "copied",
            "verified",
            "ownership_committed",
            "switched",
        ] {
            for round in 0..20 {
                let temp = tempfile::tempdir().unwrap();
                let legacy = temp.path().join("legacy");
                let target = temp.path().join("new/stronghold.v1");
                b04_write_source(&legacy, &source, &legacy_key);
                let marker = temp.path().join(format!("{boundary}-{round}.ready"));
                let mut child = std::process::Command::new(std::env::current_exe().unwrap())
                    .args([
                        "--ignored",
                        "--exact",
                        "credentials::tests::b04_migration_boundary_worker",
                        "--nocapture",
                    ])
                    .env("OPENNEXUS_B04_SOURCE", &legacy)
                    .env("OPENNEXUS_B04_TARGET", &target)
                    .env("OPENNEXUS_B04_BOUNDARY", boundary)
                    .env("OPENNEXUS_B04_MARKER", &marker)
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .spawn()
                    .unwrap();
                let started = std::time::Instant::now();
                while !marker.is_file() {
                    assert!(
                        child.try_wait().unwrap().is_none(),
                        "helper exited before {boundary}"
                    );
                    assert!(
                        started.elapsed() < std::time::Duration::from_secs(30),
                        "helper did not reach {boundary}"
                    );
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                child.kill().unwrap();
                assert!(!child.wait().unwrap().success());
                assert_eq!(fs::read(legacy.join("credentials.json")).unwrap(), source);
                assert_eq!(
                    fs::read_to_string(legacy.join("master.key")).unwrap(),
                    legacy_key
                );

                let owner_exists = legacy.join(".opennexus-owner.json").is_file();
                let mut broker = CredentialBroker::new(target);
                broker.unlock(password()).unwrap();
                let before_count = broker.list().unwrap().len();
                assert!(before_count == 0 || before_count == expected.len());
                if owner_exists {
                    assert_eq!(before_count, expected.len());
                }
                assert_eq!(broker.import_fernet(&legacy, None).unwrap(), expected.len());
                assert_eq!(broker.list().unwrap().len(), expected.len());
                for (id, value) in &expected {
                    assert_eq!(
                        broker
                            .resolve(&Scope::Provider, &CredentialId::legacy(id))
                            .unwrap()
                            .unwrap()
                            .as_slice(),
                        value.as_bytes()
                    );
                }
                let owner = read_state(&legacy.join(".opennexus-owner.json")).unwrap();
                let journal = read_state(
                    &broker
                        .path
                        .parent()
                        .unwrap()
                        .join("migration-state")
                        .join(format!("{}.json", owner.migration_id)),
                )
                .unwrap();
                assert_eq!(owner.state, "switched");
                assert_eq!(journal, owner);
            }
        }
    }
    #[test]
    fn b04_cleanup_is_confirmed_scoped_and_resumable() {
        let (source, legacy_key, expected) = b04_fixture();
        for boundary in [
            "cleanup_authorized",
            "source_removed",
            "key_removed",
            "backup_removed",
            "cleanup_recorded",
            "cleanup_confirmed",
        ] {
            let temp = tempfile::tempdir().unwrap();
            let legacy = temp.path().join("legacy");
            b04_write_source(&legacy, &source, &legacy_key);
            let mut broker = CredentialBroker::new(temp.path().join("new/stronghold.v1"));
            broker.unlock(password()).unwrap();
            broker.import_fernet(&legacy, None).unwrap();
            let state = read_state(&legacy.join(".opennexus-owner.json")).unwrap();
            let backup = broker
                .path
                .parent()
                .unwrap()
                .join("migration-backups")
                .join(&state.migration_id);
            assert!(legacy.join("credentials.json").is_file());
            assert!(legacy.join("master.key").is_file());
            assert!(backup.is_dir());
            assert_eq!(
                broker.cleanup_fernet(&legacy, &"0".repeat(64)).unwrap_err(),
                "MIGRATION_CLEANUP_CONFIRMATION"
            );
            assert!(legacy.join("credentials.json").is_file());
            assert!(legacy.join("master.key").is_file());
            assert!(backup.is_dir());

            let mut stopped = false;
            assert_eq!(
                broker
                    .cleanup_fernet_inner(&legacy, &state.source_sha256, |at| {
                        if at == boundary && !stopped {
                            stopped = true;
                            return Err("POWER_CUT".into());
                        }
                        Ok(())
                    })
                    .unwrap_err(),
                "POWER_CUT"
            );
            broker
                .cleanup_fernet(&legacy, &state.source_sha256)
                .unwrap();
            assert!(!legacy.join("credentials.json").exists());
            assert!(!legacy.join("master.key").exists());
            assert!(!backup.exists());
            let completed = read_state(&legacy.join(".opennexus-owner.json")).unwrap();
            assert_eq!(completed.state, "cleanup_confirmed");
            assert_eq!(completed.count, expected.len());
            broker
                .cleanup_fernet(&legacy, &state.source_sha256)
                .unwrap();
        }

        let temp = tempfile::tempdir().unwrap();
        let legacy = temp.path().join("environment-legacy");
        let external_key_file = temp.path().join("external-environment-key.txt");
        fs::create_dir_all(&legacy).unwrap();
        fs::write(legacy.join("credentials.json"), &source).unwrap();
        fs::write(legacy.join("master.key"), b"unmanaged-sentinel").unwrap();
        fs::write(&external_key_file, &legacy_key).unwrap();
        let external_before = fs::read(&external_key_file).unwrap();
        let mut broker = CredentialBroker::new(temp.path().join("environment/stronghold.v1"));
        broker.unlock(password()).unwrap();
        broker
            .import_fernet(&legacy, Some(Zeroizing::new(legacy_key)))
            .unwrap();
        let state = read_state(&legacy.join(".opennexus-owner.json")).unwrap();
        assert!(state.environment_key);
        broker
            .cleanup_fernet(&legacy, &state.source_sha256)
            .unwrap();
        assert!(!legacy.join("credentials.json").exists());
        assert_eq!(
            fs::read(legacy.join("master.key")).unwrap(),
            b"unmanaged-sentinel"
        );
        assert_eq!(fs::read(external_key_file).unwrap(), external_before);
        assert_eq!(broker.list().unwrap().len(), expected.len());
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
    fn b01_domain_matrix_never_exposes_or_cross_resolves_secrets() {
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("credentials.v1");
        let mut broker = CredentialBroker::new(file.clone());
        broker.unlock(password()).unwrap();
        let domains = [
            ("provider", Scope::Provider),
            ("plugin", Scope::Plugin("reviewer".into())),
            ("mcp", Scope::Mcp("calendar".into())),
            ("sync", Scope::Sync("server-account".into())),
        ];
        let mut records = Vec::new();
        for (label, scope) in &domains {
            for index in 0..3 {
                let id = CredentialId {
                    scope: scope.clone(),
                    id: format!("b01-{label}-{index}"),
                };
                let secret = format!("b01-planted-{label}-{index}-value").into_bytes();
                broker.put(&id, Zeroizing::new(secret.clone())).unwrap();
                records.push((id, secret));
            }
        }
        let callers = domains
            .iter()
            .map(|(_, scope)| scope.clone())
            .collect::<Vec<_>>();
        let mut public_errors = Vec::new();
        for (id, secret) in &records {
            for caller in &callers {
                if caller == &id.scope {
                    let resolved = broker.resolve(caller, id).unwrap().unwrap();
                    assert!(resolved.as_slice() == secret, "same-domain secret mismatch");
                } else {
                    let error = broker.resolve(caller, id).unwrap_err();
                    assert_eq!(error, "CREDENTIAL_SCOPE_DENIED");
                    public_errors.push(error);
                }
            }
            let wrong_owner = match &id.scope {
                Scope::Plugin(_) => Some(Scope::Plugin("another-plugin".into())),
                Scope::Mcp(_) => Some(Scope::Mcp("another-mcp".into())),
                Scope::Sync(_) => Some(Scope::Sync("another-sync-account".into())),
                Scope::Provider => None,
            };
            if let Some(wrong_owner) = wrong_owner {
                let error = broker.resolve(&wrong_owner, id).unwrap_err();
                assert_eq!(error, "CREDENTIAL_SCOPE_DENIED");
                public_errors.push(error);
            }
        }
        assert_eq!(records.len(), 12);
        assert_eq!(public_errors.len(), 45);
        assert_eq!(broker.list().unwrap().len(), 12);

        let public_status = serde_json::to_vec(&serde_json::json!({
            "locked": broker.is_locked(),
            "credentials": broker.list().unwrap(),
            "errors": public_errors,
        }))
        .unwrap();
        let vault = tempfile::tempdir().unwrap();
        let mut workspace = crate::workspace::Workspace::open(vault.path()).unwrap();
        workspace
            .write("note.md", "", b"safe note", "local")
            .unwrap();
        let binding = workspace
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        let job = workspace.sync_next(&binding.id).unwrap().unwrap();
        let sync_payload =
            serde_json::to_vec(&workspace.sync_commit_payload(&job).unwrap()).unwrap();
        let encrypted = fs::read(&file).unwrap();
        for (_, secret) in &records {
            for exposed in [&encrypted, &public_status, &sync_payload] {
                assert!(
                    !exposed.windows(secret.len()).any(|window| window == secret),
                    "secret appeared outside the scoped resolver"
                );
            }
        }
        broker.lock();
        assert!(records.iter().all(|(id, _)| broker
            .resolve(&id.scope, id)
            .is_err_and(|error| error == "CREDENTIALS_LOCKED")));
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
    #[test]
    fn b03_unrecoverable_store_does_not_block_local_editing() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("credentials.v1");
        fs::write(&path, b"unrecoverable-credential-store").unwrap();
        let mut broker = CredentialBroker::new(path.clone());
        assert_eq!(
            broker.unlock(password()).unwrap_err(),
            "SCHEMA_INCOMPATIBLE"
        );
        assert!(broker.is_locked());
        assert_eq!(fs::read(&path).unwrap(), b"unrecoverable-credential-store");

        let vault = tempfile::tempdir().unwrap();
        let mut workspace = crate::workspace::Workspace::open(vault.path()).unwrap();
        let saved = workspace
            .write(
                "still-editable.md",
                "",
                b"local notes remain available",
                "local",
            )
            .unwrap();
        assert_eq!(
            workspace.read("still-editable.md").unwrap().content,
            "local notes remain available"
        );
        assert_eq!(
            fs::read(vault.path().join("still-editable.md")).unwrap(),
            b"local notes remain available"
        );
        assert_eq!(
            saved.hash,
            crate::workspace::hash(b"local notes remain available")
        );
    }
}
