//! Integrity discovery only; no execution permission or process launch.
//! The expected inventory comes from the Host binary, never a mutable receipt.
use crate::workspace::{HostError, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs::File, io::Read, path::Path};

pub const RUNTIME_ID: &str = "python-3.13.16-windows-x64";
const LOCK: &str = include_str!("../../../scripts/experiment-runtime-lock.json");
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Inventory {
    schema_version: u32,
    lock: serde_json::Value,
    files: BTreeMap<String, Item>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Item {
    sha256: String,
    bytes: u64,
}
#[derive(Debug, Clone, Serialize)]
pub struct RuntimeInfo {
    pub runtime_id: String,
    pub version: String,
    pub files: usize,
}
fn invalid() -> HostError {
    HostError::new("EXPERIMENT_RUNTIME_INTEGRITY_FAILED")
}
fn safe_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('.')
        && !name.ends_with(['.', ' '])
        && !name
            .chars()
            .any(|c| c.is_control() || "/\\:*?\"<>|".contains(c))
}
fn checked(path: &Path, directory: bool) -> Result<std::fs::Metadata> {
    let metadata = std::fs::symlink_metadata(path).map_err(|_| invalid())?;
    if metadata.file_type().is_symlink()
        || (directory && !metadata.is_dir())
        || (!directory && !metadata.is_file())
    {
        return Err(invalid());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(invalid());
        }
    }
    #[cfg(unix)]
    if !directory {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return Err(invalid());
        }
    }
    Ok(metadata)
}

fn open_file(path: &Path) -> Result<File> {
    checked(path, false)?;
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(1).custom_flags(0x00200000); // OPEN_REPARSE_POINT
    }
    let file = options.open(path).map_err(|_| invalid())?;
    let metadata = file.metadata().map_err(|_| invalid())?;
    if !metadata.is_file() {
        return Err(invalid());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return Err(invalid());
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::*;
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || info.nNumberOfLinks != 1
        {
            return Err(invalid());
        }
    }
    Ok(file)
}

/// Performs a bounded, point-in-time discovery. A future run must revalidate and
/// retain pinned runtime/ancestor handles and ACL grants for its whole lifetime.
/// `expected` must be the build's embedded inventory, not caller-supplied JSON.
pub fn verify_distribution(root: &Path, expected: &str) -> Result<RuntimeInfo> {
    if !root.is_absolute() || expected.len() > 64 * 1024 {
        return Err(invalid());
    }
    let inventory: Inventory = serde_json::from_str(expected).map_err(|_| invalid())?;
    let lock: serde_json::Value = serde_json::from_str(LOCK).map_err(|_| invalid())?;
    if inventory.schema_version != 1
        || inventory.lock != lock
        || inventory.files.is_empty()
        || inventory.files.len() > 128
        || !["python.exe", "python313._pth", "LICENSE.txt"]
            .iter()
            .all(|name| inventory.files.contains_key(*name))
    {
        return Err(invalid());
    }
    for ancestor in root.ancestors() {
        checked(ancestor, true)?;
    }
    let names: std::collections::BTreeSet<_> = inventory
        .files
        .keys()
        .cloned()
        .chain(Some("runtime.json".into()))
        .collect();
    let mut actual = std::collections::BTreeSet::new();
    for entry in std::fs::read_dir(root).map_err(|_| invalid())? {
        let name = entry
            .map_err(|_| invalid())?
            .file_name()
            .into_string()
            .map_err(|_| invalid())?;
        if !names.contains(&name) {
            return Err(invalid());
        }
        actual.insert(name);
    }
    if actual != names {
        return Err(invalid());
    }
    if checked(&root.join("runtime.json"), false)?.len() != expected.len() as u64 {
        return Err(invalid());
    }
    let mut receipt = Vec::new();
    open_file(&root.join("runtime.json"))?
        .take(expected.len() as u64 + 1)
        .read_to_end(&mut receipt)
        .map_err(|_| invalid())?;
    if receipt != expected.as_bytes() {
        return Err(invalid());
    }
    let mut total = 0u64;
    for (name, item) in &inventory.files {
        total = total.checked_add(item.bytes).ok_or_else(invalid)?;
        if !safe_name(name)
            || total > 64 * 1024 * 1024
            || item.sha256.len() != 64
            || !item.sha256.bytes().all(|b| b.is_ascii_hexdigit())
        {
            return Err(invalid());
        }
        let path = root.join(name);
        if checked(&path, false)?.len() != item.bytes {
            return Err(invalid());
        }
        let mut file = open_file(&path)?;
        let mut digest = Sha256::new();
        let mut count = 0u64;
        let mut buffer = [0u8; 65536];
        loop {
            let n = file.read(&mut buffer).map_err(|_| invalid())?;
            if n == 0 {
                break;
            }
            count += n as u64;
            if count > item.bytes {
                return Err(invalid());
            }
            digest.update(&buffer[..n]);
        }
        if count != item.bytes || format!("{:x}", digest.finalize()) != item.sha256 {
            return Err(invalid());
        }
    }
    Ok(RuntimeInfo {
        runtime_id: RUNTIME_ID.into(),
        version: "3.13.16".into(),
        files: inventory.files.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(root: &Path) -> String {
        let mut files = serde_json::Map::new();
        for name in ["python.exe", "python313._pth", "LICENSE.txt"] {
            std::fs::write(root.join(name), b"locked").unwrap();
            files.insert(
                name.into(),
                serde_json::json!({"bytes": 6,
                "sha256": format!("{:x}", Sha256::digest(b"locked"))}),
            );
        }
        let manifest = serde_json::json!({"schema_version": 1,
            "lock": serde_json::from_str::<serde_json::Value>(LOCK).unwrap(), "files": files})
        .to_string();
        std::fs::write(root.join("runtime.json"), &manifest).unwrap();
        manifest
    }
    #[test]
    fn changing_files_and_receipt_does_not_change_embedded_authority() {
        let root = tempfile::tempdir().unwrap();
        let manifest = fixture(root.path());
        assert_eq!(
            verify_distribution(root.path(), &manifest).unwrap().files,
            3
        );
        std::fs::write(root.path().join("python.exe"), b"edited").unwrap();
        let forged = manifest.replace(
            &format!("{:x}", Sha256::digest(b"locked")),
            &format!("{:x}", Sha256::digest(b"edited")),
        );
        std::fs::write(root.path().join("runtime.json"), forged).unwrap();
        assert!(verify_distribution(root.path(), &manifest).is_err());
    }
    #[test]
    fn rejects_extra_files_links_and_wrong_source_lock() {
        let root = tempfile::tempdir().unwrap();
        let manifest = fixture(root.path());
        std::fs::write(root.path().join("injected.pyd"), b"extra").unwrap();
        assert!(verify_distribution(root.path(), &manifest).is_err());
        std::fs::remove_file(root.path().join("injected.pyd")).unwrap();
        let outside = tempfile::NamedTempFile::new().unwrap();
        std::fs::remove_file(root.path().join("python.exe")).unwrap();
        std::fs::write(outside.path(), b"locked").unwrap();
        std::fs::hard_link(outside.path(), root.path().join("python.exe")).unwrap();
        assert!(verify_distribution(root.path(), &manifest).is_err());
        std::fs::remove_file(root.path().join("python.exe")).unwrap();
        std::fs::write(root.path().join("python.exe"), b"locked").unwrap();
        assert!(verify_distribution(root.path(), &manifest).is_ok());
        assert!(verify_distribution(Path::new("relative"), &manifest).is_err());
        assert!(verify_distribution(root.path(), &manifest.replace("3.13.16", "0.0.0")).is_err());
    }
}
