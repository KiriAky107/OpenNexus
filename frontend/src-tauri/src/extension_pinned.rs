//! Windows package handles retained across verification and launch. This pins
//! existing objects; it is not a read-only filesystem mount.
use crate::{
    extension_container::Profile,
    extension_package::Inventory,
    workspace::{HostError, Result},
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
use cap_std::fs::{Dir, OpenOptions, OpenOptionsExt};
use std::{collections::BTreeMap, fs::File, os::windows::fs::MetadataExt};
use windows_sys::Win32::Storage::FileSystem::*;

pub struct PinnedPackage {
    directories: BTreeMap<String, Dir>,
    files: BTreeMap<String, File>,
    tree_sha256: String,
}
/// The package borrow and all ancestor handles must outlive the process using
/// this path. Only files present in the verified package can produce this guard.
pub struct BoundEntry<'a> {
    name: String,
    path: std::path::PathBuf,
    _ancestors: Vec<File>,
    _package: &'a PinnedPackage,
}
impl BoundEntry<'_> {
    pub fn relative_name(&self) -> &str {
        &self.name
    }
    pub fn tree_sha256(&self) -> &str {
        self._package.tree_sha256()
    }
    pub fn path(&self) -> &std::path::Path {
        &self.path
    }
}
fn identity(file: &File) -> Result<(u32, u32, u32)> {
    use std::os::windows::io::AsRawHandle;
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || info.nNumberOfLinks != 1
    {
        return Err(HostError::new("EXTENSION_ENTRY_BINDING_FAILED"));
    }
    Ok((
        info.dwVolumeSerialNumber,
        info.nFileIndexHigh,
        info.nFileIndexLow,
    ))
}
fn final_volume_path(file: &File) -> Result<std::path::PathBuf> {
    use std::os::windows::{ffi::OsStringExt, io::AsRawHandle};
    let bad = || HostError::new("EXTENSION_ENTRY_BINDING_FAILED");
    let length = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            std::ptr::null_mut(),
            0,
            VOLUME_NAME_GUID,
        )
    };
    if length == 0 || length >= 32767 {
        return Err(bad());
    }
    let mut buffer = vec![0u16; length as usize + 1];
    let written = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            VOLUME_NAME_GUID,
        )
    };
    if written == 0 || written as usize >= buffer.len() {
        return Err(bad());
    }
    Ok(std::path::PathBuf::from(std::ffi::OsString::from_wide(
        &buffer[..written as usize],
    )))
}
fn options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options
        .read(true)
        .access_mode(FILE_GENERIC_READ | WRITE_DAC)
        .share_mode(FILE_SHARE_READ)
        .follow(FollowSymlinks::No)
        .maybe_dir(true);
    options
}
fn directory(parent: &Dir, name: &str) -> Result<Dir> {
    let handle = parent.open_with(name, &options())?.into_std();
    let metadata = handle.metadata()?;
    if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
    }
    Ok(Dir::from_std_file(handle))
}
impl PinnedPackage {
    /// `root` and inventory originate from the verified Host store. Keep this
    /// owner until the instance stops; no renderer-supplied filesystem path is
    /// accepted here. Callers must also constrain ancestors used by native launch.
    pub fn open(root: &Dir, inventory: &Inventory, expected_tree: &str) -> Result<Self> {
        let bad = || HostError::new("EXTENSION_STORE_CORRUPT");
        if inventory.files.is_empty()
            || inventory.files.len() > 2048
            || inventory.expanded_size > 50 * 1024 * 1024
            || expected_tree.len() != 64
        {
            return Err(bad());
        }
        let mut pinned = Self {
            directories: BTreeMap::new(),
            files: BTreeMap::new(),
            tree_sha256: String::new(),
        };
        pinned
            .directories
            .insert(String::new(), directory(root, ".")?);
        for path in inventory.files.keys() {
            if path.len() > 1024 {
                return Err(bad());
            }
            let parts: Vec<_> = path.split('/').collect();
            if parts.iter().any(|p| {
                p.is_empty()
                    || *p == "."
                    || *p == ".."
                    || p.contains(['\\', ':'])
                    || p.chars().any(char::is_control)
            }) {
                return Err(bad());
            }
            let mut parent = String::new();
            for part in &parts[..parts.len() - 1] {
                let key = if parent.is_empty() {
                    (*part).to_owned()
                } else {
                    format!("{parent}/{part}")
                };
                if !pinned.directories.contains_key(&key) {
                    if pinned.directories.len() >= 10000 {
                        return Err(bad());
                    }
                    let dir = directory(&pinned.directories[&parent], part)?;
                    pinned.directories.insert(key.clone(), dir);
                }
                parent = key;
            }
            let handle = pinned.directories[&parent]
                .open_with(parts.last().unwrap(), &options())?
                .into_std();
            let metadata = handle.metadata()?;
            if !metadata.is_file() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            {
                return Err(bad());
            }
            pinned.files.insert(path.clone(), handle);
        }
        // All existing objects are already pinned when verification reopens
        // them. Sharing violations or hash mismatches release the entire set.
        pinned.tree_sha256 =
            crate::extension_unpack::verify_tree(&pinned.directories[""], inventory)?;
        if pinned.tree_sha256 != expected_tree {
            return Err(bad());
        }
        Ok(pinned)
    }
    /// Resolve through the owned file handle, then pin the volume-rooted path
    /// component by component and compare native file identity. No drive-letter
    /// or UNC fallback is permitted if volume GUID lookup is unavailable.
    pub fn bind_entry(&self, name: &str) -> Result<BoundEntry<'_>> {
        use std::{
            os::windows::fs::OpenOptionsExt as _,
            path::{Component, Prefix},
        };
        let bad = || HostError::new("EXTENSION_ENTRY_BINDING_FAILED");
        let expected = self.files.get(name).ok_or_else(bad)?;
        let path = final_volume_path(expected)?;
        let mut components = path.components();
        let Some(Component::Prefix(prefix)) = components.next() else {
            return Err(bad());
        };
        let Prefix::Verbatim(volume) = prefix.kind() else {
            return Err(bad());
        };
        let volume = volume.to_str().ok_or_else(bad)?;
        let guid = volume
            .strip_prefix("Volume{")
            .and_then(|s| s.strip_suffix('}'))
            .ok_or_else(bad)?;
        uuid::Uuid::parse_str(guid).map_err(|_| bad())?;
        if components.next() != Some(Component::RootDir) {
            return Err(bad());
        }
        let parts = components
            .map(|part| match part {
                Component::Normal(part) => Ok(part.to_owned()),
                _ => Err(bad()),
            })
            .collect::<Result<Vec<_>>>()?;
        if parts.is_empty() || parts.len() > 256 {
            return Err(bad());
        }
        let mut current = std::path::PathBuf::from(format!(r"\\?\{volume}\"));
        let mut handles = Vec::new();
        let open = |path: &std::path::Path| -> Result<File> {
            std::fs::OpenOptions::new()
                .access_mode(FILE_READ_ATTRIBUTES)
                .share_mode(FILE_SHARE_READ)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                .open(path)
                .map_err(|_| bad())
        };
        let volume_handle = open(&current)?;
        let metadata = volume_handle.metadata().map_err(|_| bad())?;
        if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(bad());
        }
        handles.push(volume_handle);
        for (index, part) in parts.iter().enumerate() {
            current.push(part);
            let handle = open(&current)?;
            let metadata = handle.metadata().map_err(|_| bad())?;
            if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
                || (index + 1 == parts.len() && !metadata.is_file())
                || (index + 1 < parts.len() && !metadata.is_dir())
            {
                return Err(bad());
            }
            handles.push(handle);
        }
        if identity(handles.last().ok_or_else(bad)?)? != identity(expected)? {
            return Err(bad());
        }
        Ok(BoundEntry {
            name: name.to_owned(),
            path: current,
            _ancestors: handles,
            _package: self,
        })
    }
    pub fn tree_sha256(&self) -> &str {
        &self.tree_sha256
    }
    pub fn grant_read_execute(&self, profile: &Profile) -> Result<()> {
        for dir in self.directories.values() {
            profile.grant_package_read_execute(&dir.try_clone()?.into_std_file())?;
        }
        for file in self.files.values() {
            profile.grant_package_read_execute(file)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    fn fixture() -> (tempfile::TempDir, Dir, Inventory, String) {
        let temp = tempfile::tempdir().unwrap();
        std::fs::create_dir(temp.path().join("sub")).unwrap();
        std::fs::write(temp.path().join("sub/entry.exe"), b"verified bytes").unwrap();
        let root = Dir::open_ambient_dir(temp.path(), cap_std::ambient_authority()).unwrap();
        let inventory = Inventory {
            files: [(
                "sub/entry.exe".into(),
                format!("{:x}", Sha256::digest(b"verified bytes")),
            )]
            .into_iter()
            .collect(),
            expanded_size: 14,
            manifest: "sub/entry.exe".into(),
        };
        let hash = crate::extension_unpack::verify_tree(&root, &inventory).unwrap();
        (temp, root, inventory, hash)
    }
    #[test]
    fn retained_handles_block_writes_deletes_and_renames_until_owner_is_dropped() {
        let (temp, root, inventory, hash) = fixture();
        let pinned = PinnedPackage::open(&root, &inventory, &hash).unwrap();
        assert_eq!(pinned.tree_sha256(), hash);
        let file = temp.path().join("sub/entry.exe");
        assert!(std::fs::write(&file, b"changed").is_err());
        assert!(std::fs::remove_file(&file).is_err());
        assert!(std::fs::rename(&file, temp.path().join("sub/other.exe")).is_err());
        assert!(std::fs::rename(temp.path().join("sub"), temp.path().join("moved")).is_err());
        let profile = Profile::create().unwrap();
        pinned.grant_read_execute(&profile).unwrap();
        assert_eq!(std::fs::read(&file).unwrap(), b"verified bytes");
        drop(pinned);
        std::fs::write(&file, b"changed").unwrap();
        profile.remove().unwrap();
    }
    #[test]
    fn existing_writer_wrong_tree_and_hardlinks_fail_without_retaining_locks() {
        let (temp, root, inventory, hash) = fixture();
        let file = temp.path().join("sub/entry.exe");
        let writer = std::fs::OpenOptions::new().write(true).open(&file).unwrap();
        assert!(PinnedPackage::open(&root, &inventory, &hash).is_err());
        drop(writer);
        assert!(PinnedPackage::open(&root, &inventory, &"0".repeat(64)).is_err());
        let outside = tempfile::tempdir().unwrap();
        std::fs::hard_link(&file, outside.path().join("alias.exe")).unwrap();
        assert!(PinnedPackage::open(&root, &inventory, &hash).is_err());
        std::fs::write(file, b"unlocked after failures").unwrap();
    }
    #[test]
    fn bound_entry_uses_volume_identity_and_holds_ancestor_rename_locks() {
        let temp = tempfile::tempdir().unwrap();
        let parent = temp.path().join("parent");
        let package = parent.join("package");
        std::fs::create_dir_all(&package).unwrap();
        std::fs::write(package.join("entry.exe"), b"verified bytes").unwrap();
        let root = Dir::open_ambient_dir(&package, cap_std::ambient_authority()).unwrap();
        let inventory = Inventory {
            files: [(
                "entry.exe".into(),
                format!("{:x}", Sha256::digest(b"verified bytes")),
            )]
            .into_iter()
            .collect(),
            expanded_size: 14,
            manifest: "entry.exe".into(),
        };
        let hash = crate::extension_unpack::verify_tree(&root, &inventory).unwrap();
        let pinned = PinnedPackage::open(&root, &inventory, &hash).unwrap();
        assert!(pinned.bind_entry("../entry.exe").is_err());
        assert!(pinned.bind_entry("missing.exe").is_err());
        let bound = pinned.bind_entry("entry.exe").unwrap();
        assert!(bound
            .path()
            .as_os_str()
            .to_string_lossy()
            .starts_with(r"\\?\Volume{"));
        assert_eq!(std::fs::read(bound.path()).unwrap(), b"verified bytes");
        let moved = temp.path().join("moved");
        assert!(std::fs::rename(&parent, &moved).is_err());
        drop(bound);
        drop(pinned);
        drop(root);
        std::fs::rename(&parent, &moved).unwrap();
        assert_eq!(
            std::fs::read(moved.join("package/entry.exe")).unwrap(),
            b"verified bytes"
        );
    }
}
