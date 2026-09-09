//! 旧 Python 扩展安装库的只读接管。
//!
//! 导入只记录来源状态，不复制或删除旧包，也不继承启用意图、信任与许可。

use crate::workspace::{HostError, Result};
use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

const MAX_RECORDS: usize = 4096;
const MAX_PACKAGE_BYTES: u64 = 50 * 1024 * 1024;
const MAX_PACKAGE_FILES: usize = 4096;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct LegacyRecord {
    path: String,
    digest: String,
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    permissions: Vec<String>,
    managed_root: Option<String>,
    #[serde(default)]
    removed: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct LegacyImport {
    pub kind: String,
    pub package_id: String,
    pub source_path: String,
    pub expected_digest: String,
    pub observed_digest: Option<String>,
    pub ownership: String,
    pub state: String,
    pub enabled: bool,
    pub permissions: Vec<String>,
}

fn digest_file(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn collect_files(root: &Path, directory: &Path, files: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink() {
            return Err(HostError::new("EXTENSION_LEGACY_UNSAFE"));
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Err(HostError::new("EXTENSION_LEGACY_UNSAFE"));
            }
        }
        if metadata.is_dir() {
            collect_files(root, &path, files)?;
        } else if metadata.is_file() {
            let relative = path
                .strip_prefix(root)
                .map_err(|_| HostError::new("EXTENSION_LEGACY_UNSAFE"))?;
            if !relative
                .components()
                .any(|part| part.as_os_str() == "__pycache__")
                && path.extension().and_then(|value| value.to_str()) != Some("pyc")
            {
                files.push(path);
                if files.len() > MAX_PACKAGE_FILES {
                    return Err(HostError::new("EXTENSION_LEGACY_TOO_LARGE"));
                }
            }
        }
    }
    Ok(())
}

fn package_digest(root: &Path) -> Result<String> {
    let root = root
        .canonicalize()
        .map_err(|_| HostError::new("EXTENSION_LEGACY_MISSING"))?;
    if !root.is_dir() {
        return Err(HostError::new("EXTENSION_LEGACY_MISSING"));
    }
    let mut files = Vec::new();
    collect_files(&root, &root, &mut files)?;
    files.sort_by_key(|path| {
        path.strip_prefix(&root)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/")
    });
    let mut total = 0_u64;
    let mut digest = Sha256::new();
    for path in files {
        let relative = path
            .strip_prefix(&root)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        digest.update(relative.as_bytes());
        digest.update([0]);
        total = total.saturating_add(path.metadata()?.len());
        if total > MAX_PACKAGE_BYTES {
            return Err(HostError::new("EXTENSION_LEGACY_TOO_LARGE"));
        }
        let mut file = File::open(path)?;
        let mut buffer = [0_u8; 1024 * 1024];
        loop {
            let count = file.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            digest.update(&buffer[..count]);
        }
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn normal_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
}

fn classify(record: &LegacyRecord, managed_storage: &Path) -> Result<LegacyImport> {
    let source = PathBuf::from(&record.path);
    if !source.is_absolute()
        || record.digest.len() != 64
        || !record.digest.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err(HostError::new("EXTENSION_LEGACY_INVALID"));
    }
    let managed = record.managed_root.as_ref().is_some_and(|raw| {
        let root = PathBuf::from(raw);
        root.is_absolute() && root.parent() == Some(managed_storage) && source.starts_with(root)
    });
    let observed = if source.is_dir() {
        Some(package_digest(&source)?)
    } else {
        None
    };
    let state = if record.removed {
        "removed"
    } else if observed.is_none() {
        "missing"
    } else if observed.as_deref() != Some(record.digest.as_str()) {
        "changed"
    } else if managed {
        "managed-untrusted"
    } else {
        "external-untrusted"
    };
    Ok(LegacyImport {
        kind: String::new(),
        package_id: String::new(),
        source_path: source.to_string_lossy().into_owned(),
        expected_digest: record.digest.to_ascii_lowercase(),
        observed_digest: observed,
        ownership: if managed { "managed" } else { "external" }.to_string(),
        state: state.to_string(),
        enabled: false,
        permissions: Vec::new(),
    })
}

