//! 每个 Vault 一个 OS 锁和 SQLite 日志；恢复只重放摘要仍匹配的写入，绝不覆盖外部修改。

use fs2::FileExt;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use uuid::Uuid;

#[derive(Debug, Serialize)]
pub struct HostError {
    pub code: String,
    pub message: String,
}

pub type Result<T> = std::result::Result<T, HostError>;

impl HostError {
    pub(crate) fn new(code: &str) -> Self {
        Self {
            code: code.into(),
            message: code.into(),
        }
    }
}

impl From<std::io::Error> for HostError {
    fn from(_: std::io::Error) -> Self {
        Self::new("FILESYSTEM_ERROR")
    }
}
impl From<rusqlite::Error> for HostError {
    fn from(_: rusqlite::Error) -> Self {
        Self::new("DATABASE_ERROR")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub file_id: String,
    pub path: String,
    pub hash: String,
    pub revision: i64,
    pub deleted: bool,
    #[serde(default)]
    pub is_folder: bool,
}

#[derive(Serialize)]
pub struct Document {
    #[serde(flatten)]
    pub entry: Entry,
    pub content: String,
}

pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn linked(path: &Path) -> std::io::Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        // junction 也是 reparse point，不能仅检查 symlink。
        Ok(metadata.file_attributes() & 0x400 != 0)
    }
    #[cfg(not(windows))]
    {
        Ok(metadata.file_type().is_symlink())
    }
}

/// 将 Windows 本地磁盘的 verbatim 路径转换为适合界面和持久化的普通路径。
/// UNC 与其他设备路径保持原样，后续仍会被 Vault 安全检查拒绝。
pub fn portable_path(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        let raw = path.to_string_lossy();
        if let Some(rest) = raw.strip_prefix(r"\\?\") {
            let bytes = rest.as_bytes();
            if bytes.len() >= 3
                && bytes[0].is_ascii_alphabetic()
                && bytes[1] == b':'
                && matches!(bytes[2], b'\\' | b'/')
            {
                return PathBuf::from(rest);
            }
        }
    }
    path.to_path_buf()
}

pub fn portable_path_string(path: &Path) -> String {
    portable_path(path).to_string_lossy().into_owned()
}

pub struct Workspace {
    pub root: PathBuf,
    pub vault_id: String,
    pub(crate) db: Connection,
    _lock: File,
}

