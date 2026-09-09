//! 每个实例独占其 AppContainer 配置，不接管任何已有配置。
use crate::workspace::{HostError, Result};
use windows_sys::Win32::Security::{
    FreeSid, IsValidSid,
    Isolation::{CreateAppContainerProfile, DeleteAppContainerProfile},
    PSID,
};

static PACKAGE_ACL_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

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
            // 特别是，ERROR_ALREADY_EXISTS 不得转让所有权。
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
    /// 为 SECURITY_CAPABILITIES 借用的 SID，仅在此所有者对象存续期间有效。
    pub fn sid(&self) -> PSID {
        self.sid
    }
    /// 授予当前实例读取和执行一个 Host 所有包对象的权限。调用方打开对象时不得跟随重解析点，
    /// 并须在整个启动期间持有已验证的包句柄。这里不使用递归继承，每个目录和文件都要分别检查、授权。
    /// 此操作只会添加一条 ACE，不会清理已有权限。
    pub fn grant_package_read_execute(&self, object: &std::fs::File) -> Result<()> {
        use windows_sys::Win32::Storage::FileSystem::{FILE_GENERIC_EXECUTE, FILE_GENERIC_READ};
        self.update_access(
            object,
            false,
            FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
            0,
            true,
        )
    }
    /// 授予当前实例修改其专用 scratch 目录及新建子对象的权限。
    pub fn grant_scratch_modify(&self, object: &std::fs::File) -> Result<()> {
        use windows_sys::Win32::{
            Security::SUB_CONTAINERS_AND_OBJECTS_INHERIT,
            Storage::FileSystem::{
                DELETE, FILE_GENERIC_EXECUTE, FILE_GENERIC_READ, FILE_GENERIC_WRITE,
            },
        };
        self.update_access(
            object,
            false,
            FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE | DELETE,
            SUB_CONTAINERS_AND_OBJECTS_INHERIT,
            false,
        )
    }
    /// 使用最初持有的对象句柄，仅移除这个新实例对应的允许 ACE；其他安全主体的 ACL 保持不变。
    pub fn revoke_package_access(&self, object: &std::fs::File) -> Result<()> {
        self.update_access(object, true, 0, 0, false)
    }
    fn update_access(
        &self,
        object: &std::fs::File,
        revoke: bool,
        permissions: u32,
        inheritance: u32,
        reject_hardlinks: bool,
    ) -> Result<()> {
        // 跨并发实例序列化 Host 读/合并/写操作。
        let _lock = PACKAGE_ACL_LOCK
            .lock()
            .map_err(|_| HostError::new("EXTENSION_CONTAINER_ACL_FAILED"))?;
        use std::os::windows::{fs::MetadataExt, io::AsRawHandle};
        use windows_sys::Win32::{
            Foundation::LocalFree,
            Security::{Authorization::*, DACL_SECURITY_INFORMATION},
            Storage::FileSystem::{
                GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
                FILE_ATTRIBUTE_REPARSE_POINT,
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
        if !revoke
            && (metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
                || !(metadata.is_file() || metadata.is_dir()))
        {
            return Err(HostError::new("EXTENSION_CONTAINER_ACL_OBJECT_INVALID"));
        }
        if metadata.is_file() && !revoke && reject_hardlinks {
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
        // 空 DACL 会向所有人授予完全访问权限，因此这里必须拒绝处理，不能误判为已妥善隔离的包对象。
        if status != 0 || old_acl.is_null() {
            return Err(HostError::new("EXTENSION_CONTAINER_ACL_FAILED"));
        }
        let entry = EXPLICIT_ACCESS_W {
            grfAccessPermissions: permissions,
            grfAccessMode: if revoke { REVOKE_ACCESS } else { GRANT_ACCESS },
            grfInheritance: inheritance,
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
    /// 在删除之前停止所有容器进程并关闭其句柄。
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
        // 显式删除报告失败； drop 是最后的尽力重试。
        let _ = self.remove_inner();
        if !self.sid.is_null() {
            unsafe {
                FreeSid(self.sid);
            }
            self.sid = std::ptr::null_mut();
        }
    }
}

#[cfg(all(test, feature = "desktop"))]
pub(crate) fn test_acl_entries(object: &std::fs::File) -> Vec<Vec<u8>> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::{Authorization::*, *},
    };
    struct Allocation(*mut core::ffi::c_void);
    impl Drop for Allocation {
        fn drop(&mut self) {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
    let mut acl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    assert_eq!(
        unsafe {
            GetSecurityInfo(
                object.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut acl,
                std::ptr::null_mut(),
                &mut descriptor,
            )
        },
        0
    );
    let _descriptor = Allocation(descriptor);
    assert!(!acl.is_null());
    let mut entries = Vec::new();
    for index in 0..unsafe { (*acl).AceCount } {
        let mut ace = std::ptr::null_mut();
        assert_ne!(unsafe { GetAce(acl, u32::from(index), &mut ace) }, 0);
        let length = unsafe { (*(ace as *const ACE_HEADER)).AceSize };
        entries.push(
            unsafe { std::slice::from_raw_parts(ace.cast::<u8>(), usize::from(length)) }.to_vec(),
        );
    }
    entries
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
        // 重复发生名称冲突时仍须失败：创建失败的一方并不拥有该配置，也无权删除它。
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

    // 只有测试会通过 cmd.exe 运行固定命令和受控临时路径；生产扩展启动器必须使用已验证入口，不能调用 shell。
    fn checked_process(profile: &Profile, command: Option<&str>) -> Option<u32> {
        let executable = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32/cmd.exe");
        checked_executable(
            profile,
            &executable,
            command.map(|c| format!("cmd.exe /d /c {c}")),
        )
    }

    fn checked_executable(
        profile: &Profile,
        executable: &std::path::Path,
        command: Option<String>,
    ) -> Option<u32> {
        checked_executable_data(profile, executable, command, None)
    }

    fn checked_executable_data(
        profile: &Profile,
        executable: &std::path::Path,
        command: Option<String>,
        mut data: Option<crate::extension_launch_data::LaunchData>,
    ) -> Option<u32> {
        if let Some(data) = data.take() {
            let suspended =
                crate::extension_process::Suspended::create(profile, executable, data).unwrap();
            // 仅用于受控测试夹具；此处没有授权任何用户扩展。
            let running = unsafe { suspended.resume().unwrap() };
            let result = running.wait(std::time::Duration::from_secs(10)).unwrap();
            assert!(result.is_some());
            running.terminate().unwrap();
            return result;
        }
        let resume = command.is_some() || data.is_some();
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
            .as_ref()
            .map(|command| command.encode_utf16().chain(Some(0)).collect())
            .unwrap_or_default();
        let environment_ptr = data
            .as_ref()
            .map_or(environment.as_ptr(), |d| d.environment().as_ptr());
        let command_ptr = data.as_mut().map_or_else(
            || {
                if command_line.is_empty() {
                    std::ptr::null_mut()
                } else {
                    command_line.as_mut_ptr()
                }
            },
            |d| d.command_mut().as_mut_ptr(),
        );
        let mut info = PROCESS_INFORMATION::default();
        assert_ne!(
            unsafe {
                CreateProcessW(
                    executable.as_ptr(),
                    command_ptr,
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    CREATE_SUSPENDED
                        | CREATE_NO_WINDOW
                        | EXTENDED_STARTUPINFO_PRESENT
                        | CREATE_UNICODE_ENVIRONMENT,
                    environment_ptr.cast(),
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

        let exit = resume.then(|| {
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
        // 目录 ACE 不会传播到现有子级。
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
    #[test]
    fn real_container_cannot_reach_ipv4_or_ipv6_loopback_listeners() {
        real_native_protocol_probes(false, false);
    }
    #[cfg(feature = "desktop")]
    #[test]
    #[ignore = "real MCP tools/call 60-second deadline acceptance; run explicitly"]
    fn real_mcp_tool_deadline_terminates_hung_server_and_host_can_save() {
        real_native_protocol_probes(true, false);
    }
    #[test]
    #[ignore = "real AppContainer MCP CPU/memory/process exhaustion; run explicitly"]
    fn real_mcp_resource_pressure_reports_error_and_reaps_tree() {
        real_native_protocol_probes(false, true);
    }
    fn real_native_protocol_probes(_mcp_deadline: bool, resources: bool) {
        use std::{
            net::{TcpListener, UdpSocket},
            os::windows::fs::OpenOptionsExt,
        };
        use windows_sys::Win32::Storage::FileSystem::*;
        let profile = Profile::create().unwrap();
        let package = tempfile::tempdir().unwrap();
        let executable = package.path().join("network-probe.exe");
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/sandbox_network_probe.rs");
        let compile = std::process::Command::new("rustc")
            .arg("--edition=2021")
            .arg(&fixture)
            .arg("-o")
            .arg(&executable)
            .output()
            .unwrap();
        assert!(
            compile.status.success(),
            "{}",
            String::from_utf8_lossy(&compile.stderr)
        );
        let open = |path: &std::path::Path| {
            std::fs::OpenOptions::new()
                .access_mode(READ_CONTROL | WRITE_DAC)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                .open(path)
                .unwrap()
        };
        let root = open(package.path());
        let entry = open(&executable);
        #[cfg(feature = "desktop")]
        let _pinned = {
            use sha2::{Digest, Sha256};
            let bytes = std::fs::read(&executable).unwrap();
            let inventory = crate::extension_package::Inventory {
                files: [(
                    "network-probe.exe".to_owned(),
                    format!("{:x}", Sha256::digest(&bytes)),
                )]
                .into_iter()
                .collect(),
                expanded_size: bytes.len() as u64,
                manifest: "network-probe.exe".to_owned(),
            };
            let dir =
                cap_std::fs::Dir::open_ambient_dir(package.path(), cap_std::ambient_authority())
                    .unwrap();
            let hash = crate::extension_unpack::verify_tree(&dir, &inventory).unwrap();
            let pinned =
                crate::extension_pinned::PinnedPackage::open(&dir, &inventory, &hash).unwrap();
            pinned.grant_read_execute(&profile).unwrap();
            pinned
        };
        #[cfg(feature = "desktop")]
        let bound_entry = _pinned.bind_entry("network-probe.exe").unwrap();
        #[cfg(feature = "desktop")]
        let executable = bound_entry.path().to_owned();
        #[cfg(not(feature = "desktop"))]
        {
            profile.grant_package_read_execute(&root).unwrap();
            profile.grant_package_read_execute(&entry).unwrap();
        }
        // 测试真实构建器，而不是在测试侧另写引号解码器；子进程会比较 argv 和完整环境，但不会记录具体值。
        let args = [
            "launch",
            "",
            "space value",
            "引号🦀",
            "trailing\\",
            "a\"b",
            "slash\\\"quote",
            "&|%PATH%",
            "line\nbreak",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
        let folder = profile.folder().unwrap();
        let system = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap());
        #[cfg(not(feature = "desktop"))]
        let data = crate::extension_launch_data::LaunchData::new(
            &executable,
            &args,
            &system,
            &folder,
            &folder.join("Temp"),
            &[("CUSTOM".to_owned(), "declared=值".to_owned())]
                .into_iter()
                .collect(),
        )
        .unwrap();
        #[cfg(feature = "desktop")]
        {
            use crate::extension_launch_authorization::{credential_id, Context};
            use crate::extension_permit::{Authority, Claims, Environment, ExecutionKind};
            use zeroize::Zeroizing;
            let credential_directory = tempfile::tempdir().unwrap();
            let mut broker = crate::credentials::CredentialBroker::new(
                credential_directory.path().join("credentials.v1"),
            );
            broker
                .unlock(Zeroizing::new(b"native fixture passphrase".to_vec()))
                .unwrap();
            let claims = Claims {
                kind: ExecutionKind::Mcp,
                source: "https://catalog.example/".into(),
                namespace: "examples".into(),
                package_id: "native-probe".into(),
                version: "1.0.0".into(),
                archive_sha256: "a".repeat(64),
                tree_sha256: bound_entry.tree_sha256().into(),
                signer_sha256: "b".repeat(64),
                entry: bound_entry.relative_name().into(),
                arguments: args,
                environment: [(
                    "CUSTOM".into(),
                    Environment::CredentialScope("custom".into()),
                )]
                .into_iter()
                .collect(),
                permissions: Default::default(),
                vault_id: uuid::Uuid::new_v4().to_string(),
                platform: "windows".into(),
                policy_version: "1".into(),
                // 此探测器检查 argv 与环境，不验证过期行为。许可有效期须覆盖并行负载下的有界进程观测；
                // 下方专用的过期探测仍使用两秒租期。
                expires_at_ms: 120_000,
            };
            broker
                .put(
                    &credential_id(&claims, "custom").unwrap(),
                    Zeroizing::new("declared=值".as_bytes().to_vec()),
                )
                .unwrap();
            let authority = Authority::default();
            let permit = authority.issue(&claims, 1).unwrap();
            let context = Context {
                vault_id: &claims.vault_id,
                policy_version: "1",
                system_root: &system,
                container_data: &folder,
                scratch: &folder.join("Temp"),
            };
            let launch_started = std::time::Instant::now();
            let prepared = context
                .prepare(&authority, &permit, &claims, &bound_entry, &broker, 2)
                .unwrap();
            let suspended = prepared.create_suspended(&profile, &bound_entry).unwrap();
            let running = unsafe { suspended.resume().unwrap() };
            let exit = running.wait(std::time::Duration::from_secs(5)).unwrap();
            assert_eq!(
                exit,
                Some(0),
                "launch probe elapsed={:?} authorization={:?} active_processes={:?}",
                launch_started.elapsed(),
                running.check_authorization().err().map(|error| error.code),
                running.active_test_processes().ok(),
            );
            drop(running);
            // 实际本机 RPC：子进程无法命名身份或连接到共享端点；只有它自己的 stdio 管道才能到达该代理。
            {
                use std::os::windows::io::AsRawHandle;
                use windows_sys::Win32::Storage::FileSystem::{
                    FileIdInfo, GetFileInformationByHandleEx, FILE_ID_INFO,
                };
                let sentinel_dir = tempfile::tempdir().unwrap();
                let sentinel = std::fs::OpenOptions::new()
                    .read(true)
                    .write(true)
                    .create_new(true)
                    .open(sentinel_dir.path().join("host-only-sentinel"))
                    .unwrap();
                let mut identity: FILE_ID_INFO = unsafe { std::mem::zeroed() };
                assert_ne!(
                    unsafe {
                        GetFileInformationByHandleEx(
                            sentinel.as_raw_handle(),
                            FileIdInfo,
                            (&mut identity as *mut FILE_ID_INFO).cast(),
                            std::mem::size_of::<FILE_ID_INFO>() as u32,
                        )
                    },
                    0
                );
                let sentinel_identity = format!(
                    "{}:{}",
                    identity.VolumeSerialNumber,
                    identity
                        .FileId
                        .Identifier
                        .iter()
                        .map(|byte| format!("{byte:02x}"))
                        .collect::<String>()
                );
                assert_ne!(
                    unsafe {
                        windows_sys::Win32::Foundation::SetHandleInformation(
                            sentinel.as_raw_handle(),
                            windows_sys::Win32::Foundation::HANDLE_FLAG_INHERIT,
                            windows_sys::Win32::Foundation::HANDLE_FLAG_INHERIT,
                        )
                    },
                    0
                );
                let vault = tempfile::tempdir().unwrap();
                let mut workspace = crate::workspace::Workspace::open(vault.path()).unwrap();
                workspace
                    .write("fixture.md", "", b"from host broker", "local")
                    .unwrap();
                let mut rpc = claims.clone();
                rpc.vault_id = workspace.vault_id.clone();
                rpc.arguments = vec![
                    "file_rpc".into(),
                    (sentinel.as_raw_handle() as usize).to_string(),
                    sentinel_identity,
                ];
                rpc.permissions.insert("notes.read".into());
                rpc.expires_at_ms = 10_000;
                let permit = authority.issue(&rpc, 1).unwrap();
                let rpc_context = Context {
                    vault_id: &rpc.vault_id,
                    ..context
                };
                let prepared = rpc_context
                    .prepare(&authority, &permit, &rpc, &bound_entry, &broker, 2)
                    .unwrap();
                let mut files = crate::extension_file_broker::Broker::bind(
                    &authority, &permit, &rpc, &broker, &workspace, "1", 2,
                )
                .unwrap();
                let (suspended, io) = prepared
                    .create_suspended_with_stdio(&profile, &bound_entry)
                    .unwrap();
                let running = unsafe { suspended.resume().unwrap() };
                let mut pump = running.start_io(io).unwrap();
                let crate::extension_io::Event::Frame(request) =
                    pump.receive(std::time::Duration::from_secs(5)).unwrap()
                else {
                    panic!(
                        "missing RPC request; child exit: {:?}",
                        running.wait(std::time::Duration::from_secs(1))
                    )
                };
                let response = files.dispatch(&mut workspace, &request).unwrap();
                pump.send(serde_json::to_vec(&response).unwrap()).unwrap();
                pump.close_input();
                let crate::extension_io::Event::Frame(reply) =
                    pump.receive(std::time::Duration::from_secs(5)).unwrap()
                else {
                    panic!("missing RPC reply")
                };
                assert_eq!(reply, b"{\"ok\":true}");
                assert!(matches!(
                    pump.receive(std::time::Duration::from_secs(5)).unwrap(),
                    crate::extension_io::Event::Closed
                ));
                assert_eq!(
                    running.wait(std::time::Duration::from_secs(5)).unwrap(),
                    Some(0)
                );
                pump.shutdown().unwrap();
                assert_eq!(
                    workspace.read("fixture.md").unwrap().content,
                    "from host broker"
                );
            }
            let mut mcp_modes = vec![
                "mcp",
                "mcp_wrong_id",
                "mcp_cancel",
                "mcp_remote_error",
                "mcp_bad_version",
                "mcp_pages",
                "mcp_bad_result",
                "mcp_idle_change",
                "mcp_review_lock",
            ];
            if _mcp_deadline {
                mcp_modes.push("mcp_deadline");
            }
            if resources {
                mcp_modes.extend(["mcp_cpu", "mcp_memory", "mcp_processes", "mcp_scratch"]);
            }
            for mode in mcp_modes {
                use std::sync::{
                    atomic::{AtomicBool, Ordering},
                    Arc,
                };
                let mut mcp = claims.clone();
                mcp.arguments = vec![mode.into()];
                mcp.expires_at_ms = 120_000;
                let permit = authority.issue(&mcp, 1).unwrap();
                let prepared = context
                    .prepare(&authority, &permit, &mcp, &bound_entry, &broker, 2)
                    .unwrap();
                let (suspended, io) = prepared
                    .create_suspended_with_stdio(&profile, &bound_entry)
                    .unwrap();
                let running = unsafe { suspended.resume().unwrap() };
                let mut session = crate::extension_mcp::Session::new(&running, io).unwrap();
                let cancel = Arc::new(AtomicBool::new(false));
                assert_eq!(
                    session
                        .test_call_tool("echo", serde_json::json!({}), &cancel)
                        .unwrap_err()
                        .code,
                    "EXTENSION_MCP_NOT_INITIALIZED"
                );
                if mode == "mcp_bad_version" {
                    assert_eq!(
                        session.initialize(&cancel).unwrap_err().code,
                        "EXTENSION_MCP_INITIALIZATION_INVALID"
                    );
                } else {
                    assert_eq!(
                        session.initialize(&cancel).unwrap(),
                        crate::extension_mcp::PROTOCOL_VERSION
                    );
                    assert_eq!(
                        session
                            .test_call_tool("echo", serde_json::json!({}), &cancel)
                            .unwrap_err()
                            .code,
                        "EXTENSION_MCP_CATALOG_REQUIRED"
                    );
                    assert_eq!(session.refresh_tools(&cancel).unwrap()[0].name, "echo");
                    assert_eq!(
                        session
                            .test_call_tool("missing", serde_json::json!({}), &cancel)
                            .unwrap_err()
                            .code,
                        "EXTENSION_MCP_TOOL_NOT_FOUND"
                    );
                    assert_eq!(
                        session
                            .test_call_tool("echo", serde_json::json!({"unexpected":1}), &cancel)
                            .unwrap_err()
                            .code,
                        "EXTENSION_MCP_ARGUMENTS_INVALID"
                    );
                    let cancellation = if mode == "mcp_cancel" {
                        let flag = Arc::clone(&cancel);
                        Some(std::thread::spawn(move || {
                            std::thread::sleep(std::time::Duration::from_millis(100));
                            flag.store(true, Ordering::Release);
                        }))
                    } else {
                        None
                    };
                    let mut earlier = None;
                    if mode == "mcp" {
                        let review = session.review_call("echo", serde_json::json!({})).unwrap();
                        let identity = serde_json::to_value(&review.identity).unwrap();
                        assert_eq!(identity["package_id"], mcp.package_id);
                        assert_eq!(identity["vault_id"], mcp.vault_id);
                        assert_eq!(identity["source"], mcp.source);
                        assert_eq!(identity["execution_digest"].as_str().unwrap().len(), 64);
                        earlier = Some(session.confirm_call(&review.review_id).unwrap());
                    }
                    let tool_started = std::time::Instant::now();
                    let result = if mode == "mcp" {
                        let mut review =
                            session.review_call("echo", serde_json::json!({})).unwrap();
                        review.arguments = serde_json::json!({"unexpected":1});
                        let approved = session.confirm_call(&review.review_id).unwrap();
                        session.call_tool(approved, &cancel)
                    } else if mode == "mcp_review_lock" {
                        let review = session.review_call("echo", serde_json::json!({})).unwrap();
                        let approved = session.confirm_call(&review.review_id).unwrap();
                        broker.lock();
                        session.call_tool(approved, &cancel)
                    } else {
                        session.test_call_tool("echo", serde_json::json!({}), &cancel)
                    };
                    match mode {
                        "mcp_review_lock" => {
                            assert_eq!(result.unwrap_err().code, "CREDENTIALS_LOCKED");
                            broker
                                .unlock(Zeroizing::new(b"native fixture passphrase".to_vec()))
                                .unwrap();
                            assert_eq!(
                                session.refresh_tools(&cancel).err().unwrap().code,
                                "EXTENSION_MCP_SESSION_FAILED"
                            );
                        }
                        "mcp_bad_result" => assert_eq!(
                            result.unwrap_err().code,
                            "EXTENSION_MCP_TOOL_RESULT_INVALID"
                        ),
                        "mcp_idle_change" => {
                            assert_eq!(result.unwrap()["structuredContent"]["ok"], true);
                            std::thread::sleep(std::time::Duration::from_millis(200));
                            assert_eq!(
                                session
                                    .test_call_tool("echo", serde_json::json!({}), &cancel)
                                    .unwrap_err()
                                    .code,
                                "EXTENSION_MCP_CATALOG_REQUIRED"
                            );
                            assert!(session.take_tools_changed());
                        }
                        "mcp_cpu" | "mcp_memory" | "mcp_processes" | "mcp_scratch" => {
                            let expected = match mode {
                                "mcp_memory" => "EXTENSION_RESOURCE_MEMORY_EXCEEDED",
                                "mcp_processes" => "EXTENSION_RESOURCE_PROCESSES_EXCEEDED",
                                "mcp_scratch" => "EXTENSION_RESOURCE_SCRATCH_EXCEEDED",
                                _ => "EXTENSION_RESOURCE_CPU_EXCEEDED",
                            };
                            assert_eq!(result.unwrap_err().code, expected);
                            let elapsed = tool_started.elapsed();
                            assert!(
                                elapsed
                                    < std::time::Duration::from_secs(if mode == "mcp_cpu" {
                                        20
                                    } else {
                                        10
                                    })
                            );
                            eprintln!("AppContainer {mode} resource error: {elapsed:?}");
                            assert_eq!(
                                session.refresh_tools(&cancel).err().unwrap().code,
                                "EXTENSION_MCP_SESSION_FAILED"
                            );
                            assert_eq!(running.check_authorization().unwrap_err().code, expected);
                            let vault = tempfile::tempdir().unwrap();
                            let mut workspace =
                                crate::workspace::Workspace::open(vault.path()).unwrap();
                            workspace
                                .write(
                                    "resource-recovery.md",
                                    "",
                                    b"Host saved after resource termination",
                                    "local",
                                )
                                .unwrap();
                            drop(workspace);
                            assert_eq!(
                                crate::workspace::Workspace::open(vault.path())
                                    .unwrap()
                                    .read("resource-recovery.md")
                                    .unwrap()
                                    .content,
                                "Host saved after resource termination"
                            );
                        }
                        "mcp_deadline" => {
                            assert_eq!(
                                result.unwrap_err().code,
                                "EXTENSION_TOOL_DEADLINE_EXCEEDED"
                            );
                            let elapsed = tool_started.elapsed();
                            assert!(
                                elapsed >= std::time::Duration::from_secs(59)
                                    && elapsed < std::time::Duration::from_secs(65)
                            );
                            eprintln!("real MCP tools/call deadline: {elapsed:?}");
                            let vault = tempfile::tempdir().unwrap();
                            let mut workspace =
                                crate::workspace::Workspace::open(vault.path()).unwrap();
                            workspace
                                .write(
                                    "after-timeout.md",
                                    "",
                                    b"Host save after actual MCP deadline",
                                    "local",
                                )
                                .unwrap();
                            drop(workspace);
                            let mut workspace =
                                crate::workspace::Workspace::open(vault.path()).unwrap();
                            assert_eq!(
                                workspace.read("after-timeout.md").unwrap().content,
                                "Host save after actual MCP deadline"
                            );
                        }
                        "mcp_wrong_id" => assert_eq!(
                            result.unwrap_err().code,
                            "EXTENSION_MCP_RESPONSE_ID_MISMATCH"
                        ),
                        "mcp_cancel" => {
                            assert_eq!(result.unwrap_err().code, "EXTENSION_MCP_CANCELLED");
                            cancellation.unwrap().join().unwrap();
                        }
                        "mcp_remote_error" => {
                            assert_eq!(result.unwrap_err().code, "EXTENSION_MCP_REMOTE_ERROR");
                            assert_eq!(
                                session
                                    .test_call_tool("echo", serde_json::json!({}), &cancel)
                                    .unwrap()["content"][0]["text"],
                                "native MCP success"
                            );
                        }
                        _ => {
                            assert_eq!(result.unwrap()["content"][0]["text"], "native MCP success");
                            assert!(session.take_tools_changed());
                            assert!(!session.take_tools_changed());
                            if let Some(approved) = earlier {
                                assert_eq!(
                                    session.call_tool(approved, &cancel).unwrap_err().code,
                                    "EXTENSION_CALL_CATALOG_CHANGED"
                                );
                            }
                            assert_eq!(
                                session
                                    .test_call_tool("echo", serde_json::json!({}), &cancel)
                                    .unwrap_err()
                                    .code,
                                "EXTENSION_MCP_CATALOG_REQUIRED"
                            );
                        }
                    }
                    if mode == "mcp_cancel" || mode == "mcp_wrong_id" {
                        assert_eq!(
                            session.refresh_tools(&cancel).err().unwrap().code,
                            "EXTENSION_MCP_SESSION_FAILED"
                        );
                    }
                }
                session.shutdown().unwrap();
                assert!(running
                    .wait(std::time::Duration::from_secs(5))
                    .unwrap()
                    .is_some());
                let started = std::time::Instant::now();
                while running.active_test_processes().unwrap() != 0 {
                    assert!(started.elapsed() < std::time::Duration::from_secs(5));
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
            }
            for mode in ["wait_tree", "stderr_flood", "stdout_flood"] {
                let mut io_claims = claims.clone();
                io_claims.arguments = vec![mode.into()];
                io_claims.expires_at_ms = 120_000;
                let permit = authority.issue(&io_claims, 1).unwrap();
                let prepared = context
                    .prepare(&authority, &permit, &io_claims, &bound_entry, &broker, 2)
                    .unwrap();
                let (suspended, io) = prepared
                    .create_suspended_with_stdio(&profile, &bound_entry)
                    .unwrap();
                let running = unsafe { suspended.resume().unwrap() };
                let pump = running.start_io(io).unwrap();
                if mode == "wait_tree" {
                    pump.send(vec![b'x'; crate::extension_stdio::MAX_FRAME_BYTES])
                        .unwrap();
                    let started = std::time::Instant::now();
                    while running.active_test_processes().unwrap() < 2 {
                        assert!(started.elapsed() < std::time::Duration::from_secs(5));
                        std::thread::sleep(std::time::Duration::from_millis(5));
                    }
                } else {
                    let started = std::time::Instant::now();
                    while pump.check().is_ok() {
                        assert!(started.elapsed() < std::time::Duration::from_secs(5));
                        std::thread::sleep(std::time::Duration::from_millis(5));
                    }
                    assert_eq!(
                        pump.check().unwrap_err().code,
                        if mode == "stderr_flood" {
                            "EXTENSION_STDERR_LIMIT_EXCEEDED"
                        } else {
                            "EXTENSION_BROKER_REQUEST_TOO_LARGE"
                        }
                    );
                }
                let started = std::time::Instant::now();
                pump.shutdown().unwrap();
                assert!(running
                    .wait(std::time::Duration::from_secs(5))
                    .unwrap()
                    .is_some());
                while running.active_test_processes().unwrap() != 0 {
                    assert!(started.elapsed() < std::time::Duration::from_secs(5));
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
                eprintln!(
                    "native IO {mode}: shutdown/tree empty in {:?}",
                    started.elapsed()
                );
            }
            for cause in [
                "before_create",
                "before_resume",
                "permit",
                "credential",
                "credential_without_secret",
                "owner_drop",
                "expiry",
            ] {
                let mut issuer = Authority::default();
                let mut waiting = claims.clone();
                waiting.arguments = vec!["wait_tree".into()];
                if cause == "credential_without_secret" {
                    waiting.environment.clear();
                }
                waiting.expires_at_ms = if cause == "expiry" { 2_002 } else { 120_000 };
                let permit = issuer.issue(&waiting, 1).unwrap();
                let prepared = context
                    .prepare(&issuer, &permit, &waiting, &bound_entry, &broker, 2)
                    .unwrap();
                if cause == "before_create" {
                    issuer.invalidate_all();
                    assert_eq!(
                        prepared
                            .create_suspended(&profile, &bound_entry)
                            .err()
                            .unwrap()
                            .code,
                        "EXTENSION_PERMIT_REVOKED"
                    );
                    continue;
                }
                let suspended = prepared.create_suspended(&profile, &bound_entry).unwrap();
                if cause == "before_resume" {
                    issuer.invalidate_all();
                    assert_eq!(
                        unsafe { suspended.resume() }.err().unwrap().code,
                        "EXTENSION_PERMIT_REVOKED"
                    );
                    continue;
                }
                let running = unsafe { suspended.resume().unwrap() };
                let started = std::time::Instant::now();
                while running.active_test_processes().unwrap() != 2
                    && started.elapsed() < std::time::Duration::from_secs(5)
                {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                assert_eq!(running.active_test_processes().unwrap(), 2);
                let revoked = std::time::Instant::now();
                match cause {
                    "permit" => issuer.invalidate_all(),
                    "credential" | "credential_without_secret" => broker.lock(),
                    "expiry" => {}
                    _ => drop(issuer),
                }
                // 监视器必须在没有 check_authorization 或工具轮询的情况下运行。
                assert!(running
                    .wait(std::time::Duration::from_secs(5))
                    .unwrap()
                    .is_some());
                while running.active_test_processes().unwrap() != 0
                    && revoked.elapsed() < std::time::Duration::from_secs(5)
                {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                assert_eq!(running.active_test_processes().unwrap(), 0);
                assert!(revoked.elapsed() < std::time::Duration::from_secs(5));
                let expected = match cause {
                    "credential" | "credential_without_secret" => "CREDENTIALS_LOCKED",
                    "expiry" => "EXTENSION_PERMIT_EXPIRED",
                    _ => "EXTENSION_PERMIT_REVOKED",
                };
                assert_eq!(running.check_authorization().unwrap_err().code, expected);
                assert_eq!(running.start_tool_call().err().unwrap().code, expected);
                eprintln!(
                    "instance revocation {cause}: tree empty after {:?}",
                    revoked.elapsed()
                );
                drop(running);
                if matches!(cause, "credential" | "credential_without_secret") {
                    broker
                        .unlock(Zeroizing::new(b"native fixture passphrase".to_vec()))
                        .unwrap();
                }
            }
        }
        #[cfg(not(feature = "desktop"))]
        assert_eq!(
            checked_executable_data(&profile, &executable, None, Some(data)),
            Some(0)
        );
        let data = crate::extension_launch_data::LaunchData::new(
            &executable,
            &["wait".to_owned()],
            &system,
            &folder,
            &folder.join("Temp"),
            &std::collections::BTreeMap::new(),
        )
        .unwrap();
        let suspended =
            crate::extension_process::Suspended::create(&profile, &executable, data).unwrap();
        let running = unsafe { suspended.resume().unwrap() };
        assert_eq!(running.wait(std::time::Duration::ZERO).unwrap(), None);
        assert_eq!(
            running
                .wait(std::time::Duration::from_millis(60001))
                .unwrap_err()
                .code,
            "EXTENSION_PROCESS_WAIT_INVALID"
        );
        let completed = running.start_tool_call().unwrap();
        completed.finish().unwrap();
        assert_eq!(running.wait(std::time::Duration::ZERO).unwrap(), None);
        let deadline = running
            .start_test_tool_call(std::time::Duration::from_millis(100))
            .unwrap();
        // 过期处理不依赖 check() 或 finish() 驱动；这里只等待 OS 进程退出。
        assert!(running
            .wait(std::time::Duration::from_secs(5))
            .unwrap()
            .is_some());
        assert_eq!(
            deadline.finish().unwrap_err().code,
            "EXTENSION_TOOL_DEADLINE_EXCEEDED"
        );
        assert!(running
            .wait(std::time::Duration::from_secs(5))
            .unwrap()
            .is_some());
        drop(running);
        for explicit_cancel in [false, true] {
            let data = crate::extension_launch_data::LaunchData::new(
                &executable,
                &["wait".to_owned()],
                &system,
                &folder,
                &folder.join("Temp"),
                &std::collections::BTreeMap::new(),
            )
            .unwrap();
            let suspended =
                crate::extension_process::Suspended::create(&profile, &executable, data).unwrap();
            let running = unsafe { suspended.resume().unwrap() };
            let deadline = running.start_tool_call().unwrap();
            assert_eq!(running.wait(std::time::Duration::ZERO).unwrap(), None);
            if explicit_cancel {
                deadline.cancel().unwrap();
            } else {
                drop(deadline);
            }
            assert!(running
                .wait(std::time::Duration::from_secs(5))
                .unwrap()
                .is_some());
        }
        for ip in ["127.0.0.1:0", "[::1]:0"] {
            let tcp = TcpListener::bind(ip).unwrap();
            let udp = UdpSocket::bind(ip).unwrap();
            for (mode, address) in [
                ("tcp", tcp.local_addr().unwrap()),
                ("udp", udp.local_addr().unwrap()),
            ] {
                let address = address.to_string();
                // 验证同一个可执行文件与目标在容器隔离之外能够正常工作。
                assert!(std::process::Command::new(&executable)
                    .args([mode, &address])
                    .status()
                    .unwrap()
                    .success());
                if mode == "tcp" {
                    tcp.set_nonblocking(true).unwrap();
                    tcp.accept().unwrap();
                } else {
                    udp.set_read_timeout(Some(std::time::Duration::from_secs(2)))
                        .unwrap();
                    let mut bytes = [0u8; 8];
                    assert_eq!(udp.recv(&mut bytes).unwrap(), 5);
                    assert_eq!(&bytes[..5], b"probe");
                    udp.set_nonblocking(true).unwrap();
                }
                let data = crate::extension_launch_data::LaunchData::new(
                    &executable,
                    &[mode.to_owned(), address.clone()],
                    &system,
                    &folder,
                    &folder.join("Temp"),
                    &std::collections::BTreeMap::new(),
                )
                .unwrap();
                // 环回隔离可以静默丢弃数据包。 TCP必须明确报告拒绝或超时； UDP 发送可能会成功，但没有数据报可能到达下面的受控侦听器。
                let exit = checked_executable_data(&profile, &executable, None, Some(data));
                eprintln!("container network probe {mode} {address}: {exit:?}");
                if mode == "tcp" {
                    assert!(matches!(exit, Some(77 | 80)), "{mode} {address}: {exit:?}");
                } else {
                    assert!(matches!(exit, Some(0 | 77)), "{mode} {address}: {exit:?}");
                    std::thread::sleep(std::time::Duration::from_millis(200));
                }
                if mode == "tcp" {
                    assert_eq!(
                        tcp.accept().unwrap_err().kind(),
                        std::io::ErrorKind::WouldBlock
                    );
                } else {
                    assert_eq!(
                        udp.recv(&mut [0u8; 8]).unwrap_err().kind(),
                        std::io::ErrorKind::WouldBlock
                    );
                }
                assert!(std::process::Command::new(&executable)
                    .args([mode, &address])
                    .status()
                    .unwrap()
                    .success());
                if mode == "tcp" {
                    tcp.accept().unwrap();
                } else {
                    udp.set_nonblocking(false).unwrap();
                    assert_eq!(udp.recv(&mut [0u8; 8]).unwrap(), 5);
                }
            }
        }
        drop(entry);
        drop(root);
        profile.remove().unwrap();
    }
    #[test]
    #[ignore = "production 60-second wall-clock and process-tree acceptance; run explicitly"]
    fn real_sixty_second_tool_deadline_kills_tree_and_host_can_save() {
        use std::{
            os::windows::fs::OpenOptionsExt,
            time::{Duration, Instant},
        };
        use windows_sys::Win32::Storage::FileSystem::*;
        let profile = Profile::create().unwrap();
        let package = tempfile::tempdir().unwrap();
        let executable = package.path().join("network-probe.exe");
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/sandbox_network_probe.rs");
        let compile = std::process::Command::new("rustc")
            .arg("--edition=2021")
            .arg(&fixture)
            .arg("-o")
            .arg(&executable)
            .output()
            .unwrap();
        assert!(
            compile.status.success(),
            "{}",
            String::from_utf8_lossy(&compile.stderr)
        );
        let open = |path: &std::path::Path| {
            std::fs::OpenOptions::new()
                .access_mode(READ_CONTROL | WRITE_DAC)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                .open(path)
                .unwrap()
        };
        let root = open(package.path());
        let entry = open(&executable);
        #[cfg(feature = "desktop")]
        let _pinned = {
            use sha2::{Digest, Sha256};
            let bytes = std::fs::read(&executable).unwrap();
            let inventory = crate::extension_package::Inventory {
                files: [(
                    "network-probe.exe".to_owned(),
                    format!("{:x}", Sha256::digest(&bytes)),
                )]
                .into_iter()
                .collect(),
                expanded_size: bytes.len() as u64,
                manifest: "network-probe.exe".to_owned(),
            };
            let dir =
                cap_std::fs::Dir::open_ambient_dir(package.path(), cap_std::ambient_authority())
                    .unwrap();
            let hash = crate::extension_unpack::verify_tree(&dir, &inventory).unwrap();
            let pinned =
                crate::extension_pinned::PinnedPackage::open(&dir, &inventory, &hash).unwrap();
            pinned.grant_read_execute(&profile).unwrap();
            pinned
        };
        #[cfg(feature = "desktop")]
        let bound_entry = _pinned.bind_entry("network-probe.exe").unwrap();
        #[cfg(feature = "desktop")]
        let executable = bound_entry.path().to_owned();
        #[cfg(not(feature = "desktop"))]
        {
            profile.grant_package_read_execute(&root).unwrap();
            profile.grant_package_read_execute(&entry).unwrap();
        }

        let folder = profile.folder().unwrap();
        let system = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap());
        let data = crate::extension_launch_data::LaunchData::new(
            &executable,
            &["wait_tree".to_owned()],
            &system,
            &folder,
            &folder.join("Temp"),
            &std::collections::BTreeMap::new(),
        )
        .unwrap();
        let suspended =
            crate::extension_process::Suspended::create(&profile, &executable, data).unwrap();
        let running = unsafe { suspended.resume().unwrap() };
        let start = Instant::now();
        let deadline = running.start_tool_call().unwrap();
        while running.active_test_processes().unwrap() != 2
            && start.elapsed() < Duration::from_secs(5)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(
            running.active_test_processes().unwrap(),
            2,
            "container child did not start"
        );
        let vault = tempfile::tempdir().unwrap();
        let mut workspace = crate::workspace::Workspace::open(vault.path()).unwrap();
        let before = workspace
            .write("deadline.md", "", b"during tool call", "local")
            .unwrap();
        eprintln!("production deadline: two managed processes; Host save succeeded; waiting 60-second budget");
        let exit = running
            .wait(Duration::from_secs(60))
            .unwrap()
            .or_else(|| running.wait(Duration::from_secs(5)).unwrap())
            .unwrap();
        assert_ne!(
            exit, 84,
            "probe ended naturally before the watchdog killed it"
        );
        while running.active_test_processes().unwrap() != 0
            && start.elapsed() < Duration::from_secs(70)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(running.active_test_processes().unwrap(), 0);
        let elapsed = start.elapsed();
        assert!(
            elapsed >= Duration::from_secs(60) && elapsed < Duration::from_secs(70),
            "{elapsed:?}"
        );
        assert_eq!(
            deadline.finish().unwrap_err().code,
            "EXTENSION_TOOL_DEADLINE_EXCEEDED"
        );
        workspace
            .write(
                "deadline.md",
                &before.hash,
                b"after process-tree cleanup",
                "local",
            )
            .unwrap();
        drop(workspace);
        let mut reopened = crate::workspace::Workspace::open(vault.path()).unwrap();
        assert_eq!(
            reopened.read("deadline.md").unwrap().content,
            "after process-tree cleanup"
        );
        eprintln!(
            "production deadline: tree empty after {elapsed:?}; Host save and reopen succeeded"
        );
        drop(reopened);
        drop(running);
        drop(entry);
        drop(root);
        profile.remove().unwrap();
    }
}
