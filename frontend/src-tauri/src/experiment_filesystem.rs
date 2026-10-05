//! Restrict only the fresh, owned profile and pin its existing objects.
//! Default-created scratch objects inherit modify access without WRITE_DAC.
//! Windows permits explicit security descriptors on new objects; do not treat
//! inheritance as a guarantee about every script-created child's ACL. Inspection
//! and cleanup failures must remain real failures, never an empty output list.
use crate::{
    extension_container::Profile,
    workspace::{HostError, Result},
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions as CapOptions, OpenOptionsExt};
use std::{
    fs::File,
    os::windows::{fs::MetadataExt, io::AsRawHandle},
    path::Path,
};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
};

pub(crate) struct Filesystem {
    _objects: Vec<File>,
}
struct Allocation(*mut core::ffi::c_void);
impl Drop for Allocation {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { LocalFree(self.0) };
        }
    }
}
fn failed() -> HostError {
    HostError::new("EXPERIMENT_FILESYSTEM_ISOLATION_FAILED")
}
fn inspect(file: &File) -> Result<std::fs::Metadata> {
    let metadata = file.metadata().map_err(|_| failed())?;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || !(metadata.is_file() || metadata.is_dir())
    {
        return Err(failed());
    }
    if metadata.is_file() {
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.nNumberOfLinks != 1
        {
            return Err(failed());
        }
    }
    Ok(metadata)
}
impl Filesystem {
    /// The only ambient root comes from this newly created Profile, never a
    /// request. Pin its existing tree before changing ACLs; no link is followed.
    pub(crate) fn restrict(profile: &Profile, scratch: &Path) -> Result<Self> {
        use std::os::windows::fs::OpenOptionsExt as StdOptionsExt;
        let folder = profile.folder()?;
        if scratch != folder.join("Temp") {
            return Err(failed());
        }
        let root = folder.parent().ok_or_else(failed)?;
        let file = std::fs::OpenOptions::new()
            .access_mode(FILE_GENERIC_READ | WRITE_DAC)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(root)
            .map_err(|_| failed())?;
        if !inspect(&file)?.is_dir() {
            return Err(failed());
        }
        let mut objects = vec![(file.try_clone().map_err(|_| failed())?, String::new())];
        let mut pending = vec![(Dir::from_std_file(file), String::new(), 0usize)];
        let mut options = CapOptions::new();
        options
            .read(true)
            .access_mode(FILE_GENERIC_READ | WRITE_DAC)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .follow(FollowSymlinks::No)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
        while let Some((directory, parent, depth)) = pending.pop() {
            for entry in directory.entries().map_err(|_| failed())? {
                if objects.len() >= 128 || depth >= 16 {
                    return Err(failed());
                }
                let entry = entry.map_err(|_| failed())?;
                let name = entry.file_name().into_string().map_err(|_| failed())?;
                if name.is_empty() || name.contains(['/', '\\', ':']) {
                    return Err(failed());
                }
                let relative = if parent.is_empty() {
                    name
                } else {
                    format!("{parent}/{name}")
                };
                let file = entry.open_with(&options).map_err(|_| failed())?.into_std();
                let metadata = inspect(&file)?;
                if metadata.is_dir() {
                    pending.push((
                        Dir::from_std_file(file.try_clone().map_err(|_| failed())?),
                        relative.clone(),
                        depth + 1,
                    ));
                }
                objects.push((file, relative));
            }
        }
        if !objects
            .iter()
            .any(|(_, path)| path.eq_ignore_ascii_case("AC/Temp"))
        {
            return Err(failed());
        }
        // Parent first: remove inherited package-full grants before adding the
        // explicit scratch-modify grant. Host/SYSTEM and other SIDs are retained.
        objects.sort_by_key(|(_, path)| path.matches('/').count() + usize::from(!path.is_empty()));
        for (file, relative) in &objects {
            let path = relative.to_ascii_lowercase();
            let writable = path == "ac/temp" || path.starts_with("ac/temp/");
            restrict_object(file, profile.sid(), writable)?;
        }
        Ok(Self {
            _objects: objects.into_iter().map(|(file, _)| file).collect(),
        })
    }
}
fn restrict_object(file: &File, sid: PSID, writable: bool) -> Result<()> {
    let mut old_acl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut old_acl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    let _descriptor = Allocation(descriptor);
    if status != 0 || old_acl.is_null() || unsafe { IsValidAcl(old_acl) } == 0 {
        return Err(failed());
    }
    let entry = |mode, permissions| EXPLICIT_ACCESS_W {
        grfAccessPermissions: permissions,
        grfAccessMode: mode,
        grfInheritance: if file.metadata().map(|m| m.is_dir()).unwrap_or(false) {
            SUB_CONTAINERS_AND_OBJECTS_INHERIT
        } else {
            0
        },
        Trustee: TRUSTEE_W {
            TrusteeForm: TRUSTEE_IS_SID,
            TrusteeType: TRUSTEE_IS_UNKNOWN,
            ptstrName: sid.cast(),
            ..Default::default()
        },
    };
    // Default profile package-full ACEs bypass a layered deny. Replace only
    // this exact SID's grant, as with the already sealed private registry.
    let revoke = entry(REVOKE_ACCESS, 0);
    let mut revoked = std::ptr::null_mut();
    let status = unsafe { SetEntriesInAclW(1, &revoke, old_acl, &mut revoked) };
    let _revoked = Allocation(revoked.cast());
    if status != 0 || revoked.is_null() {
        return Err(failed());
    }
    let permissions = FILE_GENERIC_READ
        | FILE_GENERIC_EXECUTE
        | if writable {
            FILE_GENERIC_WRITE | DELETE
        } else {
            0
        };
    let grant = entry(GRANT_ACCESS, permissions);
    let mut acl = std::ptr::null_mut();
    let status = unsafe { SetEntriesInAclW(1, &grant, revoked, &mut acl) };
    let _acl = Allocation(acl.cast());
    if status != 0
        || acl.is_null()
        || unsafe {
            SetSecurityInfo(
                file.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                acl,
                std::ptr::null_mut(),
            )
        } != 0
    {
        return Err(failed());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::windows::fs::OpenOptionsExt;

    fn open(path: &Path) -> File {
        std::fs::OpenOptions::new()
            .access_mode(FILE_GENERIC_READ | WRITE_DAC)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .unwrap()
    }
    fn other_entries(file: &File, sid: PSID) -> Vec<Vec<u8>> {
        let mut entries: Vec<_> = crate::extension_container::test_acl_entries(file)
            .into_iter()
            .filter(|ace| {
                assert!(ace.len() >= 16 && matches!(ace[0], 0 | 1));
                unsafe { EqualSid(ace.as_ptr().add(8).cast_mut().cast(), sid) == 0 }
            })
            .map(|mut ace| {
                // SetSecurityInfo can normalize inherited flags. Compare the
                // actual trustees and access masks, which must be preserved.
                ace[1] = 0;
                ace
            })
            .collect();
        entries.sort();
        entries.dedup();
        entries
    }
    #[test]
    fn experiment_filesystem_preserves_host_access_and_pins_the_owned_tree() {
        let profile = Profile::create().unwrap();
        let folder = profile.folder().unwrap();
        let scratch = folder.join("Temp");
        std::fs::create_dir_all(&scratch).unwrap();
        std::fs::write(scratch.join("existing.txt"), b"old").unwrap();
        let root = folder.parent().unwrap();
        let root_before = other_entries(&open(root), profile.sid());
        let scratch_before = other_entries(&open(&scratch), profile.sid());
        let guard = Filesystem::restrict(&profile, &scratch).unwrap();
        assert_eq!(other_entries(&open(root), profile.sid()), root_before);
        assert_eq!(
            other_entries(&open(&scratch), profile.sid()),
            scratch_before
        );
        std::fs::write(scratch.join("existing.txt"), b"updated").unwrap();
        std::fs::write(scratch.join("new.txt"), b"new").unwrap();
        std::fs::remove_file(scratch.join("new.txt")).unwrap();
        std::fs::write(folder.join("host-only.txt"), b"Host control").unwrap();
        assert_eq!(
            std::fs::read(scratch.join("existing.txt")).unwrap(),
            b"updated"
        );
        assert_eq!(
            std::fs::OpenOptions::new()
                .access_mode(DELETE)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
                .open(root)
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(guard);
        profile.remove().unwrap();
        assert!(!root.exists());
    }
    #[test]
    fn experiment_filesystem_rejects_unsafe_or_unbounded_trees_before_acl_changes() {
        let profile = Profile::create().unwrap();
        let folder = profile.folder().unwrap();
        let scratch = folder.join("Temp");
        std::fs::create_dir_all(&scratch).unwrap();
        let original = crate::extension_container::test_acl_entries(&open(&scratch));
        assert!(Filesystem::restrict(&profile, &folder).is_err());
        let external = tempfile::tempdir().unwrap();
        let target = external.path().join("owned-sentinel.txt");
        std::fs::write(&target, b"unchanged").unwrap();
        let target_acl = crate::extension_container::test_acl_entries(&open(&target));
        std::fs::hard_link(&target, scratch.join("linked.txt")).unwrap();
        assert!(Filesystem::restrict(&profile, &scratch).is_err());
        assert_eq!(
            crate::extension_container::test_acl_entries(&open(&target)),
            target_acl
        );
        std::fs::remove_file(scratch.join("linked.txt")).unwrap();
        let link = scratch.join("junction");
        let command = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&link)
            .arg(external.path())
            .output()
            .unwrap();
        assert!(command.status.success());
        assert!(Filesystem::restrict(&profile, &scratch).is_err());
        std::fs::remove_dir(link).unwrap();
        for index in 0..128 {
            std::fs::write(scratch.join(format!("object-{index}.txt")), b"bounded").unwrap();
        }
        assert!(Filesystem::restrict(&profile, &scratch).is_err());
        assert_eq!(
            crate::extension_container::test_acl_entries(&open(&scratch)),
            original
        );
        assert_eq!(std::fs::read(target).unwrap(), b"unchanged");
        profile.remove().unwrap();
    }
}
