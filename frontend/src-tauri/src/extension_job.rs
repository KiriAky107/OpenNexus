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
    state: std::sync::Arc<std::sync::atomic::AtomicU8>,
    _monitor: Option<ResourceMonitor>,
}
impl Job {
    pub(crate) fn clone_for_deadline(&self) -> Result<Self> {
        Ok(Self {
            state: std::sync::Arc::clone(&self.state),
            _monitor: None,
            handle: self
                .handle
                .try_clone()
                .map_err(|_| HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"))?,
        })
    }
    pub fn new() -> Result<Self> {
        Self::with_process_limit(16)
    }
    fn with_process_limit(processes: u32) -> Result<Self> {
        let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if raw.is_null() {
            return Err(HostError::new("EXTENSION_RESOURCE_UNAVAILABLE"));
        }
        let mut job = Self {
            handle: unsafe { OwnedHandle::from_raw_handle(raw) },
            state: std::sync::Arc::new(std::sync::atomic::AtomicU8::new(0)),
            _monitor: None,
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
            ControlFlags: JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
                | JOB_OBJECT_CPU_RATE_CONTROL_NOTIFY,
            Anonymous: JOBOBJECT_CPU_RATE_CONTROL_INFORMATION_0 {
                CpuRate: (10000 / processors).max(1),
            },
        };
        job.set(JobObjectCpuRateControlInformation, &cpu)?;
        job._monitor = Some(ResourceMonitor::arm(&job)?);
        Ok(job)
    }
    pub fn check_resources(&self) -> Result<()> {
        use std::sync::atomic::Ordering;
        match self.state.load(Ordering::Acquire) {
            0 => Ok(()),
            1 => Err(HostError::new("EXTENSION_RESOURCE_CPU_EXCEEDED")),
            3 => Err(HostError::new("EXTENSION_RESOURCE_TERMINATE_FAILED")),
            4 => Err(HostError::new("EXTENSION_RESOURCE_MEMORY_EXCEEDED")),
            5 => Err(HostError::new("EXTENSION_RESOURCE_PROCESSES_EXCEEDED")),
            _ => Err(HostError::new("EXTENSION_RESOURCE_MONITOR_FAILED")),
        }
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
        self.check_resources()?;
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

/// The Windows notification uses a ten-second window and ToleranceHigh (60%
/// over budget). This is not a measurement of ten uninterrupted busy seconds.
/// Only the original Job owns this monitor; observation/deadline clones do not.
const JOB_MEMORY_LIMIT: u32 = 10; // JOB_OBJECT_MSG_JOB_MEMORY_LIMIT
const JOB_PROCESS_LIMIT: u32 = 3; // JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT
const JOB_NOTIFICATION_LIMIT: u32 = 11; // JOB_OBJECT_MSG_NOTIFICATION_LIMIT (Windows SDK)
struct ResourceMonitor {
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl ResourceMonitor {
    fn arm(job: &Job) -> Result<Self> {
        use std::sync::{
            atomic::{AtomicBool, Ordering},
            Arc,
        };
        use windows_sys::Win32::{Foundation::INVALID_HANDLE_VALUE, System::IO::*};
        let raw =
            unsafe { CreateIoCompletionPort(INVALID_HANDLE_VALUE, std::ptr::null_mut(), 0, 1) };
        if raw.is_null() {
            return Err(HostError::new("EXTENSION_RESOURCE_MONITOR_UNAVAILABLE"));
        }
        let port = unsafe { OwnedHandle::from_raw_handle(raw) };
        job.set(
            JobObjectAssociateCompletionPortInformation,
            &JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
                CompletionKey: std::ptr::dangling_mut::<u8>().cast(),
                CompletionPort: port.as_raw_handle(),
            },
        )?;
        job.set(
            JobObjectNotificationLimitInformation,
            &JOBOBJECT_NOTIFICATION_LIMIT_INFORMATION {
                LimitFlags: JOB_OBJECT_LIMIT_RATE_CONTROL,
                RateControlTolerance: ToleranceHigh,
                RateControlToleranceInterval: ToleranceIntervalShort,
                ..Default::default()
            },
        )?;
        let owned_job = job
            .handle
            .try_clone()
            .map_err(|_| HostError::new("EXTENSION_RESOURCE_MONITOR_UNAVAILABLE"))?;
        let state = Arc::clone(&job.state);
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = Arc::clone(&stop);
        let worker = std::thread::Builder::new()
            .name("extension-resources".into())
            .spawn(move || {
                // All exits, including a caught panic or completion-port failure,
                // terminate the tree while this worker still owns a Job handle.
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    while !thread_stop.load(Ordering::Acquire) {
                        let (mut code, mut key, mut pointer) = (0, 0, std::ptr::null_mut());
                        let ok = unsafe {
                            GetQueuedCompletionStatus(
                                port.as_raw_handle(),
                                &mut code,
                                &mut key,
                                &mut pointer,
                                100,
                            )
                        };
                        if ok == 0 {
                            if unsafe { windows_sys::Win32::Foundation::GetLastError() }
                                == windows_sys::Win32::Foundation::WAIT_TIMEOUT
                            {
                                continue;
                            }
                            return 2;
                        }
                        if key != 1 {
                            return 2;
                        }
                        // These hard-limit notifications are best effort on Windows;
                        // the kernel still enforces the configured allocation caps.
                        if code == JOB_MEMORY_LIMIT {
                            return 4;
                        }
                        if code == JOB_PROCESS_LIMIT {
                            return 5;
                        }
                        if code != JOB_NOTIFICATION_LIMIT {
                            continue;
                        }
                        let mut info = JOBOBJECT_LIMIT_VIOLATION_INFORMATION::default();
                        if unsafe {
                            QueryInformationJobObject(
                                owned_job.as_raw_handle(),
                                JobObjectLimitViolationInformation,
                                (&mut info as *mut JOBOBJECT_LIMIT_VIOLATION_INFORMATION).cast(),
                                size_of::<JOBOBJECT_LIMIT_VIOLATION_INFORMATION>() as u32,
                                std::ptr::null_mut(),
                            )
                        } == 0
                        {
                            return 2;
                        }
                        if info.ViolationLimitFlags & JOB_OBJECT_LIMIT_RATE_CONTROL != 0 {
                            return 1;
                        }
                    }
                    0
                }))
                .unwrap_or(2);
                state.store(outcome, Ordering::Release);
                if unsafe { TerminateJobObject(owned_job.as_raw_handle(), 1) } == 0 {
                    state.store(3, Ordering::Release);
                }
            })
            .map_err(|_| HostError::new("EXTENSION_RESOURCE_MONITOR_UNAVAILABLE"))?;
        Ok(Self {
            stop,
            worker: Some(worker),
        })
    }
}
impl Drop for ResourceMonitor {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
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
    #[ignore = "helper consumes CPU inside the parent controlled job"]
    fn worker_cpu() {
        let end = std::time::Instant::now() + Duration::from_secs(40);
        std::thread::scope(|scope| {
            for _ in 0..8 {
                scope.spawn(move || {
                    let mut n = 1u64;
                    while std::time::Instant::now() < end {
                        for _ in 0..10000 {
                            n = std::hint::black_box(
                                n.wrapping_mul(6364136223846793005).wrapping_add(1),
                            );
                        }
                    }
                });
            }
        });
    }
    #[test]
    #[ignore = "real ten-second CPU pressure and Host save acceptance; run explicitly"]
    fn cpu_pressure_terminates_job_and_host_can_save() {
        let idle_job = Job::new().unwrap();
        let idle = worker();
        unsafe {
            idle_job.assign_suspended(idle.process.as_handle()).unwrap();
            assert_ne!(ResumeThread(idle.thread.as_raw_handle()), u32::MAX);
        }
        let job = Job::new().unwrap();
        let mut rate = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION::default();
        assert_ne!(
            unsafe {
                QueryInformationJobObject(
                    job.handle.as_raw_handle(),
                    JobObjectCpuRateControlInformation,
                    (&mut rate as *mut JOBOBJECT_CPU_RATE_CONTROL_INFORMATION).cast(),
                    size_of::<JOBOBJECT_CPU_RATE_CONTROL_INFORMATION>() as u32,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        assert_eq!(
            rate.ControlFlags,
            JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
                | JOB_OBJECT_CPU_RATE_CONTROL_NOTIFY
        );
        assert_eq!(
            unsafe { rate.Anonymous.CpuRate },
            (10000 / unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) }).max(1)
        );
        let child = worker_named("worker_cpu");
        let start = std::time::Instant::now();
        unsafe {
            job.assign_suspended(child.process.as_handle()).unwrap();
            assert_ne!(ResumeThread(child.thread.as_raw_handle()), u32::MAX);
        }
        let vault = tempfile::tempdir().unwrap();
        let mut workspace = crate::workspace::Workspace::open(vault.path()).unwrap();
        workspace
            .write("cpu.md", "", b"while CPU is busy", "local")
            .unwrap();
        while job.check_resources().is_ok() && start.elapsed() < Duration::from_secs(20) {
            std::thread::sleep(Duration::from_millis(25));
        }
        assert_eq!(
            job.check_resources().unwrap_err().code,
            "EXTENSION_RESOURCE_CPU_EXCEEDED"
        );
        let detected = start.elapsed();
        let cleanup = std::time::Instant::now();
        while job.active_processes().unwrap() != 0 && cleanup.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(job.active_processes().unwrap(), 0);
        let saved = workspace.read("cpu.md").unwrap();
        workspace
            .write("cpu.md", &saved.entry.hash, b"after CPU cleanup", "local")
            .unwrap();
        drop(workspace);
        assert_eq!(
            crate::workspace::Workspace::open(vault.path())
                .unwrap()
                .read("cpu.md")
                .unwrap()
                .content,
            "after CPU cleanup"
        );
        eprintln!(
            "CPU pressure detected at {detected:?}; Job empty after {:?}",
            cleanup.elapsed()
        );
        idle_job.check_resources().unwrap();
        assert!(idle_job.active_processes().unwrap() > 0);
        // An observation handle must not keep the monitor alive after its owner
        // is dropped, or keep the idle process running indefinitely.
        let observation = idle_job.clone_for_deadline().unwrap();
        let stop = std::time::Instant::now();
        drop(idle_job);
        assert!(stop.elapsed() < Duration::from_secs(2));
        while observation.active_processes().unwrap() != 0
            && stop.elapsed() < Duration::from_secs(5)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(observation.active_processes().unwrap(), 0);
    }
    #[test]
    fn actual_allocation_above_job_memory_budget_is_refused() {
        let job = Job::new().unwrap();
        let child = worker_named("worker_memory");
        unsafe {
            job.assign_suspended(child.process.as_handle()).unwrap();
            assert_ne!(ResumeThread(child.thread.as_raw_handle()), u32::MAX);
        }
        assert_resource_cleanup(&job, "EXTENSION_RESOURCE_MEMORY_EXCEEDED");
    }
    fn assert_resource_cleanup(job: &Job, expected: &str) {
        let started = std::time::Instant::now();
        while job.check_resources().is_ok() && started.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(job.check_resources().unwrap_err().code, expected);
        while job.active_processes().unwrap() != 0 && started.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(job.active_processes().unwrap(), 0);
    }
    #[test]
    #[ignore = "helper attempts to exceed production child process budget"]
    fn worker_processes() {
        let mut children = Vec::new();
        for _ in 0..24 {
            match std::process::Command::new(std::env::current_exe().unwrap())
                .creation_flags(CREATE_NO_WINDOW)
                .args(["--ignored", "--exact", "extension_job::tests::worker_wait"])
                .spawn()
            {
                Ok(child) => children.push(child),
                Err(_) => break,
            }
        }
        std::thread::sleep(Duration::from_secs(30));
        for mut child in children {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    #[test]
    fn descendant_process_exhaustion_terminates_production_job() {
        let job = Job::new().unwrap();
        let child = worker_named("worker_processes");
        unsafe {
            job.assign_suspended(child.process.as_handle()).unwrap();
            assert_ne!(ResumeThread(child.thread.as_raw_handle()), u32::MAX);
        }
        assert_resource_cleanup(&job, "EXTENSION_RESOURCE_PROCESSES_EXCEEDED");
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
        // Both processes were still suspended. The attempted limit violation
        // now revokes the entire first job, rather than leaving it runnable.
        drop(second);
        assert_resource_cleanup(&job, "EXTENSION_RESOURCE_PROCESSES_EXCEEDED");
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
            JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
                | JOB_OBJECT_CPU_RATE_CONTROL_NOTIFY
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
