//! An experiment cannot store unaccounted data in its private registry hive.
//! Restrict only the new, Host-owned profile, before resuming any process.
use crate::workspace::{HostError, Result};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{Authorization::*, *},
    Storage::FileSystem::WRITE_DAC,
    System::Registry::*,
};

struct Key(usize);
impl Key {
    fn raw(&self) -> HKEY {
        self.0 as HKEY
    }
}
impl Drop for Key {
    fn drop(&mut self) {
        unsafe { RegCloseKey(self.raw()) };
    }
}
struct Allocation(*mut core::ffi::c_void);
impl Drop for Allocation {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { LocalFree(self.0) };
        }
    }
}

pub(crate) struct ReadOnlyRegistry {
    _keys: Vec<Key>,
}
impl ReadOnlyRegistry {
    /// The name and SID come exclusively from a successfully created Profile.
    /// A caller cannot choose another profile or redirect the current user hive.
    pub(crate) fn restrict(name: &[u16], sid: PSID) -> Result<Self> {
        let bad = || HostError::new("EXPERIMENT_REGISTRY_ISOLATION_FAILED");
        let name =
            String::from_utf16(name.strip_suffix(&[0]).ok_or_else(bad)?).map_err(|_| bad())?;
        if !name.starts_with("OpenNexus.sandbox.")
            || name.len() != "OpenNexus.sandbox.".len() + 32
            || !name["OpenNexus.sandbox.".len()..]
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(bad());
        }
        let mut raw = std::ptr::null_mut();
        if unsafe { RegOpenCurrentUser(KEY_READ, &mut raw) } != 0 || raw.is_null() {
            return Err(bad());
        }
        let user = Key(raw as usize);
        let path: Vec<u16> = format!(
            "Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\AppContainer\\Storage\\{name}"
        ).encode_utf16().chain(Some(0)).collect();
        // Opening an existing, owned key only; never create a fallback location.
        // OPEN_LINK lets us reject an alias instead of editing its target ACL.
        raw = std::ptr::null_mut();
        if unsafe {
            RegOpenKeyExW(
                user.raw(),
                path.as_ptr(),
                REG_OPTION_OPEN_LINK,
                KEY_READ | WRITE_DAC | KEY_WOW64_64KEY,
                &mut raw,
            )
        } != 0
            || raw.is_null()
        {
            return Err(bad());
        }
        let mut pending = vec![(Key(raw as usize), 0u32)];
        let mut keys = Vec::new();
        let mut count = 1;
        while let Some((key, depth)) = pending.pop() {
            reject_link(&key)?;
            let mut index = 0;
            loop {
                let mut name = [0u16; 256];
                let mut length = name.len() as u32;
                let status = unsafe {
                    RegEnumKeyExW(
                        key.raw(),
                        index,
                        name.as_mut_ptr(),
                        &mut length,
                        std::ptr::null(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                    )
                };
                if status == windows_sys::Win32::Foundation::ERROR_NO_MORE_ITEMS {
                    break;
                }
                count += 1;
                if status != 0 || length == 0 || length >= 256 || depth >= 16 || count > 64 {
                    return Err(bad());
                }
                raw = std::ptr::null_mut();
                if unsafe {
                    RegOpenKeyExW(
                        key.raw(),
                        name.as_ptr(),
                        REG_OPTION_OPEN_LINK,
                        KEY_READ | WRITE_DAC | KEY_WOW64_64KEY,
                        &mut raw,
                    )
                } != 0
                    || raw.is_null()
                {
                    return Err(bad());
                }
                pending.push((Key(raw as usize), depth + 1));
                index += 1;
            }
            restrict_key(&key, sid)?;
            keys.push(key);
        }
        // Nothing is inherited by the child. Host, owner and SYSTEM ACEs remain
        // intact. Keep the package read-only until its owned profile is deleted.
        Ok(Self { _keys: keys })
    }
}