pub fn import(
    db: &mut Connection,
    host_root: &Path,
    legacy_data_root: &Path,
) -> Result<Vec<LegacyImport>> {
    let source = legacy_data_root.join("extension-installations.sqlite3");
    if !source.exists() {
        return Ok(Vec::new());
    }
    if fs::symlink_metadata(&source)?.file_type().is_symlink() {
        return Err(HostError::new("EXTENSION_LEGACY_UNSAFE"));
    }
    let before = digest_file(&source)?;
    let legacy = Connection::open_with_flags(
        &source,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    let table: i64 = legacy.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='installations'",
        [],
        |row| row.get(0),
    )?;
    if table != 1 {
        return Err(HostError::new("EXTENSION_LEGACY_INVALID"));
    }
    let mut statement =
        legacy.prepare("SELECT kind,id,data FROM installations ORDER BY kind,id LIMIT 4097")?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    let mut imported = Vec::new();
    let managed_storage = legacy_data_root.join("extension-packages");
    for row in rows {
        if imported.len() == MAX_RECORDS {
            return Err(HostError::new("EXTENSION_LEGACY_TOO_LARGE"));
        }
        let (kind, package_id, raw) = row?;
        if !matches!(kind.as_str(), "skill" | "plugin")
            || !normal_id(&package_id)
            || raw.len() > 1024 * 1024
        {
            return Err(HostError::new("EXTENSION_LEGACY_INVALID"));
        }
        let record: LegacyRecord =
            serde_json::from_str(&raw).map_err(|_| HostError::new("EXTENSION_LEGACY_INVALID"))?;
        let mut item = classify(&record, &managed_storage)?;
        item.kind = kind;
        item.package_id = package_id;
        imported.push(item);
    }
    drop(statement);
    drop(legacy);
    if digest_file(&source)? != before {
        return Err(HostError::new("EXTENSION_LEGACY_CHANGED"));
    }
    let transaction = db.transaction()?;
    for item in &imported {
        transaction.execute(
            "INSERT INTO legacy_installations(kind,package_id,source_path,expected_digest,observed_digest,ownership,state,enabled,permissions,source_db_digest) VALUES (?1,?2,?3,?4,?5,?6,?7,0,'[]',?8) ON CONFLICT(kind,package_id) DO UPDATE SET source_path=excluded.source_path,expected_digest=excluded.expected_digest,observed_digest=excluded.observed_digest,ownership=excluded.ownership,state=excluded.state,enabled=0,permissions='[]',source_db_digest=excluded.source_db_digest",
            params![item.kind,item.package_id,item.source_path,item.expected_digest,item.observed_digest,item.ownership,item.state,before])?;
    }
    transaction.commit()?;
    let marker = legacy_data_root.join("extension-installations.rust-owned.json");
    let marker_data = serde_json::to_vec(&serde_json::json!({"schema":1,"owner":"rust-host","source_db_sha256":before,"host_root":host_root.to_string_lossy()})).unwrap();
    let temporary = legacy_data_root.join("extension-installations.rust-owned.tmp");
    {
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(&marker_data)?;
        file.sync_all()?;
    }
    fs::rename(temporary, marker)?;
    Ok(imported)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn write_package(path: &Path, content: &[u8]) -> String {
        fs::create_dir_all(path).unwrap();
        fs::write(path.join("entry.py"), content).unwrap();
        package_digest(path).unwrap()
    }

    #[test]
    fn d02_read_only_import_classifies_four_groups_and_is_idempotent() {
        let legacy_root = tempdir().unwrap();
        let host_root = tempdir().unwrap();
        let managed_root = legacy_root.path().join("extension-packages/managed-a");
        let managed_package = managed_root.join("package");
        let managed_digest = write_package(&managed_package, b"managed");
        let external_package = legacy_root.path().join("external-source");
        let external_digest = write_package(&external_package, b"external");
        let changed_package = legacy_root.path().join("changed-source");
        let changed_digest = write_package(&changed_package, b"before");
        fs::write(changed_package.join("entry.py"), b"after").unwrap();
        let missing_package = legacy_root.path().join("missing-source");

        let legacy_db_path = legacy_root.path().join("extension-installations.sqlite3");
        let legacy_db = Connection::open(&legacy_db_path).unwrap();
        legacy_db
            .execute_batch(
                "CREATE TABLE installations(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));",
            )
            .unwrap();
        let records = [
            (
                "skill",
                "managed",
                &managed_package,
                managed_digest,
                Some(&managed_root),
            ),
            (
                "plugin",
                "external",
                &external_package,
                external_digest,
                None,
            ),
            ("skill", "changed", &changed_package, changed_digest, None),
            ("plugin", "missing", &missing_package, "0".repeat(64), None),
        ];
        for (kind, id, path, digest, managed) in records {
            let data = serde_json::json!({
                "path": path.to_string_lossy(), "digest": digest, "enabled": true,
                "permissions": ["notes.write"],
                "managed_root": managed.map(|value| value.to_string_lossy().into_owned()),
                "removed": false,
            });
            legacy_db
                .execute(
                    "INSERT INTO installations VALUES (?1,?2,?3)",
                    params![kind, id, data.to_string()],
                )
                .unwrap();
        }
        drop(legacy_db);
        let source_before = digest_file(&legacy_db_path).unwrap();
        let external_before = package_digest(&external_package).unwrap();

        let mut host_db = Connection::open(host_root.path().join("host.sqlite3")).unwrap();
        host_db.execute_batch("CREATE TABLE legacy_installations(kind TEXT NOT NULL,package_id TEXT NOT NULL,source_path TEXT NOT NULL,expected_digest TEXT NOT NULL,observed_digest TEXT,ownership TEXT NOT NULL,state TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled=0),permissions TEXT NOT NULL CHECK(permissions='[]'),source_db_digest TEXT NOT NULL,PRIMARY KEY(kind,package_id));").unwrap();
        for _ in 0..3 {
            let result = import(&mut host_db, host_root.path(), legacy_root.path()).unwrap();
            assert_eq!(result.len(), 4);
            assert!(result
                .iter()
                .all(|item| !item.enabled && item.permissions.is_empty()));
        }
        let states: Vec<(String, String)> = host_db
            .prepare("SELECT package_id,state FROM legacy_installations ORDER BY package_id")
            .unwrap()
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
            .unwrap()
            .map(std::result::Result::unwrap)
            .collect();
        assert_eq!(
            states,
            vec![
                ("changed".into(), "changed".into()),
                ("external".into(), "external-untrusted".into()),
                ("managed".into(), "managed-untrusted".into()),
                ("missing".into(), "missing".into()),
            ]
        );
        assert_eq!(
            host_db
                .query_row("SELECT COUNT(*) FROM legacy_installations", [], |row| row
                    .get::<_, i64>(
                    0
                ))
                .unwrap(),
            4
        );
        assert_eq!(digest_file(&legacy_db_path).unwrap(), source_before);
        assert_eq!(package_digest(&external_package).unwrap(), external_before);
        assert!(legacy_root
            .path()
            .join("extension-installations.rust-owned.json")
            .is_file());
    }
}