impl Workspace {
    pub fn open(root: &Path) -> Result<Self> {
        let root = portable_path(root);
        if root.to_string_lossy().starts_with("\\\\") || !root.is_dir() || linked(&root)? {
            return Err(HostError::new("VAULT_PATH_UNSUPPORTED"));
        }
        let root = root.canonicalize()?;
        let managed = root.join(".ainote");
        if managed.exists() && linked(&managed)? {
            return Err(HostError::new("UNSAFE_PATH"));
        }
        fs::create_dir_all(&managed)?;
        let lock_path = managed.join("host.lock");
        if lock_path.exists() && linked(&lock_path)? {
            return Err(HostError::new("UNSAFE_PATH"));
        }
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)?;
        lock.try_lock_exclusive()
            .map_err(|_| HostError::new("VAULT_ALREADY_OPEN"))?;
        let db_path = managed.join("host.sqlite3");
        if db_path.exists() && linked(&db_path)? {
            return Err(HostError::new("UNSAFE_PATH"));
        }
        let db = Connection::open(db_path)?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")?;
        let version: i64 = db.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version > 3 {
            return Err(HostError::new("SCHEMA_INCOMPATIBLE"));
        }
        if (1..3).contains(&version) {
            // Independent, complete SQLite backup before the schema ownership change.
            let backup = managed.join(format!("host-schema{version}-{}.sqlite3", Uuid::new_v4()));
            db.execute("VACUUM INTO ?1", [backup.to_string_lossy().as_ref()])?;
        }
        db.execute_batch("BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS identity (id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,hash TEXT NOT NULL,revision INTEGER NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS journal (operation_id TEXT PRIMARY KEY,file_id TEXT NOT NULL,path TEXT NOT NULL,expected TEXT NOT NULL,content BLOB NOT NULL,origin TEXT NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS file_ops (id TEXT PRIMARY KEY,kind TEXT NOT NULL,path TEXT NOT NULL,destination TEXT NOT NULL,hash TEXT NOT NULL,content BLOB NOT NULL,state TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS outbox (operation_id TEXT PRIMARY KEY,file_id TEXT NOT NULL,revision INTEGER NOT NULL,path TEXT NOT NULL,hash TEXT NOT NULL,operation TEXT NOT NULL,content BLOB NOT NULL,state TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS operations (operation_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,state TEXT NOT NULL,result TEXT);
            CREATE TABLE IF NOT EXISTS sync_bindings (id TEXT PRIMARY KEY,endpoint TEXT NOT NULL,remote_vault TEXT NOT NULL,account TEXT NOT NULL,state TEXT NOT NULL,cursor INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS sync_active ON sync_bindings(state) WHERE state='active';
            CREATE TABLE IF NOT EXISTS sync_jobs (binding TEXT NOT NULL,operation_id TEXT NOT NULL,file_id TEXT NOT NULL,path TEXT NOT NULL,hash TEXT NOT NULL,size INTEGER NOT NULL,operation TEXT NOT NULL,state TEXT NOT NULL,base_revision INTEGER,upload_id TEXT,remote_revision INTEGER,error TEXT,PRIMARY KEY(binding,operation_id));
            CREATE TABLE IF NOT EXISTS sync_heads (binding TEXT NOT NULL,file_id TEXT NOT NULL,revision INTEGER NOT NULL,path TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(binding,file_id));
            PRAGMA user_version=3; COMMIT;")?;
        let vault_id: String = db
            .query_row("SELECT id FROM identity", [], |r| r.get(0))
            .optional()?
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        db.execute(
            "INSERT INTO identity SELECT ?1 WHERE NOT EXISTS (SELECT 1 FROM identity)",
            [&vault_id],
        )?;
        let mut workspace = Self {
            root,
            vault_id,
            db,
            _lock: lock,
        };
        workspace.recover()?;
        workspace.scan()?;
        Ok(workspace)
    }

    pub fn resolve(&self, relative: &str) -> Result<PathBuf> {
        if relative.is_empty() || relative.contains('\\') || relative.starts_with('/') {
            return Err(HostError::new("UNSAFE_PATH"));
        }
        let mut path = self.root.clone();
        for component in Path::new(relative).components() {
            let Component::Normal(value) = component else {
                return Err(HostError::new("UNSAFE_PATH"));
            };
            let name = value.to_string_lossy();
            let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
            if name.eq_ignore_ascii_case(".ainote")
                || name.eq_ignore_ascii_case(".git")
                || name.ends_with(['.', ' '])
                || name
                    .chars()
                    .any(|c| c.is_control() || "<>:\"|?*".contains(c))
                || ["CON", "PRN", "AUX", "NUL"].contains(&stem.as_str())
                || (stem.len() == 4
                    && (stem.starts_with("COM") || stem.starts_with("LPT"))
                    && stem.as_bytes()[3].is_ascii_digit())
            {
                return Err(HostError::new("UNSAFE_PATH"));
            }
            path.push(value);
            if path.exists() && linked(&path)? {
                return Err(HostError::new("UNSAFE_PATH"));
            }
        }
        Ok(path)
    }

    fn entry(&self, path: &str) -> Result<Option<Entry>> {
        Ok(self
            .db
            .query_row(
                "SELECT id,path,hash,revision,deleted FROM files WHERE path=?1",
                [path],
                |r| {
                    Ok(Entry {
                        file_id: r.get(0)?,
                        path: r.get(1)?,
                        hash: r.get(2)?,
                        revision: r.get(3)?,
                        deleted: r.get(4)?,
                        is_folder: false,
                    })
                },
            )
            .optional()?)
    }

    fn scan_dir(&self, dir: &Path, paths: &mut Vec<String>) -> Result<()> {
        for item in fs::read_dir(dir)? {
            let path = item?.path();
            let relative = path
                .strip_prefix(&self.root)
                .map_err(|_| HostError::new("UNSAFE_PATH"))?
                .to_string_lossy()
                .replace('\\', "/");
            if relative
                .split('/')
                .any(|s| s.eq_ignore_ascii_case(".ainote") || s.eq_ignore_ascii_case(".git"))
                || linked(&path)?
            {
                continue;
            }
            if path.is_dir() {
                paths.push(relative);
                self.scan_dir(&path, paths)?;
            } else if path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("md"))
            {
                paths.push(relative);
            }
        }
        Ok(())
    }

