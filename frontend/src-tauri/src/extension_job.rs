//! Windows resource containment only. This is NOT a filesystem/network sandbox.
use crate::workspace::{HostError, Result};
use std::{
    mem::size_of,
    os::windows::io::{AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::System::{
    JobObjects::*,
    Threading::{GetActiveProcessorCount, ALL_PROCESSOR_GROUPS},
};

pub struct Job {
    handle: OwnedHandle,
}
impl Job {
    pub fn new() -> Result<Self> {
        Self::with_process_limit(16)
    }
    fn with_process_limit(processes: u32) -> Result<Self> {
        let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if raw.is_null() {
            return Err(HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"));
        }
        let job = Self {
            handle: unsafe { OwnedHandle::from_raw_handle(raw) },
        };
        let limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
            BasicLimitInformation: JOBOBJECT_BASIC_LIMIT_INFORMATION {
                LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                    | JOB_OBJECT_LIMIT_JOB_MEMORY,
                ActiveProcessLimit: processes,
                ..Default::default()
            },
            JobMemoryLimit: 512 * 1024 * 1024,
            ..Default::default()
        };
        job.set(JobObjectExtendedLimitInformation, &limits)?;
        let processors = unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) };
        if processors == 0 {
            return Err(HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"));
        }
        let cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION {
            ControlFlags: JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
            Anonymous: JOBOBJECT_CPU_RATE_CONTROL_INFORMATION_0 {
                CpuRate: (10000 / processors).max(1),
            },
        };
        job.set(JobObjectCpuRateControlInformation, &cpu)?;
        Ok(job)
    }
    fn set<T>(&self, class: JOBOBJECTINFOCLASS, value: &T) -> Result<()> {
        if unsafe {
            SetInformationJobObject(
                self.handle.as_raw_handle(),
                class,
                (value as *const T).cast(),
                size_of::<T>() as u32,
            )
        } == 0
        {
            return Err(HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"));
        }
        Ok(())
    }
    /// Attach before any extension instruction executes. No breakaway flags are enabled.
    ///
    /// # Safety
    /// Caller must own an unresumed CREATE_SUSPENDED process and terminate it on
    /// any error. Resume only after all AppContainer/handle/permission checks pass.
    pub unsafe fn assign_suspended(&self, process: BorrowedHandle<'_>) -> Result<()> {
        if unsafe { AssignProcessToJobObject(self.handle.as_raw_handle(), process.as_raw_handle()) }
            == 0
        {
            return Err(HostError::new("EXTENSION_RESOURCE_ASSIGN_FAILED"));
        }
        Ok(())
    }
    pub fn terminate(&self) -> Result<()> {
        if unsafe { TerminateJobObject(self.handle.as_raw_handle(), 1) } == 0 {
            return Err(HostError::new("EXTENSION_RESOURCE_TERMINATE_FAILED"));
        }
        Ok(())
    }
    pub fn active_processes(&self) -> Result<u32> {
        let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        if unsafe {
            QueryInformationJobObject(
                self.handle.as_raw_handle(),
                JobObjectBasicAccountingInformation,
                (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(HostError::new("EXTENSION_RESOURCE_QUERY_FAILED"));
        }
        Ok(accounting.ActiveProcesses)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::windows::process::CommandExt;
    use std::{
        os::windows::{ffi::OsStrExt, io::AsHandle},
        time::Duration,
    };
    use windows_sys::Win32::{Foundation::WAIT_OBJECT_0, System::Threading::*};
    struct Worker {
        process: OwnedHandle,
        thread: OwnedHandle,
    }
    impl Drop for Worker {
        fn drop(&mut self) {
            unsafe {
                TerminateProcess(self.process.as_raw_handle(), 1);
                WaitForSingleObject(self.process.as_raw_handle(), 5000);
            }
        }
    }
    fn worker() -> Worker {
        worker_named("worker_wait")
    }
    fn worker_named(name: &str) -> Worker {
        let exe = std::env::current_exe().unwrap();
        let app: Vec<u16> = exe.as_os_str().encode_wide().chain(Some(0)).collect();
        let command = format!(
            "\"{}\" --ignored --exact extension_job::tests::{name} --nocapture",
            exe.display()
        );
        let mut command: Vec<u16> = command.encode_utf16().chain(Some(0)).collect();
        let startup = STARTUPINFOW {
            cb: size_of::<STARTUPINFOW>() as u32,
            ..Default::default()
        };
        let mut info = PROCESS_INFORMATION::default();
        let empty_environment = [0u16, 0];
        assert_ne!(
            unsafe {
                CreateProcessW(
                    app.as_ptr(),
                    command.as_mut_ptr(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                    empty_environment.as_ptr().cast(),
                    std::ptr::null(),
                    &startup,
                    &mut info,
                )
            },
            0
        );
        Worker {
            process: unsafe { OwnedHandle::from_raw_handle(info.hProcess) },
            thread: unsafe { OwnedHandle::from_raw_handle(info.hThread) },
        }
    }
    #[test]
    #[ignore = "helper process launched suspended by resource containment test"]
    fn worker_wait() {
        std::thread::sleep(Duration::from_secs(30));
    }
    #[test]
    #[ignore = "helper process creates a managed descendant for the parent test"]
    fn worker_tree() {
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .creation_flags(CREATE_NO_WINDOW)
            .args([
                "--ignored",
                "--exact",
                "extension_job::tests::worker_wait",
                "--nocapture",
            ])
            .spawn()
            .unwrap();
        child.wait().unwrap();
    }
    #[test]
    #[ignore = "helper probes allocations inside the parent's limited job"]
    fn worker_memory() {
        let mut normal = Vec::<u8>::new();
        normal.try_reserve_exact(32 * 1024 * 1024).unwrap();
        normal.resize(32 * 1024 * 1024, 1);
        assert_eq!(normal[normal.len() - 1], 1);
        let mut excessive = Vec::<u8>::new();
        assert!(excessive.try_reserve_exact(600 * 1024 * 1024).is_err());
    }
    #[test]
    fn actual_allocation_above_job_memory_budget_is_refused() {
        let job = Job::new().unwrap();
        let child = worker_named("worker_memory");
        unsafe {
            job.assign_suspended(child.process.as_handle()).unwrap();
            assert_ne!(ResumeThread(child.thread.as_raw_handle()), u32::MAX);
        }
        assert_eq!(
            unsafe { WaitForSingleObject(child.process.as_raw_handle(), 10000) },
            WAIT_OBJECT_0
        );
        let mut code = 1;
        assert_ne!(
            unsafe { GetExitCodeProcess(child.process.as_raw_handle(), &mut code) },
            0
        );
        assert_eq!(code, 0);
    }
    #[test]
    fn managed_descendants_terminate_with_their_job() {
        let job = Job::new().unwrap();
        let root = worker_named("worker_tree");
        unsafe {
            job.assign_suspended(root.process.as_handle()).unwrap();
            assert_ne!(ResumeThread(root.thread.as_raw_handle()), u32::MAX);
        }
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while job.active_processes().unwrap() < 2 && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(job.active_processes().unwrap(), 2);
        job.terminate().unwrap();
        assert_eq!(
            unsafe { WaitForSingleObject(root.process.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while job.active_processes().unwrap() != 0 && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(job.active_processes().unwrap(), 0);
    }
    #[test]
    fn actual_suspended_process_assignment_limits_and_close_cleanup() {
        let job = Job::with_process_limit(1).unwrap();
        let first = worker();
        unsafe {
            job.assign_suspended(first.process.as_handle()).unwrap();
        }
        assert_eq!(job.active_processes().unwrap(), 1);
        let second = worker();
        assert!(unsafe { job.assign_suspended(second.process.as_handle()) }.is_err());
        // The second process has never been resumed, even after assignment failure.
        drop(second);
        assert_ne!(
            unsafe { ResumeThread(first.thread.as_raw_handle()) },
            u32::MAX
        );
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while job.active_processes().unwrap() != 1 && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(job.active_processes().unwrap(), 1);
        drop(job);
        assert_eq!(
            unsafe { WaitForSingleObject(first.process.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
    }
    #[test]
    fn production_limits_are_configured_and_explicit_termination_reaps_process() {
        let job = Job::new().unwrap();
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        assert_ne!(
            unsafe {
                QueryInformationJobObject(
                    job.handle.as_raw_handle(),
                    JobObjectExtendedLimitInformation,
                    (&mut info as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        assert_eq!(info.JobMemoryLimit, 512 * 1024 * 1024);
        assert_eq!(info.BasicLimitInformation.ActiveProcessLimit, 16);
        assert_eq!(
            info.BasicLimitInformation.LimitFlags
                & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK),
            0
        );
        let mut cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION::default();
        assert_ne!(
            unsafe {
                QueryInformationJobObject(
                    job.handle.as_raw_handle(),
                    JobObjectCpuRateControlInformation,
                    (&mut cpu as *mut JOBOBJECT_CPU_RATE_CONTROL_INFORMATION).cast(),
                    size_of::<JOBOBJECT_CPU_RATE_CONTROL_INFORMATION>() as u32,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        assert_eq!(
            cpu.ControlFlags,
            JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        );
        let child = worker();
        unsafe {
            job.assign_suspended(child.process.as_handle()).unwrap();
        }
        job.terminate().unwrap();
        assert_eq!(
            unsafe { WaitForSingleObject(child.process.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
    }
}
