//! Windows 进程所有权原语。暂停的进程不是执行授权；扩展运行时必须在恢复之前完成其检查。
use crate::{
    extension_container::Profile,
    extension_job::Job,
    extension_launch_data::LaunchData,
    workspace::{HostError, Result},
};
use std::{
    mem::size_of,
    os::windows::{
        ffi::OsStrExt,
        io::{AsHandle, AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::Path,
    time::Duration,
};
use windows_sys::Win32::{
    Foundation::{WAIT_OBJECT_0, WAIT_TIMEOUT},
    Security::*,
    System::Threading::*,
};

struct Attributes {
    buffer: Vec<usize>,
    initialized: bool,
}
impl Attributes {
    fn new(count: u32) -> Result<Self> {
        let bad = || HostError::new("EXTENSION_PROCESS_ATTRIBUTES_FAILED");
        let mut bytes = 0;
        unsafe {
            InitializeProcThreadAttributeList(std::ptr::null_mut(), count, 0, &mut bytes);
        }
        if bytes == 0 || bytes > 65536 {
            return Err(bad());
        }
        let mut value = Self {
            buffer: vec![0; bytes.div_ceil(size_of::<usize>())],
            initialized: false,
        };
        if unsafe {
            InitializeProcThreadAttributeList(
                value.buffer.as_mut_ptr().cast(),
                count,
                0,
                &mut bytes,
            )
        } == 0
        {
            return Err(bad());
        }
        value.initialized = true;
        // 该属性存储指向SECURITY_CAPABILITIES的指针。调用者使用在 CreateProcessW 之前保持活动状态的存储来更新它。
        Ok(value)
    }
}
impl Drop for Attributes {
    fn drop(&mut self) {
        if self.initialized {
            unsafe {
                DeleteProcThreadAttributeList(self.buffer.as_mut_ptr().cast());
            }
        }
    }
}
struct Handles {
    process: OwnedHandle,
    thread: OwnedHandle,
}
impl Drop for Handles {
    fn drop(&mut self) {
        unsafe {
            TerminateProcess(self.process.as_raw_handle(), 1);
            WaitForSingleObject(self.process.as_raw_handle(), 5000);
        }
    }
}
struct Process<'a> {
    handles: Handles,
    job: Job,
    _profile: &'a Profile,
    #[cfg(feature = "desktop")]
    _bound_entry: Option<&'a crate::extension_pinned::BoundEntry<'a>>,
}
impl Drop for Process<'_> {
    fn drop(&mut self) {
        let _ = self.job.terminate();
    }
}
pub struct Suspended<'a>(Process<'a>);
pub struct Running<'a> {
    process: Process<'a>,
    #[cfg(feature = "desktop")]
    revocation: Option<crate::extension_revocation::Watch>,
    #[cfg(feature = "desktop")]
    identity: Option<crate::extension_call_authorization::Identity>,
}
impl<'a> Suspended<'a> {
    /// 使用显式环境和工作目录创建隐藏进程，不继承任意句柄。Profile 借用会在
    /// 所有者存活期间阻止清理；此 API 永远不会恢复扩展指令。
    pub fn create(profile: &'a Profile, executable: &Path, data: LaunchData) -> Result<Self> {
        Self::create_inner(profile, executable, data, None)
    }
    fn create_inner(
        profile: &'a Profile,
        executable: &Path,
        mut data: LaunchData,
        io: Option<crate::extension_stdio::ChildIo>,
    ) -> Result<Self> {
        let bad = || HostError::new("EXTENSION_PROCESS_CREATE_FAILED");
        if !executable.is_absolute() || data.command_mut().last() != Some(&0) {
            return Err(bad());
        }
        let executable: Vec<u16> = executable.as_os_str().encode_wide().collect();
        if executable.is_empty() || executable.len() > 32766 || executable.contains(&0) {
            return Err(bad());
        }
        let executable: Vec<_> = executable.into_iter().chain(Some(0)).collect();
        let folder = profile.folder()?;
        std::fs::create_dir_all(data.scratch())
            .map_err(|_| HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"))?;
        let scratch_metadata = std::fs::symlink_metadata(data.scratch())
            .map_err(|_| HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"))?;
        use std::os::windows::fs::MetadataExt;
        if !scratch_metadata.is_dir()
            || scratch_metadata.file_attributes() & 0x400 != 0
            || scratch_metadata.file_type().is_symlink()
        {
            return Err(HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"));
        }
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, READ_CONTROL, WRITE_DAC,
        };
        let scratch_handle = std::fs::OpenOptions::new()
            .access_mode(READ_CONTROL | WRITE_DAC)
            .share_mode(1 | 2 | 4)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(data.scratch())
            .map_err(|_| HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"))?;
        profile.grant_scratch_modify(&scratch_handle)?;
        let directory: Vec<u16> = folder.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut attributes = Attributes::new(if io.is_some() { 2 } else { 1 })?;
        let caps = SECURITY_CAPABILITIES {
            AppContainerSid: profile.sid(),
            Capabilities: std::ptr::null_mut(),
            CapabilityCount: 0,
            Reserved: 0,
        };
        if unsafe {
            UpdateProcThreadAttribute(
                attributes.buffer.as_mut_ptr().cast(),
                0,
                PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
                (&caps as *const SECURITY_CAPABILITIES).cast(),
                size_of::<SECURITY_CAPABILITIES>(),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(HostError::new("EXTENSION_PROCESS_ATTRIBUTES_FAILED"));
        }
        let job = Job::with_scratch(data.scratch())?;
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.buffer.as_mut_ptr().cast();
        // 在 CreateProcessW 返回前同时保留句柄数组和管道所有者，不允许任意
        // Host 可继承句柄进入扩展进程。
        let io = io
            .map(crate::extension_stdio::ChildIo::inherit)
            .transpose()?;
        let inherited = io.as_ref().map(|value| value.handles());
        if let Some(handles) = &inherited {
            if unsafe {
                UpdateProcThreadAttribute(
                    attributes.buffer.as_mut_ptr().cast(),
                    0,
                    PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                    handles.as_ptr().cast(),
                    size_of::<[windows_sys::Win32::Foundation::HANDLE; 3]>(),
                    std::ptr::null_mut(),
                    std::ptr::null(),
                )
            } == 0
            {
                return Err(HostError::new("EXTENSION_PROCESS_ATTRIBUTES_FAILED"));
            }
            startup.StartupInfo.dwFlags |= STARTF_USESTDHANDLES;
            startup.StartupInfo.hStdInput = handles[0];
            startup.StartupInfo.hStdOutput = handles[1];
            startup.StartupInfo.hStdError = handles[2];
        }
        let mut info = PROCESS_INFORMATION::default();
        let environment = data.environment().as_ptr();
        if unsafe {
            CreateProcessW(
                executable.as_ptr(),
                data.command_mut().as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                i32::from(io.is_some()),
                CREATE_SUSPENDED
                    | CREATE_NO_WINDOW
                    | EXTENDED_STARTUPINFO_PRESENT
                    | CREATE_UNICODE_ENVIRONMENT,
                environment.cast(),
                directory.as_ptr(),
                &startup.StartupInfo,
                &mut info,
            )
        } == 0
        {
            return Err(bad());
        }
        let handles = Handles {
            process: unsafe { OwnedHandle::from_raw_handle(info.hProcess) },
            thread: unsafe { OwnedHandle::from_raw_handle(info.hThread) },
        };
        unsafe {
            job.assign_suspended(handles.process.as_handle())?;
        }
        let process = Process {
            handles,
            job,
            _profile: profile,
            #[cfg(feature = "desktop")]
            _bound_entry: None,
        };
        verify_identity(&process.handles, profile)?;
        Ok(Self(process))
    }
    /// 包启动路径在暂停和运行的整个生命周期内保留入口守卫及包祖先句柄。
    #[cfg(feature = "desktop")]
    pub fn create_bound(
        profile: &'a Profile,
        entry: &'a crate::extension_pinned::BoundEntry<'a>,
        data: LaunchData,
    ) -> Result<Self> {
        let mut value = Self::create(profile, entry.path(), data)?;
        value.0._bound_entry = Some(entry);
        Ok(value)
    }
    /// 创建特定于实例的 stdio，而不暴露地址或信任自我报告的进程/包身份。 Host 端点永远不会被继承。
    #[cfg(feature = "desktop")]
    pub fn create_bound_with_stdio(
        profile: &'a Profile,
        entry: &'a crate::extension_pinned::BoundEntry<'a>,
        data: LaunchData,
    ) -> Result<(Self, crate::extension_stdio::HostIo)> {
        let (child, host) = crate::extension_stdio::ChildIo::create()?;
        let mut value = Self::create_inner(profile, entry.path(), data, Some(child))?;
        value.0._bound_entry = Some(entry);
        Ok((value, host))
    }
    /// # Safety
    /// 调用方必须持有已验证的包与入口句柄，并在调用前立即复核当前执行许可、信任、
    /// Vault 绑定、环境声明、broker 以及全部资源策略要求。
    /// 此底层模块不会代为执行上述授权检查。
    pub unsafe fn resume(self) -> Result<Running<'a>> {
        self.0.job.check_resources()?;
        if unsafe { ResumeThread(self.0.handles.thread.as_raw_handle()) } != 1 {
            return Err(HostError::new("EXTENSION_PROCESS_RESUME_FAILED"));
        }
        Ok(Running {
            process: self.0,
            #[cfg(feature = "desktop")]
            revocation: None,
            #[cfg(feature = "desktop")]
            identity: None,
        })
    }
    /// # 安全性
    /// 必须满足与 resume 相同的完整资源、代理与信任前提；此外还要在恢复执行任何指令前启用撤销监控。
    #[cfg(feature = "desktop")]
    pub(crate) unsafe fn resume_with_lease(
        self,
        lease: crate::extension_permit::Lease,
        identity: crate::extension_call_authorization::Identity,
    ) -> Result<Running<'a>> {
        let watch = crate::extension_revocation::Watch::arm(&self.0.job, lease)?;
        watch.check()?;
        self.0.job.check_resources()?;
        if unsafe { ResumeThread(self.0.handles.thread.as_raw_handle()) } != 1 {
            return Err(HostError::new("EXTENSION_PROCESS_RESUME_FAILED"));
        }
        let running = Running {
            process: self.0,
            revocation: Some(watch),
            identity: Some(identity),
        };
        running.check_authorization()?;
        Ok(running)
    }
}
impl Running<'_> {
    #[cfg(feature = "desktop")]
    pub(crate) fn call_identity(&self) -> Result<crate::extension_call_authorization::Identity> {
        self.check_authorization()?;
        self.identity
            .clone()
            .ok_or_else(|| HostError::new("EXTENSION_CALL_BINDING_REQUIRED"))
    }
    #[cfg(feature = "desktop")]
    pub fn start_io(
        &self,
        io: crate::extension_stdio::HostIo,
    ) -> Result<crate::extension_io::Pump> {
        self.check_authorization()?;
        crate::extension_io::Pump::start(io, self.process.job.clone_for_deadline()?)
    }

    pub fn check_authorization(&self) -> Result<()> {
        self.process.job.check_resources()?;
        #[cfg(feature = "desktop")]
        if let Some(watch) = &self.revocation {
            return watch.check();
        }
        Ok(())
    }
    #[cfg(test)]
    pub(crate) fn test_job(&self) -> Result<crate::extension_job::Job> {
        self.process.job.clone_for_deadline()
    }
    #[cfg(test)]
    pub(crate) fn active_test_processes(&self) -> Result<u32> {
        self.process.job.active_processes()
    }
    /// 发送工具请求前启动计时，收到结果后结束。若启动计时失败，必须阻止调度；该计时不限制服务器生命周期。
    pub fn start_tool_call(&self) -> Result<crate::extension_deadline::ToolDeadline> {
        self.check_authorization()?;
        crate::extension_deadline::ToolDeadline::arm(&self.process.job)
    }
    #[cfg(test)]
    pub(crate) fn start_test_tool_call(
        &self,
        budget: Duration,
    ) -> Result<crate::extension_deadline::ToolDeadline> {
        crate::extension_deadline::ToolDeadline::arm_test(&self.process.job, budget)
    }
    /// 此处只进行有界观测；工具截止时间必须由运行时强制执行。
    pub fn wait(&self, timeout: Duration) -> Result<Option<u32>> {
        let milliseconds = u32::try_from(timeout.as_millis())
            .ok()
            .filter(|n| *n <= 60000)
            .ok_or_else(|| HostError::new("EXTENSION_PROCESS_WAIT_INVALID"))?;
        match unsafe {
            WaitForSingleObject(self.process.handles.process.as_raw_handle(), milliseconds)
        } {
            WAIT_TIMEOUT => Ok(None),
            WAIT_OBJECT_0 => {
                let mut code = 0;
                if unsafe {
                    GetExitCodeProcess(self.process.handles.process.as_raw_handle(), &mut code)
                } == 0
                {
                    return Err(HostError::new("EXTENSION_PROCESS_QUERY_FAILED"));
                }
                Ok(Some(code))
            }
            _ => Err(HostError::new("EXTENSION_PROCESS_WAIT_FAILED")),
        }
    }
    /// 终止整个托管组，包括后代。
    pub fn terminate(&self) -> Result<()> {
        self.process.job.terminate()
    }
}
fn verify_identity(handles: &Handles, profile: &Profile) -> Result<()> {
    let bad = || HostError::new("EXTENSION_PROCESS_IDENTITY_INVALID");
    let mut token = std::ptr::null_mut();
    if unsafe { OpenProcessToken(handles.process.as_raw_handle(), TOKEN_QUERY, &mut token) } == 0 {
        return Err(bad());
    }
    let token = unsafe { OwnedHandle::from_raw_handle(token) };
    let mut contained = 0u32;
    let mut length = 0;
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle(),
            TokenIsAppContainer,
            (&mut contained as *mut u32).cast(),
            size_of::<u32>() as u32,
            &mut length,
        )
    } == 0
        || contained != 1
        || length != 4
    {
        return Err(bad());
    }
    let query = |class: TOKEN_INFORMATION_CLASS| -> Result<Vec<usize>> {
        let mut bytes = 0;
        unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                class,
                std::ptr::null_mut(),
                0,
                &mut bytes,
            );
        }
        if !(4..=4096).contains(&bytes) {
            return Err(bad());
        }
        let mut buffer = vec![0usize; (bytes as usize).div_ceil(size_of::<usize>())];
        let capacity = bytes;
        if unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                class,
                buffer.as_mut_ptr().cast(),
                capacity,
                &mut bytes,
            )
        } == 0
            || bytes < 4
            || bytes > capacity
        {
            return Err(bad());
        }
        Ok(buffer)
    };
    let identity = query(TokenAppContainerSid)?;
    if identity.len() * size_of::<usize>() < size_of::<TOKEN_APPCONTAINER_INFORMATION>() {
        return Err(bad());
    }
    let identity = unsafe { &*identity.as_ptr().cast::<TOKEN_APPCONTAINER_INFORMATION>() };
    if identity.TokenAppContainer.is_null()
        || unsafe { EqualSid(identity.TokenAppContainer, profile.sid()) } == 0
    {
        return Err(bad());
    }
    let capabilities = query(TokenCapabilities)?;
    if unsafe { *capabilities.as_ptr().cast::<u32>() } != 0 {
        return Err(bad());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn data(profile: &Profile, entry: &Path) -> LaunchData {
        let system = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap());
        let folder = profile.folder().unwrap();
        LaunchData::new(
            entry,
            &[],
            &system,
            &folder,
            &folder.join("Temp"),
            &std::collections::BTreeMap::new(),
        )
        .unwrap()
    }
    #[test]
    fn dropped_suspended_owner_terminates_process_and_wrong_identity_is_rejected() {
        let profile = Profile::create().unwrap();
        let other = Profile::create().unwrap();
        let entry = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32/cmd.exe");
        let suspended = Suspended::create(&profile, &entry, data(&profile, &entry)).unwrap();
        assert_eq!(
            verify_identity(&suspended.0.handles, &other)
                .unwrap_err()
                .code,
            "EXTENSION_PROCESS_IDENTITY_INVALID"
        );
        let observer = suspended.0.handles.process.try_clone().unwrap();
        assert_eq!(
            unsafe { WaitForSingleObject(observer.as_raw_handle(), 0) },
            WAIT_TIMEOUT
        );
        drop(suspended); // 从未恢复任何命令解释器指令。
        assert_eq!(
            unsafe { WaitForSingleObject(observer.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
        drop(observer);
        other.remove().unwrap();
        profile.remove().unwrap();
    }
    #[test]
    fn invalid_command_and_missing_entry_fail_without_consuming_profile() {
        let profile = Profile::create().unwrap();
        let directory = tempfile::tempdir().unwrap();
        let entry = directory.path().join("does-not-exist.exe");
        let error = Suspended::create(&profile, &entry, data(&profile, &entry))
            .err()
            .unwrap();
        assert_eq!(error.code, "EXTENSION_PROCESS_CREATE_FAILED");
        let mut invalid = data(&profile, &entry);
        *invalid.command_mut().last_mut().unwrap() = 1;
        assert_eq!(
            Suspended::create(&profile, &entry, invalid)
                .err()
                .unwrap()
                .code,
            "EXTENSION_PROCESS_CREATE_FAILED"
        );
        assert!(profile.folder().unwrap().is_dir());
        profile.remove().unwrap();
    }
}