    pub fn scan(&mut self) -> Result<Vec<Entry>> {
        let mut paths = Vec::new();
        self.scan_dir(&self.root, &mut paths)?;
        let mut entries = Vec::new();
        for path in paths {
            if self.resolve(&path)?.is_dir() {
                entries.push(Entry {
                    file_id: format!("folder:{path}"),
                    path,
                    hash: String::new(),
                    revision: 0,
                    deleted: false,
                    is_folder: true,
                });
                continue;
            }
            let content = fs::read(self.resolve(&path)?)?;
            let digest = hash(&content);
            let previous = self.entry(&path)?;
            if previous
                .as_ref()
                .is_none_or(|e| e.hash != digest || e.deleted)
            {
                let id = previous
                    .as_ref()
                    .map_or_else(|| Uuid::new_v4().to_string(), |e| e.file_id.clone());
                self.db.execute("INSERT INTO files VALUES (?1,?2,?3,1,0) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,revision=files.revision+1,deleted=0", params![id,path,digest])?;
            }
            entries.push(
                self.entry(&path)?
                    .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))?,
            );
        }
        Ok(entries)
    }

    pub fn read(&mut self, path: &str) -> Result<Document> {
        let content = fs::read_to_string(self.resolve(path)?)?;
        let digest = hash(content.as_bytes());
        let previous = self.entry(path)?;
        if previous
            .as_ref()
            .is_none_or(|entry| entry.hash != digest || entry.deleted)
        {
            let id = previous.map_or_else(|| Uuid::new_v4().to_string(), |entry| entry.file_id);
            self.db.execute("INSERT INTO files VALUES (?1,?2,?3,1,0) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,revision=files.revision+1,deleted=0", params![id,path,digest])?;
        }
        let entry = self
            .entry(path)?
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))?;
        if hash(content.as_bytes()) != entry.hash {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        Ok(Document { entry, content })
    }

    pub fn path_for_id(&self, file_id: &str) -> Result<String> {
        self.db
            .query_row(
                "SELECT path FROM files WHERE id=?1 AND deleted=0",
                [file_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))
    }

    pub fn write(
        &mut self,
        path: &str,
        expected: &str,
        content: &[u8],
        origin: &str,
    ) -> Result<Entry> {
        self.write_operation(path, expected, content, origin, &Uuid::new_v4().to_string())
    }

    pub fn operation(&self, operation_id: &str) -> Result<Option<serde_json::Value>> {
        let value: Option<(String, Option<String>)> = self
            .db
            .query_row(
                "SELECT state,result FROM operations WHERE operation_id=?1",
                [operation_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        value
            .map(|(state, result)| {
                let result: Option<Entry> = result
                    .map(|value| {
                        serde_json::from_str(&value).map_err(|_| HostError::new("DATABASE_ERROR"))
                    })
                    .transpose()?;
                Ok(serde_json::json!({"operation_id":operation_id,"state":state,"result":result}))
            })
            .transpose()
    }

    pub fn write_operation(
        &mut self,
        path: &str,
        expected: &str,
        content: &[u8],
        origin: &str,
        operation_id: &str,
    ) -> Result<Entry> {
        if Uuid::parse_str(operation_id).is_err() {
            return Err(HostError::new("OPERATION_ID_INVALID"));
        }
        if content.len() > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        if origin != "local" && origin != "remote" {
            return Err(HostError::new("INVALID_ORIGIN"));
        }
        let fingerprint = hash(
            &serde_json::to_vec(&(path, expected, hash(content), origin))
                .map_err(|_| HostError::new("INVALID_OPERATION"))?,
        );
        let previous: Option<String> = self
            .db
            .query_row(
                "SELECT fingerprint FROM operations WHERE operation_id=?1",
                [operation_id],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(previous) = previous {
            if previous != fingerprint {
                return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
            }
            self.recover()?;
            let receipt = self
                .operation(operation_id)?
                .ok_or_else(|| HostError::new("DATABASE_ERROR"))?;
            if receipt["state"] != "committed" {
                return Err(HostError::new("RECOVERY_CONFLICT"));
            }
            return serde_json::from_value(receipt["result"].clone())
                .map_err(|_| HostError::new("DATABASE_ERROR"));
        }
        let target = self.resolve(path)?;
        let current = if target.exists() {
            hash(&fs::read(&target)?)
        } else {
            String::new()
        };
        if current != expected {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        let file_id = self
            .entry(path)?
            .map_or_else(|| Uuid::new_v4().to_string(), |e| e.file_id);
        let tx = self.db.transaction()?;
        tx.execute(
            "INSERT INTO operations VALUES (?1,?2,'pending',NULL)",
            params![operation_id, fingerprint],
        )?;
        tx.execute(
            "INSERT INTO journal VALUES (?1,?2,?3,?4,?5,?6,'pending')",
            params![operation_id, file_id, path, expected, content, origin],
        )?;
        tx.commit()?;
        self.apply_journal(operation_id, &file_id, path, expected, content, origin)?;
        self.entry(path)?
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))
    }

    fn apply_journal(
        &mut self,
        operation_id: &str,
        file_id: &str,
        path: &str,
        expected: &str,
        content: &[u8],
        origin: &str,
    ) -> Result<()> {
        let target = self.resolve(path)?;
        let digest = hash(content);
        let current = if target.exists() {
            hash(&fs::read(&target)?)
        } else {
            String::new()
        };
        if current != expected && current != digest {
            let tx = self.db.transaction()?;
            tx.execute(
                "UPDATE journal SET state='conflict' WHERE operation_id=?1",
                [operation_id],
            )?;
            tx.execute(
                "UPDATE operations SET state='conflict' WHERE operation_id=?1",
                [operation_id],
            )?;
            tx.commit()?;
            return Err(HostError::new("RECOVERY_CONFLICT"));
        }
        if current != digest {
            let parent = target
                .parent()
                .ok_or_else(|| HostError::new("UNSAFE_PATH"))?;
            fs::create_dir_all(parent)?;
            let mut temp = tempfile::NamedTempFile::new_in(parent)?;
            temp.write_all(content)?;
            temp.as_file().sync_all()?;
            temp.persist(&target)
                .map_err(|_| HostError::new("ATOMIC_REPLACE_FAILED"))?;
            #[cfg(unix)]
            File::open(parent)?.sync_all()?;
        }
        // 文件成功但 DB 未提交时，重启凭 journal 补齐同一 operation_id，避免丢 outbox。
        let tx = self.db.transaction()?;
        tx.execute("INSERT INTO files VALUES (?1,?2,?3,1,0) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,revision=files.revision+1,deleted=0", params![file_id,path,digest])?;
        if origin == "local" {
            tx.execute("INSERT OR IGNORE INTO outbox SELECT ?1,id,revision,path,hash,'put',?2,'pending' FROM files WHERE path=?3", params![operation_id,content,path])?;
        }
        let entry = tx.query_row(
            "SELECT id,path,hash,revision,deleted FROM files WHERE path=?1",
            [path],
            |row| {
                Ok(Entry {
                    file_id: row.get(0)?,
                    path: row.get(1)?,
                    hash: row.get(2)?,
                    revision: row.get(3)?,
                    deleted: row.get(4)?,
                    is_folder: false,
                })
            },
        )?;
        let result = serde_json::to_string(&entry).map_err(|_| HostError::new("DATABASE_ERROR"))?;
        tx.execute(
            "UPDATE operations SET state='committed',result=?2 WHERE operation_id=?1",
            params![operation_id, result],
        )?;
        tx.execute("DELETE FROM journal WHERE operation_id=?1", [operation_id])?;
        tx.commit()?;
        Ok(())
    }

    pub fn recover(&mut self) -> Result<()> {
        let operations = {
            let mut statement = self
                .db
                .prepare("SELECT id FROM file_ops WHERE state='pending'")?;
            let values = statement
                .query_map([], |r| r.get::<_, String>(0))?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            values
        };
        for operation in operations {
            match self.apply_file_op(&operation) {
                Err(error) if error.code == "RECOVERY_CONFLICT" => {}
                result => result?,
            }
        }
        let pending = {
            let mut statement = self.db.prepare("SELECT operation_id,file_id,path,expected,content,origin FROM journal WHERE state='pending'")?;
            let result = statement
                .query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                        r.get::<_, String>(3)?,
                        r.get::<_, Vec<u8>>(4)?,
                        r.get::<_, String>(5)?,
                    ))
                })?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            result
        };
        for (op, id, path, expected, content, origin) in pending {
            match self.apply_journal(&op, &id, &path, &expected, &content, &origin) {
                Err(e) if e.code == "RECOVERY_CONFLICT" => {}
                result => result?,
            }
        }
        Ok(())
    }

    pub fn pending_count(&self) -> Result<i64> {
        Ok(self.db.query_row(
            "SELECT COUNT(*) FROM outbox WHERE state IN ('pending','queued')",
            [],
            |r| r.get(0),
        )?)
    }

    pub fn mkdir(&self, path: &str) -> Result<()> {
        fs::create_dir_all(self.resolve(path)?)?;
        Ok(())
    }

    pub fn rename(&mut self, path: &str, destination: &str, expected: &str) -> Result<Entry> {
        let target = self.resolve(destination)?;
        if target.exists() {
            return Err(HostError::new("PATH_CONFLICT"));
        }
        let operation = self.prepare_file_op("rename", path, destination, expected)?;
        self.apply_file_op(&operation)?;
        self.entry(destination)?
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))
    }

    pub fn delete(&mut self, path: &str, expected: &str) -> Result<()> {
        let operation = self.prepare_file_op("delete", path, "", expected)?;
        self.apply_file_op(&operation)
    }

    fn prepare_file_op(
        &mut self,
        kind: &str,
        path: &str,
        destination: &str,
        expected: &str,
    ) -> Result<String> {
        self.prepare_file_op_with_id(
            kind,
            path,
            destination,
            expected,
            &Uuid::new_v4().to_string(),
        )
    }

    pub fn mutate_operation(
        &mut self,
        kind: &str,
        path: &str,
        destination: &str,
        expected: &str,
        operation_id: &str,
    ) -> Result<serde_json::Value> {
        let id = self.prepare_file_op_with_id(kind, path, destination, expected, operation_id)?;
        if self
            .operation(&id)?
            .is_some_and(|v| v["state"] == "committed")
        {
            return self
                .operation(&id)?
                .ok_or_else(|| HostError::new("DATABASE_ERROR"));
        }
        self.apply_file_op(&id)?;
        self.operation(&id)?
            .ok_or_else(|| HostError::new("DATABASE_ERROR"))
    }

    fn prepare_file_op_with_id(
        &mut self,
        kind: &str,
        path: &str,
        destination: &str,
        expected: &str,
        id: &str,
    ) -> Result<String> {
        if !matches!(kind, "rename" | "delete") || Uuid::parse_str(id).is_err() {
            return Err(HostError::new("INVALID_OPERATION"));
        }
        let fingerprint = hash(
            &serde_json::to_vec(&(kind, path, destination, expected))
                .map_err(|_| HostError::new("INVALID_OPERATION"))?,
        );
        let previous: Option<String> = self
            .db
            .query_row(
                "SELECT fingerprint FROM operations WHERE operation_id=?1",
                [id],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(previous) = previous {
            if previous != fingerprint {
                return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
            }
            self.recover()?;
            if self
                .operation(id)?
                .is_some_and(|v| v["state"] == "conflict")
            {
                return Err(HostError::new("RECOVERY_CONFLICT"));
            }
            return Ok(id.to_owned());
        }
        if kind == "rename" && self.resolve(destination)?.exists() {
            return Err(HostError::new("PATH_CONFLICT"));
        }
        let source = self.resolve(path)?;
        if !source.is_file() {
            return Err(HostError::new("FILE_NOT_FOUND"));
        }
        let content = fs::read(source)?;
        if hash(&content) != expected {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        self.entry(path)?
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))?;
        let tx = self.db.transaction()?;
        tx.execute(
            "INSERT INTO operations VALUES (?1,?2,'pending',NULL)",
            params![id, fingerprint],
        )?;
        tx.execute(
            "INSERT INTO file_ops VALUES (?1,?2,?3,?4,?5,?6,'pending')",
            params![id, kind, path, destination, expected, content],
        )?;
        tx.commit()?;
        Ok(id.to_owned())
    }

    fn apply_file_op(&mut self, id: &str) -> Result<()> {
        let (kind, path, destination, expected, content): (
            String,
            String,
            String,
            String,
            Vec<u8>,
        ) = self.db.query_row(
            "SELECT kind,path,destination,hash,content FROM file_ops WHERE id=?1",
            [id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )?;
        let source = self.resolve(&path)?;
        let previous = self
            .entry(&path)?
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))?;
        let source_conflict = source.exists() && hash(&fs::read(&source)?) != expected;
        let target = if kind == "rename" {
            self.resolve(&destination)?
        } else {
            let trash = self.root.join(".ainote").join("trash");
            if trash.exists() && linked(&trash)? {
                return Err(HostError::new("UNSAFE_PATH"));
            }
            fs::create_dir_all(&trash)?;
            trash.join(id)
        };
        let target_conflict =
            target.exists() && (linked(&target)? || hash(&fs::read(&target)?) != expected);
        if source_conflict || target_conflict {
            let tx = self.db.transaction()?;
            tx.execute("UPDATE file_ops SET state='conflict' WHERE id=?1", [id])?;
            tx.execute(
                "UPDATE operations SET state='conflict' WHERE operation_id=?1",
                [id],
            )?;
            tx.commit()?;
            return Err(HostError::new("RECOVERY_CONFLICT"));
        }
        // journal 保留完整内容，目标刷盘后才删除来源；两处崩溃均可幂等重放。
        if !target.exists() {
            let parent = target
                .parent()
                .ok_or_else(|| HostError::new("UNSAFE_PATH"))?;
            fs::create_dir_all(parent)?;
            let mut temp = tempfile::NamedTempFile::new_in(parent)?;
            temp.write_all(&content)?;
            temp.as_file().sync_all()?;
            temp.persist_noclobber(&target)
                .map_err(|_| HostError::new("PATH_CONFLICT"))?;
        }
        if source.exists() {
            fs::remove_file(source)?;
        }
        let tx = self.db.transaction()?;
        if kind == "rename" {
            tx.execute(
                "UPDATE files SET path=?1,revision=revision+1 WHERE id=?2",
                params![destination, previous.file_id],
            )?;
            tx.execute("INSERT INTO outbox SELECT ?1,id,revision,path,hash,'put',?2,'pending' FROM files WHERE id=?3", params![id,content,previous.file_id])?;
        } else {
            tx.execute(
                "UPDATE files SET deleted=1,revision=revision+1 WHERE id=?1",
                [&previous.file_id],
            )?;
            tx.execute("INSERT INTO outbox SELECT ?1,id,revision,path,'','delete',X'','pending' FROM files WHERE id=?2", params![id,previous.file_id])?;
        }
        tx.execute("DELETE FROM file_ops WHERE id=?1", [id])?;
        let mut result = previous;
        result.revision += 1;
        if kind == "rename" {
            result.path = destination;
        } else {
            result.deleted = true;
        }
        tx.execute(
            "UPDATE operations SET state='committed',result=?2 WHERE operation_id=?1",
            params![
                id,
                serde_json::to_string(&result).map_err(|_| HostError::new("DATABASE_ERROR"))?
            ],
        )?;
        tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(windows)]
    #[test]
    fn opens_windows_verbatim_disk_path() {
        let dir = tempfile::tempdir().unwrap();
        let verbatim = dir.path().canonicalize().unwrap();
        assert!(verbatim.to_string_lossy().starts_with(r"\\?\"));

        let ws = Workspace::open(&verbatim).unwrap();
        assert_eq!(portable_path(&ws.root), portable_path(&verbatim));
    }

    #[test]
    fn rename_and_delete_recover_after_source_removed() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        let original = ws.write("a.md", "", b"safe", "local").unwrap();
        let operation = ws
            .prepare_file_op("rename", "a.md", "b.md", &original.hash)
            .unwrap();
        fs::rename(dir.path().join("a.md"), dir.path().join("b.md")).unwrap();
        ws.apply_file_op(&operation).unwrap();
        assert_eq!(ws.read("b.md").unwrap().entry.file_id, original.file_id);
        let operation = ws
            .prepare_file_op("delete", "b.md", "", &original.hash)
            .unwrap();
        fs::remove_file(dir.path().join("b.md")).unwrap();
        ws.apply_file_op(&operation).unwrap();
        assert_eq!(
            fs::read(dir.path().join(".ainote/trash").join(operation)).unwrap(),
            b"safe"
        );
        assert_eq!(ws.pending_count().unwrap(), 3);
    }

    #[test]
    fn saves_conflict_and_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        let first = ws.write("中文/笔记.md", "", b"first", "local").unwrap();
        assert!(Workspace::open(dir.path()).is_err());
        assert_eq!(
            ws.write("中文/笔记.md", "", b"lost", "local")
                .unwrap_err()
                .code,
            "REVISION_CONFLICT"
        );
        let second = ws
            .write("中文/笔记.md", &first.hash, b"second", "local")
            .unwrap();
        assert_eq!(first.file_id, second.file_id);
        assert_eq!(ws.pending_count().unwrap(), 2);
        drop(ws);
        let mut ws = Workspace::open(dir.path()).unwrap();
        assert_eq!(ws.read("中文/笔记.md").unwrap().content, "second");
        assert_eq!(ws.pending_count().unwrap(), 2);
    }

    #[test]
    fn operation_receipt_survives_reopen_and_replay_after_later_edit() {
        let dir = tempfile::tempdir().unwrap();
        let operation = Uuid::new_v4().to_string();
        let mut ws = Workspace::open(dir.path()).unwrap();
        let first = ws
            .write_operation("a.md", "", b"first", "local", &operation)
            .unwrap();
        ws.write("a.md", &first.hash, b"second", "local").unwrap();
        drop(ws);
        let mut ws = Workspace::open(dir.path()).unwrap();
        for _ in 0..100 {
            let replay = ws
                .write_operation("a.md", "", b"first", "local", &operation)
                .unwrap();
            assert_eq!(replay.revision, first.revision);
            assert_eq!(replay.hash, first.hash);
        }
        assert_eq!(ws.read("a.md").unwrap().content, "second");
        assert_eq!(ws.pending_count().unwrap(), 2);
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "committed"
        );
        assert_eq!(
            ws.write_operation("a.md", "", b"changed-payload", "local", &operation)
                .unwrap_err()
                .code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
    }

    #[test]
    fn schema_upgrade_preserves_a_readable_previous_database() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        ws.write("a.md", "", b"old-data", "local").unwrap();
        ws.db
            .execute_batch("DROP TABLE operations; PRAGMA user_version=1;")
            .unwrap();
        drop(ws);
        let ws = Workspace::open(dir.path()).unwrap();
        assert_eq!(ws.pending_count().unwrap(), 1);
        let backup = fs::read_dir(dir.path().join(".ainote"))
            .unwrap()
            .filter_map(|e| e.ok())
            .find(|e| e.file_name().to_string_lossy().starts_with("host-schema1-"))
            .unwrap();
        let old = Connection::open(backup.path()).unwrap();
        assert_eq!(
            old.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert_eq!(
            old.query_row("SELECT COUNT(*) FROM outbox", [], |row| row
                .get::<_, i64>(0))
                .unwrap(),
            1
        );
    }

    #[test]
    fn unsafe_paths_external_change_and_remote_origin() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        for path in ["../x", "/x", "x\\y", "CON.md", ".ainote/file", "a:b", "x."] {
            assert!(ws.resolve(path).is_err(), "{path}");
        }
        let first = ws.write("a.md", "", b"a", "remote").unwrap();
        assert_eq!(ws.pending_count().unwrap(), 0);
        fs::write(dir.path().join("a.md"), b"external").unwrap();
        assert_eq!(
            ws.write("a.md", &first.hash, b"lost", "local")
                .unwrap_err()
                .code,
            "REVISION_CONFLICT"
        );
    }

    #[test]
    fn crash_after_file_replace_recovers_outbox_once() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        let id = Uuid::new_v4().to_string();
        ws.db
            .execute(
                "INSERT INTO journal VALUES ('operation',?1,'a.md','',?2,'local','pending')",
                params![id, b"recovered".as_slice()],
            )
            .unwrap();
        fs::write(dir.path().join("a.md"), b"recovered").unwrap();
        ws.recover().unwrap();
        ws.recover().unwrap();
        assert_eq!(ws.pending_count().unwrap(), 1);
        assert_eq!(ws.read("a.md").unwrap().content, "recovered");
    }

    #[test]
    fn recovery_keeps_both_sides_on_external_change() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        ws.db
            .execute(
                "INSERT INTO journal VALUES ('operation','file','a.md','',?1,'local','pending')",
                [b"pending".as_slice()],
            )
            .unwrap();
        fs::write(dir.path().join("a.md"), b"external").unwrap();
        ws.recover().unwrap();
        assert_eq!(fs::read(dir.path().join("a.md")).unwrap(), b"external");
        let state: String = ws
            .db
            .query_row("SELECT state FROM journal", [], |r| r.get(0))
            .unwrap();
        assert_eq!(state, "conflict");
    }
}