fn reject_link(key: &Key) -> Result<()> {
    let bad = || HostError::new("EXPERIMENT_REGISTRY_ISOLATION_FAILED");
    let link: Vec<u16> = "SymbolicLinkValue\0".encode_utf16().collect();
    let mut size = 0;
    if unsafe {
        RegQueryValueExW(
            key.raw(),
            link.as_ptr(),
            std::ptr::null(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut size,
        )
    } != windows_sys::Win32::Foundation::ERROR_FILE_NOT_FOUND
    {
        return Err(bad());
    }
    Ok(())
}

fn restrict_key(key: &Key, sid: PSID) -> Result<()> {
    let bad = || HostError::new("EXPERIMENT_REGISTRY_ISOLATION_FAILED");
    let mut old_acl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            key.raw(),
            SE_REGISTRY_KEY,
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
        return Err(bad());
    }
    let entry = |mode, permissions| EXPLICIT_ACCESS_W {
        grfAccessPermissions: permissions,
        grfAccessMode: mode,
        grfInheritance: SUB_CONTAINERS_AND_OBJECTS_INHERIT,
        Trustee: TRUSTEE_W {
            TrusteeForm: TRUSTEE_IS_SID,
            TrusteeType: TRUSTEE_IS_UNKNOWN,
            ptstrName: sid.cast(),
            ..Default::default()
        },
    };
    // The default package ACE carries CR (critical) and grants all key
    // rights. Remove it instead of layering a deny which it can bypass.
    let revoke = entry(REVOKE_ACCESS, 0);
    let mut revoked = std::ptr::null_mut();
    let status = unsafe { SetEntriesInAclW(1, &revoke, old_acl, &mut revoked) };
    let _revoked = Allocation(revoked.cast());
    if status != 0 || revoked.is_null() {
        return Err(bad());
    }
    let read = entry(GRANT_ACCESS, KEY_READ);
    let mut acl = std::ptr::null_mut();
    let status = unsafe { SetEntriesInAclW(1, &read, revoked, &mut acl) };
    let _acl = Allocation(acl.cast());
    if status != 0
        || acl.is_null()
        || unsafe {
            SetSecurityInfo(
                key.raw(),
                SE_REGISTRY_KEY,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                acl,
                std::ptr::null_mut(),
            )
        } != 0
    {
        return Err(bad());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extension_container::Profile;

    fn create(parent: &Key, name: &str) -> Key {
        let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let mut raw = std::ptr::null_mut();
        assert_eq!(
            unsafe {
                RegCreateKeyExW(
                    parent.raw(),
                    name.as_ptr(),
                    0,
                    std::ptr::null(),
                    0,
                    KEY_READ | KEY_WRITE,
                    std::ptr::null(),
                    &mut raw,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        Key(raw as usize)
    }
    #[test]
    fn host_can_write_and_remove_only_its_new_profile() {
        let profile = Profile::create().unwrap();
        let guard = profile.restrict_experiment_registry().unwrap();
        let child = create(&guard._keys[0], "host-control");
        let name: Vec<u16> = "synthetic\0".encode_utf16().collect();
        let content = b"Host positive control";
        assert_eq!(
            unsafe {
                RegSetValueExW(
                    child.raw(),
                    name.as_ptr(),
                    0,
                    REG_BINARY,
                    content.as_ptr(),
                    content.len() as u32,
                )
            },
            0
        );
        drop(child);
        drop(guard);
        profile.remove().unwrap();
    }
    #[test]
    fn rejects_oversized_private_trees_and_keeps_host_cleanup() {
        let profile = Profile::create().unwrap();
        let guard = profile.restrict_experiment_registry().unwrap();
        for index in 0..64 {
            drop(create(&guard._keys[0], &format!("owned-{index}")));
        }
        assert_eq!(
            profile.restrict_experiment_registry().err().unwrap().code,
            "EXPERIMENT_REGISTRY_ISOLATION_FAILED"
        );
        drop(guard);
        profile.remove().unwrap();
    }
    #[test]
    fn rejects_registry_link_markers_before_following_and_cleans_profile() {
        let profile = Profile::create().unwrap();
        let guard = profile.restrict_experiment_registry().unwrap();
        let child = create(&guard._keys[0], "owned-marker");
        let name: Vec<u16> = "SymbolicLinkValue\0".encode_utf16().collect();
        // An ordinary synthetic key with a link marker; no actual alias to any
        // other registry object is created by this test.
        assert_eq!(
            unsafe {
                RegSetValueExW(
                    child.raw(),
                    name.as_ptr(),
                    0,
                    REG_LINK,
                    b"owned marker".as_ptr(),
                    12,
                )
            },
            0
        );
        assert_eq!(
            profile.restrict_experiment_registry().err().unwrap().code,
            "EXPERIMENT_REGISTRY_ISOLATION_FAILED"
        );
        drop(child);
        drop(guard);
        profile.remove().unwrap();
    }
}
