//! Per-instance AppContainer profile ownership. No existing profile is adopted.
use crate::workspace::{HostError, Result};
use windows_sys::Win32::Security::{
    FreeSid, IsValidSid,
    Isolation::{CreateAppContainerProfile, DeleteAppContainerProfile},
    PSID,
};

pub struct Profile {
    name: Vec<u16>,
    sid: PSID,
    exists: bool,
}
impl Profile {
    pub fn create() -> Result<Self> {
        Self::create_named(format!(
            "OpenNexus.sandbox.{}",
            uuid::Uuid::new_v4().simple()
        ))
    }
    fn create_named(name: String) -> Result<Self> {
        let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let mut sid = std::ptr::null_mut();
        let status = unsafe {
            CreateAppContainerProfile(
                name.as_ptr(),
                name.as_ptr(),
                name.as_ptr(),
                std::ptr::null(),
                0,
                &mut sid,
            )
        };
        if status < 0 {
            // In particular, ERROR_ALREADY_EXISTS must not transfer ownership.
            if !sid.is_null() {
                unsafe {
                    FreeSid(sid);
                }
            }
            return Err(HostError::new("EXTENSION_CONTAINER_CREATE_FAILED"));
        }
        let profile = Self {
            name,
            sid,
            exists: true,
        };
        if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
            return Err(HostError::new("EXTENSION_CONTAINER_SID_INVALID"));
        }
        Ok(profile)
    }
    /// Borrowed SID for SECURITY_CAPABILITIES. Valid only while this owner lives.
    pub fn sid(&self) -> PSID {
        self.sid
    }
    /// Grant this instance read/execute access to one Host-owned package object.
    /// The caller must open it without following reparse points and retain the
    /// verified package handles for the entire launch. No recursive inheritance
    /// is used: every directory and file must be checked and granted separately.
    /// This adds an ACE; it does not sanitize pre-existing permissions.
    pub fn grant_package_read_execute(&self, object: &std::fs::File) -> Result<()> {
        use std::os::windows::{fs::MetadataExt, io::AsRawHandle};
        use windows_sys::Win32::{
            Foundation::LocalFree,
            Security::{Authorization::*, DACL_SECURITY_INFORMATION},
            Storage::FileSystem::{
                GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
                FILE_ATTRIBUTE_REPARSE_POINT, FILE_GENERIC_EXECUTE, FILE_GENERIC_READ,
            },
        };
        struct LocalAllocation(*mut core::ffi::c_void);
        impl Drop for LocalAllocation {
            fn drop(&mut self) {
                if !self.0.is_null() {
                    unsafe {
                        LocalFree(self.0);
                    }
                }
            }
        }
        let metadata = object
            .metadata()
            .map_err(|_| HostError::new("EXTENSION_CONTAINER_ACL_FAILED"))?;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || !(metadata.is_file() || metadata.is_dir())
        {
            return Err(HostError::new("EXTENSION_CONTAINER_ACL_OBJECT_INVALID"));
        }
        if metadata.is_file() {
            let mut info = BY_HANDLE_FILE_INFORMATION::default();
            if unsafe { GetFileInformationByHandle(object.as_raw_handle(), &mut info) } == 0
                || info.nNumberOfLinks != 1
            {
                return Err(HostError::new("EXTENSION_CONTAINER_ACL_OBJECT_INVALID"));
            }
        }
        let mut old_acl = std::ptr::null_mut();
        let mut descriptor = std::ptr::null_mut();
        let status = unsafe {
            GetSecurityInfo(
                object.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut old_acl,
                std::ptr::null_mut(),
                &mut descriptor,
            )
        };
        let _descriptor = LocalAllocation(descriptor);
        // A null DACL grants everyone full access, so fail closed rather than
        // silently treating it as a suitably isolated package object.
        if status != 0 || old_acl.is_null() {
            return Err(HostError::new("EXTENSION_CONTAINER_ACL_FAILED"));
        }
        let entry = EXPLICIT_ACCESS_W {
            grfAccessPermissions: FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
            grfAccessMode: GRANT_ACCESS,
            grfInheritance: 0,
            Trustee: TRUSTEE_W {
                TrusteeForm: TRUSTEE_IS_SID,
                TrusteeType: TRUSTEE_IS_UNKNOWN,
                ptstrName: self.sid.cast(),
                ..Default::default()
            },
        };
        let mut acl = std::ptr::null_mut();
        let status = unsafe { SetEntriesInAclW(1, &entry, old_acl, &mut acl) };
        let _acl = LocalAllocation(acl.cast());
        if status != 0 || acl.is_null() {
            return Err(HostError::new("EXTENSION_CONTAINER_ACL_FAILED"));
        }
        let status = unsafe {
            SetSecurityInfo(
                object.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                acl,
                std::ptr::null(),
            )
        };
        if status != 0 {
            return Err(HostError::new("EXTENSION_CONTAINER_ACL_FAILED"));
        }
        Ok(())
    }
    pub fn folder(&self) -> Result<std::path::PathBuf> {
        use std::os::windows::ffi::OsStringExt;
        use windows_sys::Win32::{
            Foundation::LocalFree,
            Security::{
                Authorization::ConvertSidToStringSidW, Isolation::GetAppContainerFolderPath,
            },
            System::Com::CoTaskMemFree,
        };
        let mut string = std::ptr::null_mut();
        if unsafe { ConvertSidToStringSidW(self.sid, &mut string) } == 0 {
            return Err(HostError::new("EXTENSION_CONTAINER_SID_INVALID"));
        }
        let mut folder = std::ptr::null_mut();
        let status = unsafe { GetAppContainerFolderPath(string, &mut folder) };
        unsafe {
            LocalFree(string.cast());
        }
        if status < 0 || folder.is_null() {
            if !folder.is_null() {
                unsafe {
                    CoTaskMemFree(folder.cast());
                }
            }
            return Err(HostError::new("EXTENSION_CONTAINER_FOLDER_FAILED"));
        }
        let mut length = 0;
        while length < 32768 && unsafe { *folder.add(length) } != 0 {
            length += 1;
        }
        let result = if length == 32768 {
            Err(HostError::new("EXTENSION_CONTAINER_FOLDER_FAILED"))
        } else {
            Ok(std::path::PathBuf::from(std::ffi::OsString::from_wide(
                unsafe { std::slice::from_raw_parts(folder, length) },
            )))
        };
        unsafe {
            CoTaskMemFree(folder.cast());
        }
        result
    }
    /// Stop all container processes and close their handles before removal.
    pub fn remove(mut self) -> Result<()> {
        self.remove_inner()
    }
    fn remove_inner(&mut self) -> Result<()> {
        if self.exists {
            if unsafe { DeleteAppContainerProfile(self.name.as_ptr()) } < 0 {
                return Err(HostError::new("EXTENSION_CONTAINER_CLEANUP_FAILED"));
            }
            self.exists = false;
        }
        Ok(())
    }
}
impl Drop for Profile {
    fn drop(&mut self) {
        // Explicit remove reports failures; drop is a final best-effort retry.
        let _ = self.remove_inner();
        if !self.sid.is_null() {
            unsafe {
                FreeSid(self.sid);
            }
            self.sid = std::ptr::null_mut();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        mem::size_of,
        os::windows::{
            ffi::OsStrExt,
            io::{AsHandle, AsRawHandle, FromRawHandle, OwnedHandle},
        },
    };
    use windows_sys::Win32::{
        Security::{
            EqualSid, GetTokenInformation, TokenIsAppContainer, SECURITY_CAPABILITIES, TOKEN_QUERY,
        },
        System::Threading::*,
    };

    #[test]
    fn distinct_profiles_and_collision_do_not_take_ownership_of_existing_data() {
        let one = Profile::create().unwrap();
        let two = Profile::create().unwrap();
        assert_eq!(unsafe { EqualSid(one.sid(), two.sid()) }, 0);
        let name = String::from_utf16(&one.name[..one.name.len() - 1]).unwrap();
        assert!(Profile::create_named(name.clone()).is_err());
        // Repeated collision must still fail: the failing owner did not delete it.
        assert!(Profile::create_named(name.clone()).is_err());
        one.remove().unwrap();
        Profile::create_named(name).unwrap().remove().unwrap();
        two.remove().unwrap();
    }

    struct Attributes {
        buffer: Vec<usize>,
    }
    impl Attributes {
        fn new() -> Self {
            let mut bytes = 0;
            unsafe {
                InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut bytes);
            }
            assert!(bytes > 0);
            let mut result = Self {
                buffer: vec![0; bytes.div_ceil(size_of::<usize>())],
            };
            assert_ne!(
                unsafe {
                    InitializeProcThreadAttributeList(
                        result.buffer.as_mut_ptr().cast(),
                        1,
                        0,
                        &mut bytes,
                    )
                },
                0
            );
            result
        }
    }
    impl Drop for Attributes {
        fn drop(&mut self) {
            unsafe {
                DeleteProcThreadAttributeList(self.buffer.as_mut_ptr().cast());
            }
        }
    }
    struct Process {
        process: OwnedHandle,
        _thread: OwnedHandle,
    }
    impl Drop for Process {
        fn drop(&mut self) {
            unsafe {
                TerminateProcess(self.process.as_raw_handle(), 1);
                WaitForSingleObject(self.process.as_raw_handle(), 5000);
            }
        }
    }
    #[test]
    fn real_suspended_process_has_appcontainer_token_before_job_resume() {
        let profile = Profile::create().unwrap();
        checked_process(&profile, None);
        profile.remove().unwrap();
    }

