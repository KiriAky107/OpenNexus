//! Private, capability-relative extraction. Prepared trees are not executable installs.
use crate::{
    extension_package::{Inventory, Release},
    workspace::{HostError, Result},
};
use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    io::{Cursor, Read, Write},
};

pub struct Prepared {
    pub directory: String,
    pub tree_sha256: String,
    pub inventory: Inventory,
}

fn sync_dir(dir: &Dir) -> Result<()> {
    #[cfg(unix)]
    dir.try_clone()?.into_std_file().sync_all()?;
    #[cfg(not(unix))]
    let _ = dir;
    Ok(())
}

fn parent(root: &Dir, path: &str, create: bool) -> Result<(Dir, String)> {
    let mut dir = root.try_clone()?;
    let mut parts = path.split('/').peekable();
    while let Some(part) = parts.next() {
        if part.is_empty() || part == "." || part == ".." || part.contains(['\\', ':']) {
            return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
        }
        if parts.peek().is_none() {
            return Ok((dir, part.into()));
        }
        if create {
            match dir.create_dir(part) {
                Ok(()) => {
                    sync_dir(&dir)?;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(e) => return Err(e.into()),
            }
        }
        dir = dir.open_dir_nofollow(part)?;
    }
    Err(HostError::new("EXTENSION_STORE_UNSAFE"))
}

fn collect(
    dir: &Dir,
    prefix: &str,
    found: &mut BTreeSet<String>,
    remaining: &mut usize,
    inventory: &Inventory,
) -> Result<()> {
    for item in dir.entries()? {
        if *remaining == 0 {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        *remaining -= 1;
        let item = item?;
        let name = item
            .file_name()
            .into_string()
            .map_err(|_| HostError::new("EXTENSION_STORE_UNSAFE"))?;
        let path = format!("{prefix}{name}");
        let meta = dir.symlink_metadata(&name)?;
        if meta.file_type().is_symlink() {
            return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
        }
        if meta.is_dir() {
            if !inventory
                .files
                .keys()
                .any(|p| p.starts_with(&format!("{path}/")))
            {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            collect(
                &dir.open_dir_nofollow(&name)?,
                &format!("{path}/"),
                found,
                remaining,
                inventory,
            )?;
        } else if meta.is_file() {
            found.insert(path);
        } else {
            return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
        }
    }
    Ok(())
}

/// Re-read the exact file set and all content through directory capabilities.
pub fn verify_tree(root: &Dir, inventory: &Inventory) -> Result<String> {
    let mut found = BTreeSet::new();
    collect(root, "", &mut found, &mut 10000, inventory)?;
    if found != inventory.files.keys().cloned().collect() {
        return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
    }
    let mut total = 0u64;
    let mut tree = Sha256::new();
    for (path, expected) in &inventory.files {
        let (dir, name) = parent(root, path, false)?;
        let mut options = OpenOptions::new();
        options.read(true).follow(FollowSymlinks::No);
        let file = dir.open_with(name, &options)?.into_std();
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
        }
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
            let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
            if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
                || info.nNumberOfLinks != 1
                || info.dwFileAttributes & 0x400 != 0
            {
                return Err(HostError::new("EXTENSION_STORE_UNSAFE"));
            }
        }
        let mut content = Sha256::new();
        let mut reader = file.take(inventory.expanded_size.saturating_sub(total) + 1);
        tree.update((path.len() as u64).to_be_bytes());
        tree.update(path.as_bytes());
        tree.update(metadata.len().to_be_bytes());
        let mut buffer = [0u8; 65536];
        loop {
            let n = reader.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            total += n as u64;
            if total > inventory.expanded_size {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            content.update(&buffer[..n]);
            tree.update(&buffer[..n]);
        }
        if format!("{:x}", content.finalize()) != *expected {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
    }
    if total != inventory.expanded_size {
        return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
    }
    Ok(format!("{:x}", tree.finalize()))
}

/// `root` must be an application-owned private staging directory. Trust freshness
/// and permission grants remain installation-layer responsibilities.
pub fn prepare(
    root: &Dir,
    release: &Release,
    key: &[u8; 32],
    key_id: &str,
    namespace: &str,
    archive: &[u8],
) -> Result<Prepared> {
    let (inventory, _) = release.verify_package(key, key_id, namespace, false, false, archive)?;
    let directory = uuid::Uuid::new_v4().to_string();
    root.create_dir(&directory)?;
    sync_dir(root)?;
    let target = root.open_dir_nofollow(&directory)?;
    let mut zip = zip::ZipArchive::new(Cursor::new(archive))
        .map_err(|_| HostError::new("EXTENSION_ZIP_INVALID"))?;
    for (path, expected) in &inventory.files {
        let mut entry = zip
            .by_name(path)
            .map_err(|_| HostError::new("EXTENSION_ZIP_INVALID"))?;
        let (dir, name) = parent(&target, path, true)?;
        let mut options = OpenOptions::new();
        options
            .write(true)
            .create_new(true)
            .follow(FollowSymlinks::No);
        let mut output = dir.open_with(name, &options)?;
        let mut digest = Sha256::new();
        let mut buffer = [0u8; 65536];
        loop {
            let n = entry.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            output.write_all(&buffer[..n])?;
            digest.update(&buffer[..n]);
        }
        output.sync_all()?;
        sync_dir(&dir)?;
        if format!("{:x}", digest.finalize()) != *expected {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
    }
    let tree_sha256 = verify_tree(&target, &inventory)?;
    // Failed preparations remain isolated UUID directories; never expose them as current.
    Ok(Prepared {
        directory,
        tree_sha256,
        inventory,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::hash;
    use base64::{engine::general_purpose::STANDARD, Engine};
    #[cfg(windows)]
    #[test]
    fn junction_parent_cannot_redirect_output() {
        let temp = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let junction = temp.path().join("redirect");
        let status = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(outside.path())
            .output()
            .unwrap();
        assert!(status.status.success(), "junction fixture creation failed");
        let root = Dir::open_ambient_dir(temp.path(), cap_std::ambient_authority()).unwrap();
        assert!(parent(&root, "redirect/escaped.txt", true).is_err());
        assert_eq!(std::fs::read_dir(outside.path()).unwrap().count(), 0);
        std::fs::remove_dir(junction).unwrap();
    }
    #[test]
    fn signed_package_extracts_and_rejects_modified_extra_missing_and_linked_files() {
        let mut data: serde_json::Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        for field in ["release_id", "withdrawn", "download_path"] {
            data["release"].as_object_mut().unwrap().remove(field);
        }
        let release: Release = serde_json::from_value(data["release"].clone()).unwrap();
        let bytes = STANDARD
            .decode(data["archive_base64"].as_str().unwrap())
            .unwrap();
        let key: [u8; 32] = STANDARD
            .decode(data["key"]["public_key"].as_str().unwrap())
            .unwrap()
            .try_into()
            .unwrap();
        let temp = tempfile::tempdir().unwrap();
        let root = Dir::open_ambient_dir(temp.path(), cap_std::ambient_authority()).unwrap();
        let prepared = prepare(&root, &release, &key, "test-key", "examples", &bytes).unwrap();
        let target = root.open_dir_nofollow(&prepared.directory).unwrap();
        assert_eq!(
            verify_tree(&target, &prepared.inventory).unwrap(),
            prepared.tree_sha256
        );
        target.create_dir("unexpected-empty").unwrap();
        assert!(verify_tree(&target, &prepared.inventory).is_err());
        target.remove_dir("unexpected-empty").unwrap();
        target.write("extra", b"x").unwrap();
        assert!(verify_tree(&target, &prepared.inventory).is_err());
        target.remove_file("extra").unwrap();
        let original = target.read("persona.json").unwrap();
        target.write("persona.json", b"bad").unwrap();
        assert!(verify_tree(&target, &prepared.inventory).is_err());
        target.remove_file("persona.json").unwrap();
        assert!(verify_tree(&target, &prepared.inventory).is_err());
        let outside = temp.path().join("outside");
        std::fs::write(&outside, &original).unwrap();
        std::fs::hard_link(
            &outside,
            temp.path().join(&prepared.directory).join("persona.json"),
        )
        .unwrap();
        assert!(verify_tree(&target, &prepared.inventory).is_err());
        assert_eq!(hash(&std::fs::read(outside).unwrap()), hash(&original));
        assert!(parent(&target, "../outside", true).is_err());
        let mut corrupt = bytes.clone();
        corrupt[0] ^= 1;
        assert!(prepare(&root, &release, &key, "test-key", "examples", &corrupt).is_err());
    }
}
