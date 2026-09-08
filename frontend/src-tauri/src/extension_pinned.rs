//! Windows package handles retained across verification and launch. This pins
//! existing objects; it is not a read-only mount or an ancestor-path proof.
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
}