    // Only tests use cmd.exe, with fixed commands and controlled temporary paths.
    // A production extension launcher must use a verified entry, never a shell.
    fn checked_process(profile: &Profile, command: Option<&str>) -> Option<u32> {
        let mut attributes = Attributes::new();
        let caps = SECURITY_CAPABILITIES {
            AppContainerSid: profile.sid(),
            Capabilities: std::ptr::null_mut(),
            CapabilityCount: 0,
            Reserved: 0,
        };
        assert_ne!(
            unsafe {
                UpdateProcThreadAttribute(
                    attributes.buffer.as_mut_ptr().cast(),
                    0,
                    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
                    (&caps as *const SECURITY_CAPABILITIES).cast(),
                    size_of::<SECURITY_CAPABILITIES>(),
                    std::ptr::null_mut(),
                    std::ptr::null(),
                )
            },
            0
        );
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.buffer.as_mut_ptr().cast();
        let executable = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32/cmd.exe");
        let executable: Vec<u16> = executable
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        let folder = profile.folder().unwrap();
        let environment: Vec<u16> = format!(
            "LOCALAPPDATA={}\0SystemRoot={}\0TEMP={}\0TMP={}\0\0",
            folder.display(),
            std::env::var("SystemRoot").unwrap(),
            folder.join("Temp").display(),
            folder.join("Temp").display()
        )
        .encode_utf16()
        .collect();
        let mut command_line: Vec<u16> = command
            .map(|command| {
                format!("cmd.exe /d /c {command}")
                    .encode_utf16()
                    .chain(Some(0))
                    .collect()
            })
            .unwrap_or_default();
        let mut info = PROCESS_INFORMATION::default();
        assert_ne!(
            unsafe {
                CreateProcessW(
                    executable.as_ptr(),
                    if command_line.is_empty() {
                        std::ptr::null_mut()
                    } else {
                        command_line.as_mut_ptr()
                    },
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    CREATE_SUSPENDED
                        | CREATE_NO_WINDOW
                        | EXTENDED_STARTUPINFO_PRESENT
                        | CREATE_UNICODE_ENVIRONMENT,
                    environment.as_ptr().cast(),
                    std::ptr::null(),
                    &startup.StartupInfo,
                    &mut info,
                )
            },
            0,
            "AppContainer process creation failed: {}",
            std::io::Error::last_os_error()
        );
        let process = Process {
            process: unsafe { OwnedHandle::from_raw_handle(info.hProcess) },
            _thread: unsafe { OwnedHandle::from_raw_handle(info.hThread) },
        };
        let job = crate::extension_job::Job::new().unwrap();
        unsafe {
            job.assign_suspended(process.process.as_handle()).unwrap();
        }
        let mut token = std::ptr::null_mut();
        assert_ne!(
            unsafe { OpenProcessToken(process.process.as_raw_handle(), TOKEN_QUERY, &mut token) },
            0
        );
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        let mut contained = 0u32;
        let mut length = 0;
        assert_ne!(
            unsafe {
                GetTokenInformation(
                    token.as_raw_handle(),
                    TokenIsAppContainer,
                    (&mut contained as *mut u32).cast(),
                    size_of::<u32>() as u32,
                    &mut length,
                )
            },
            0
        );
        assert_eq!(contained, 1);
        use windows_sys::Win32::Security::{
            TokenAppContainerSid, TokenCapabilities, TOKEN_APPCONTAINER_INFORMATION,
        };
        let mut required = 0;
        unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                TokenAppContainerSid,
                std::ptr::null_mut(),
                0,
                &mut required,
            );
        }
        assert!(required > 0 && required < 4096);
        let mut data = vec![0usize; (required as usize).div_ceil(size_of::<usize>())];
        assert_ne!(
            unsafe {
                GetTokenInformation(
                    token.as_raw_handle(),
                    TokenAppContainerSid,
                    data.as_mut_ptr().cast(),
                    required,
                    &mut required,
                )
            },
            0
        );
        let identity = unsafe { &*data.as_ptr().cast::<TOKEN_APPCONTAINER_INFORMATION>() };
        assert_ne!(
            unsafe { EqualSid(identity.TokenAppContainer, profile.sid()) },
            0
        );
        let mut required = 0;
        unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                TokenCapabilities,
                std::ptr::null_mut(),
                0,
                &mut required,
            );
        }
        assert!((4..4096).contains(&required));
        let mut capabilities = vec![0usize; (required as usize).div_ceil(size_of::<usize>())];
        assert_ne!(
            unsafe {
                GetTokenInformation(
                    token.as_raw_handle(),
                    TokenCapabilities,
                    capabilities.as_mut_ptr().cast(),
                    required,
                    &mut required,
                )
            },
            0
        );
        assert_eq!(unsafe { *capabilities.as_ptr().cast::<u32>() }, 0);

        let exit = command.map(|_| {
            assert_ne!(
                unsafe { ResumeThread(process._thread.as_raw_handle()) },
                u32::MAX
            );
            assert_eq!(
                unsafe { WaitForSingleObject(process.process.as_raw_handle(), 10_000) },
                0
            );
            let mut exit = 0;
            assert_ne!(
                unsafe { GetExitCodeProcess(process.process.as_raw_handle(), &mut exit) },
                0
            );
            exit
        });
        job.terminate().unwrap();
        drop(token);
        drop(process);
        drop(job);
        drop(attributes);
        exit
    }

    #[test]
    fn real_container_can_read_only_explicitly_granted_package_objects() {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::*;
        let profile = Profile::create().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let payload = directory.path().join("payload.txt");
        let hidden = directory.path().join("ungranted.txt");
        std::fs::write(&payload, b"verified package content").unwrap();
        std::fs::write(&hidden, b"private sibling").unwrap();
        let open = |path: &std::path::Path| {
            std::fs::OpenOptions::new()
                .access_mode(READ_CONTROL | WRITE_DAC)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                .open(path)
                .unwrap()
        };
        let root_handle = open(directory.path());
        let file_handle = open(&payload);
        let read = format!("set /p value=<\"{}\"", payload.display());
        assert_ne!(checked_process(&profile, Some(&read)), Some(0));
        profile.grant_package_read_execute(&root_handle).unwrap();
        // The directory ACE does not propagate to existing children.
        assert_ne!(checked_process(&profile, Some(&read)), Some(0));
        profile.grant_package_read_execute(&file_handle).unwrap();
        assert_eq!(checked_process(&profile, Some(&read)), Some(0));
        let other = Profile::create().unwrap();
        assert_ne!(checked_process(&other, Some(&read)), Some(0));
        other.remove().unwrap();
        let created = directory.path().join("new.txt");
        assert_ne!(
            checked_process(
                &profile,
                Some(&format!("echo changed>\"{}\"", created.display()))
            ),
            Some(0)
        );
        assert!(!created.exists());
        assert_ne!(
            checked_process(
                &profile,
                Some(&format!("set /p value=<\"{}\"", hidden.display()))
            ),
            Some(0)
        );
        assert_ne!(
            checked_process(
                &profile,
                Some(&format!("echo changed>\"{}\"", payload.display()))
            ),
            Some(0)
        );
        assert_eq!(
            std::fs::read(&payload).unwrap(),
            b"verified package content"
        );
        drop(file_handle);
        drop(root_handle);
        profile.remove().unwrap();
    }
    #[test]
    fn package_grant_rejects_hardlinks_and_handles_without_acl_write_access() {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::*;
        let profile = Profile::create().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let payload = directory.path().join("payload.txt");
        let alias = directory.path().join("alias.txt");
        std::fs::write(&payload, b"unchanged").unwrap();
        let read_only = std::fs::File::open(&payload).unwrap();
        assert!(profile.grant_package_read_execute(&read_only).is_err());
        drop(read_only);
        std::fs::hard_link(&payload, &alias).unwrap();
        let handle = std::fs::OpenOptions::new()
            .access_mode(READ_CONTROL | WRITE_DAC)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&payload)
            .unwrap();
        assert_eq!(
            profile
                .grant_package_read_execute(&handle)
                .unwrap_err()
                .code,
            "EXTENSION_CONTAINER_ACL_OBJECT_INVALID"
        );
        assert_eq!(std::fs::read(&alias).unwrap(), b"unchanged");
        drop(handle);
        profile.remove().unwrap();
    }
}
