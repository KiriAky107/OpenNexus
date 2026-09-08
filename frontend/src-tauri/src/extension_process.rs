//! Windows process ownership primitive. A suspended process is not execution
//! authorization; the extension runtime must complete its checks before resume.
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
    fn new() -> Result<Self> {
        let bad = || HostError::new("EXTENSION_PROCESS_ATTRIBUTES_FAILED");
        let mut bytes = 0;
        unsafe {
            InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut bytes);
        }
        if bytes == 0 || bytes > 65536 {
            return Err(bad());
        }
        let mut value = Self {
            buffer: vec![0; bytes.div_ceil(size_of::<usize>())],
            initialized: false,
        };
        if unsafe {
            InitializeProcThreadAttributeList(value.buffer.as_mut_ptr().cast(), 1, 0, &mut bytes)
        } == 0
        {
            return Err(bad());
        }
        value.initialized = true;
        // The attribute stores a pointer to SECURITY_CAPABILITIES. The caller
        // updates it with storage that remains alive until CreateProcessW.
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
}
impl Drop for Process<'_> {
    fn drop(&mut self) {
        let _ = self.job.terminate();
    }
}
pub struct Suspended<'a>(Process<'a>);
pub struct Running<'a>(Process<'a>);
impl<'a> Suspended<'a> {
    /// Creates hidden, with no inherited handles and an explicit environment and
    /// current directory. The profile borrow prevents cleanup while this owner
    /// exists. This API never resumes extension instructions.
    pub fn create(profile: &'a Profile, executable: &Path, mut data: LaunchData) -> Result<Self> {
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
        let directory: Vec<u16> = folder.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut attributes = Attributes::new()?;
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
        let job = Job::new()?;
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.buffer.as_mut_ptr().cast();
        let mut info = PROCESS_INFORMATION::default();
        let environment = data.environment().as_ptr();
        if unsafe {
            CreateProcessW(
                executable.as_ptr(),
                data.command_mut().as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                0,
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
        };
        verify_identity(&process.handles, profile)?;
        Ok(Self(process))
    }
    /// # Safety
    /// Caller must hold the verified package/entry handles and revalidate the
    /// current execution permit, trust, Vault binding, environment declarations,
    /// broker and all resource policy requirements immediately before this call.
    /// None of those authorization checks is supplied by this low-level module.
    pub unsafe fn resume(self) -> Result<Running<'a>> {
        if unsafe { ResumeThread(self.0.handles.thread.as_raw_handle()) } != 1 {
            return Err(HostError::new("EXTENSION_PROCESS_RESUME_FAILED"));
        }
        Ok(Running(self.0))
    }
}
impl Running<'_> {
    #[cfg(test)]
    pub(crate) fn active_test_processes(&self) -> Result<u32> {
        self.0.job.active_processes()
    }
    /// Arm before dispatching a tool request; finish after receiving its result.
    /// Failure to arm must prevent dispatch. This does not time server lifetime.
    pub fn start_tool_call(&self) -> Result<crate::extension_deadline::ToolDeadline> {
        crate::extension_deadline::ToolDeadline::arm(&self.0.job)
    }
    #[cfg(test)]
    pub(crate) fn start_test_tool_call(
        &self,
        budget: Duration,
    ) -> Result<crate::extension_deadline::ToolDeadline> {
        crate::extension_deadline::ToolDeadline::arm_test(&self.0.job, budget)
    }
    /// A bounded observation only. The runtime must enforce the tool deadline.
    pub fn wait(&self, timeout: Duration) -> Result<Option<u32>> {
        let milliseconds = u32::try_from(timeout.as_millis())
            .ok()
            .filter(|n| *n <= 60000)
            .ok_or_else(|| HostError::new("EXTENSION_PROCESS_WAIT_INVALID"))?;
        match unsafe { WaitForSingleObject(self.0.handles.process.as_raw_handle(), milliseconds) } {
            WAIT_TIMEOUT => Ok(None),
            WAIT_OBJECT_0 => {
                let mut code = 0;
                if unsafe { GetExitCodeProcess(self.0.handles.process.as_raw_handle(), &mut code) }
                    == 0
                {
                    return Err(HostError::new("EXTENSION_PROCESS_QUERY_FAILED"));
                }
                Ok(Some(code))
            }
            _ => Err(HostError::new("EXTENSION_PROCESS_WAIT_FAILED")),
        }
    }
    /// Terminates the entire managed group, including descendants.
    pub fn terminate(&self) -> Result<()> {
        self.0.job.terminate()
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
        drop(suspended); // Never resumed any command interpreter instruction.
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
