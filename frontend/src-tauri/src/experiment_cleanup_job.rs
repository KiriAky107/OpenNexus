//! Durable Job creation protocol. A name alone is never an ownership receipt.
//! The fresh-create acknowledgement is committed before atomic child creation.
use crate::workspace::{HostError, Result};
use serde::{Deserialize, Serialize};
use std::{
    mem::size_of,
    os::windows::io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{
    Foundation::*,
    System::{JobObjects::*, SystemServices::JOB_OBJECT_QUERY, Threading::*},
};

fn failed() -> HostError {
    HostError::new("EXPERIMENT_CLEANUP_JOB_UNVERIFIED")
}
fn time(filetime: FILETIME) -> u64 {
    (u64::from(filetime.dwHighDateTime) << 32) | u64::from(filetime.dwLowDateTime)
}
fn creation_time(handle: BorrowedHandle<'_>) -> Result<u64> {
    let (mut created, mut exited, mut kernel, mut user) = (
        FILETIME::default(),
        FILETIME::default(),
        FILETIME::default(),
        FILETIME::default(),
    );
    if unsafe {
        GetProcessTimes(
            handle.as_raw_handle(),
            &mut created,
            &mut exited,
            &mut kernel,
            &mut user,
        )
    } == 0
        || time(created) == 0
    {
        return Err(failed());
    }
    Ok(time(created))
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProcessIdentity {
    pid: u32,
    created: u64,
}
impl ProcessIdentity {
    fn current() -> Result<Self> {
        Ok(Self {
            pid: unsafe { GetCurrentProcessId() },
            created: creation_time(unsafe { BorrowedHandle::borrow_raw(GetCurrentProcess()) })?,
        })
    }
    fn exited(&self) -> Result<bool> {
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                self.pid,
            )
        };
        if raw.is_null() {
            return if unsafe { GetLastError() } == ERROR_INVALID_PARAMETER {
                Ok(true)
            } else {
                Err(failed())
            };
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
        // PID reuse is checked against the kernel's original creation time.
        if creation_time(handle.as_handle())? != self.created {
            return Ok(true);
        }
        match unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) } {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(failed()),
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobPhase {
    Planned,
    Configured,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobReceipt {
    pub name: String,
    pub phase: JobPhase,
    owner: ProcessIdentity,
}
/// The only acknowledgement of a newly created kernel object. It holds the
/// original non-inheritable handle until native policy verification commits.
pub(crate) struct FreshJob {
    handle: OwnedHandle,
    receipt: JobReceipt,
}
pub(crate) struct ConfiguredJob {
    receipt: JobReceipt,
}
impl ConfiguredJob {
    pub(crate) fn receipt(self) -> JobReceipt {
        self.receipt
    }
}
fn policy(handle: BorrowedHandle<'_>) -> Result<()> {
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    if unsafe {
        QueryInformationJobObject(
            handle.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&mut limits as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(failed());
    }
    let flags = limits.BasicLimitInformation.LimitFlags;
    if flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0
        || flags & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK) != 0
    {
        return Err(failed());
    }
    Ok(())
}
fn accounting(handle: BorrowedHandle<'_>) -> Result<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION> {
    let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
    if unsafe {
        QueryInformationJobObject(
            handle.as_raw_handle(),
            JobObjectBasicAccountingInformation,
            (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(failed());
    }
    Ok(info)
}
impl JobReceipt {
    pub(crate) fn planned(token: &str) -> Result<Self> {
        let id = uuid::Uuid::parse_str(token).map_err(|_| failed())?;
        Ok(Self {
            name: format!("Global\\OpenNexus.experiment.{}", id.simple()),
            phase: JobPhase::Planned,
            owner: ProcessIdentity::current()?,
        })
    }
    pub(crate) fn validate(&self, token: &str) -> Result<()> {
        let id = uuid::Uuid::parse_str(token).map_err(|_| failed())?;
        if self.name != format!("Global\\OpenNexus.experiment.{}", id.simple())
            || self.owner.pid == 0
            || self.owner.created == 0
        {
            return Err(failed());
        }
        Ok(())
    }
    pub(crate) fn create_fresh(&self) -> Result<FreshJob> {
        if self.phase != JobPhase::Planned || self.owner != ProcessIdentity::current()? {
            return Err(failed());
        }
        let name: Vec<_> = self.name.encode_utf16().chain(Some(0)).collect();
        unsafe {
            SetLastError(0);
        }
        // NULL attributes make the returned handle non-inheritable. The
        // creator's token supplies the default object security descriptor.
        let raw = unsafe { CreateJobObjectW(std::ptr::null(), name.as_ptr()) };
        let error = unsafe { GetLastError() };
        if raw.is_null() {
            return Err(failed());
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
        if error == ERROR_ALREADY_EXISTS {
            // Only drop this extra handle. Do not configure, terminate or take
            // ownership of an existing Job with the requested name.
            return Err(HostError::new("EXPERIMENT_CLEANUP_JOB_COLLISION"));
        }
        Ok(FreshJob {
            handle,
            receipt: self.clone(),
        })
    }
    /// Read-only evidence for a later, separately authorized cleanup. This
    /// neither acquires the Host cleanup lease nor clears any obligation.
    pub fn stopped_after_owner_exit(&self) -> Result<()> {
        self.stopped(false)
    }
    pub(crate) fn stopped_under_lease(
        &self,
        _lease: &crate::experiment_cleanup_lease::Lease,
    ) -> Result<()> {
        // The exclusive native lease spans the complete worker lifetime. Its
        // acquisition proves that this Host cannot still own a running factory.
        // Another live Host is rejected, even if its lease has become available.
        self.stopped(self.owner == ProcessIdentity::current()?)
    }
    fn stopped(&self, same_host_stopped: bool) -> Result<()> {
        let token = self
            .name
            .strip_prefix("Global\\OpenNexus.experiment.")
            .ok_or_else(failed)?;
        self.validate(token)?;
        if !same_host_stopped && !self.owner.exited()? {
            return Err(HostError::new("EXPERIMENT_CLEANUP_OWNER_ACTIVE"));
        }
        if self.phase == JobPhase::Planned {
            // Every owned process factory must first commit Configured. A
            // planned record cannot have entered CreateProcessW at all.
            return Ok(());
        }
        let name: Vec<_> = self.name.encode_utf16().chain(Some(0)).collect();
        let raw = unsafe { OpenJobObjectW(JOB_OBJECT_QUERY, 0, name.as_ptr()) };
        if raw.is_null() {
            // The configured non-breakaway Job was atomically assigned at
            // creation. Windows destroys it only after all its processes exit.
            // Access-denied or another query error is not absence evidence.
            return if unsafe { GetLastError() } == ERROR_FILE_NOT_FOUND {
                Ok(())
            } else {
                Err(failed())
            };
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
        policy(handle.as_handle())?;
        if accounting(handle.as_handle())?.ActiveProcesses != 0 {
            return Err(HostError::new("EXPERIMENT_CLEANUP_JOB_ACTIVE"));
        }
        Ok(())
    }
}
impl FreshJob {
    pub(crate) fn clone_handle(&self) -> Result<OwnedHandle> {
        self.handle.try_clone().map_err(|_| failed())
    }
    pub(crate) fn configured(&self, actual: BorrowedHandle<'_>) -> Result<ConfiguredJob> {
        if unsafe { CompareObjectHandles(self.handle.as_raw_handle(), actual.as_raw_handle()) } == 0
        {
            return Err(failed());
        }
        let mut flags = 0;
        if unsafe { GetHandleInformation(actual.as_raw_handle(), &mut flags) } == 0
            || flags & HANDLE_FLAG_INHERIT != 0
        {
            return Err(failed());
        }
        policy(self.handle.as_handle())?;
        let info = accounting(self.handle.as_handle())?;
        if info.TotalProcesses != 0 || info.ActiveProcesses != 0 {
            return Err(failed());
        }
        let mut receipt = self.receipt.clone();
        receipt.phase = JobPhase::Configured;
        Ok(ConfiguredJob { receipt })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        os::windows::process::CommandExt,
        process::{Command, Stdio},
        time::{Duration, Instant},
    };
    use windows_sys::Win32::System::SystemServices::JOB_OBJECT_TERMINATE;

    fn configure(fresh: &FreshJob) {
        let limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
            BasicLimitInformation: JOBOBJECT_BASIC_LIMIT_INFORMATION {
                LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                ..Default::default()
            },
            ..Default::default()
        };
        assert_ne!(
            unsafe {
                SetInformationJobObject(
                    fresh.handle.as_raw_handle(),
                    JobObjectExtendedLimitInformation,
                    (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            },
            0
        );
    }
    #[test]
    fn collision_preserves_existing_job_and_acknowledgement_requires_the_exact_owned_handle() {
        let planned = JobReceipt::planned(&uuid::Uuid::new_v4().to_string()).unwrap();
        let fresh = planned.create_fresh().unwrap();
        assert!(fresh.configured(fresh.handle.as_handle()).is_err());
        assert_eq!(
            planned.create_fresh().err().unwrap().code,
            "EXPERIMENT_CLEANUP_JOB_COLLISION"
        );
        assert!(policy(fresh.handle.as_handle()).is_err());
        assert_eq!(
            accounting(fresh.handle.as_handle()).unwrap().TotalProcesses,
            0
        );
        let other = JobReceipt::planned(&uuid::Uuid::new_v4().to_string())
            .unwrap()
            .create_fresh()
            .unwrap();
        configure(&fresh);
        configure(&other);
        assert!(fresh.configured(other.handle.as_handle()).is_err());
        let duplicate = fresh.clone_handle().unwrap();
        let confirmed = fresh.configured(duplicate.as_handle()).unwrap().receipt();
        assert_eq!(confirmed.phase, JobPhase::Configured);
        let decoded: JobReceipt =
            serde_json::from_str(&serde_json::to_string(&confirmed).unwrap()).unwrap();
        assert_eq!(decoded, confirmed);
        assert_eq!(
            decoded.stopped_after_owner_exit().unwrap_err().code,
            "EXPERIMENT_CLEANUP_OWNER_ACTIVE"
        );
        assert!(decoded.validate(&uuid::Uuid::new_v4().to_string()).is_err());
    }
    #[test]
    fn failed_durable_acknowledgement_closes_the_empty_job_and_cannot_enter_the_process_factory() {
        use crate::{
            experiment_cleanup::{Journal, ObjectReceipt},
            extension_container::Profile,
            extension_job::Job,
        };
        let root = tempfile::tempdir().unwrap();
        let journal = Journal::open(root.path()).unwrap();
        let attempt = journal
            .reserve(
                &uuid::Uuid::new_v4().to_string(),
                &uuid::Uuid::new_v4().to_string(),
            )
            .unwrap();
        attempt.before_create().unwrap();
        let profile = Profile::create_named(attempt.profile_name().into()).unwrap();
        let folder = profile.folder().unwrap();
        attempt
            .created(folder.parent().unwrap(), &profile.sid_bytes().unwrap())
            .unwrap();
        for (name, kind) in [
            ("source", crate::experiment_cleanup::GrantKind::Source),
            ("runtime", crate::experiment_cleanup::GrantKind::Runtime),
        ] {
            let path = root.path().join(name);
            std::fs::create_dir(&path).unwrap();
            std::fs::write(path.join("preserve.txt"), b"owned fixture bytes").unwrap();
            if name == "source" {
                attempt.source_created(&path).unwrap();
            } else {
                attempt.runtime_bound(&path).unwrap();
            }
            attempt
                .grants_bound(kind, vec![ObjectReceipt::directory(&path).unwrap().0])
                .unwrap();
        }
        let planned = attempt.job_plan().unwrap();
        let db =
            rusqlite::Connection::open(root.path().join("experiment-cleanup.sqlite3")).unwrap();
        db.execute_batch("CREATE TRIGGER abort_job BEFORE UPDATE ON cleanup WHEN json_extract(NEW.record,'$.job.phase')='configured' BEGIN SELECT RAISE(ABORT,'owned test fault'); END;").unwrap();
        let limits = crate::experiment_policy::ExecutionLimits::default()
            .validate()
            .unwrap();
        assert_eq!(
            Job::for_owned_experiment(&profile, &limits, &attempt)
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_CLEANUP_JOURNAL_FAILED"
        );
        assert_eq!(journal.status().unwrap().unwrap().job.unwrap(), planned);
        let name: Vec<_> = planned.name.encode_utf16().chain(Some(0)).collect();
        let raw = unsafe { OpenJobObjectW(JOB_OBJECT_QUERY, 0, name.as_ptr()) };
        if !raw.is_null() {
            drop(unsafe { OwnedHandle::from_raw_handle(raw) });
            panic!("failed acknowledgement retained its empty Job");
        }
        assert_eq!(unsafe { GetLastError() }, ERROR_FILE_NOT_FOUND);
        for name in ["source", "runtime"] {
            assert_eq!(
                std::fs::read(root.path().join(name).join("preserve.txt")).unwrap(),
                b"owned fixture bytes"
            );
        }
        db.execute_batch("DROP TRIGGER abort_job").unwrap();
        let job = Job::for_owned_experiment(&profile, &limits, &attempt).unwrap();
        assert_eq!(job.active_processes().unwrap(), 0);
        let persisted = Journal::open(root.path())
            .unwrap()
            .status()
            .unwrap()
            .unwrap()
            .job
            .unwrap();
        assert_eq!(persisted.phase, JobPhase::Configured);
        assert_eq!(persisted.name, planned.name);
        assert!(attempt.job_plan().is_err());
        drop(job);
        profile.remove().unwrap();
        assert!(
            journal.status().unwrap().is_some(),
            "native resource Drop must not erase the cleanup obligation"
        );
    }
    #[test]
    #[ignore = "Owned child fixture; launched only by the Job owner-death tests"]
    fn job_owner_child_fixture() {
        let root = std::path::PathBuf::from(
            std::env::var_os("OPENNEXUS_CLEANUP_JOB_FIXTURE_ROOT").unwrap(),
        );
        let fresh = JobReceipt::planned(&uuid::Uuid::new_v4().to_string())
            .unwrap()
            .create_fresh()
            .unwrap();
        configure(&fresh);
        let receipt = fresh
            .configured(fresh.handle.as_handle())
            .unwrap()
            .receipt();
        let command = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32/cmd.exe");
        let child = Command::new(command)
            .creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        // This fixture never resumes a shell instruction. The separate atomic
        // factory test covers the window before manual assignment could occur.
        assert_ne!(
            unsafe {
                AssignProcessToJobObject(fresh.handle.as_raw_handle(), child.as_raw_handle())
            },
            0
        );
        let ready = serde_json::json!({"receipt": receipt, "suspended_pid": child.id()});
        std::fs::write(root.join("ready.tmp"), serde_json::to_vec(&ready).unwrap()).unwrap();
        std::fs::rename(root.join("ready.tmp"), root.join("ready.json")).unwrap();
        loop {
            std::thread::park();
        }
    }
    struct OwnedChild(std::process::Child);
    impl Drop for OwnedChild {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    fn owner_death_probe(retain_job: bool) {
        let root = tempfile::tempdir().unwrap();
        let mut owner = OwnedChild(
            Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "experiment_cleanup_job::tests::job_owner_child_fixture",
                    "--ignored",
                    "--nocapture",
                ])
                .env("OPENNEXUS_CLEANUP_JOB_FIXTURE_ROOT", root.path())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .unwrap(),
        );
        let deadline = Instant::now() + Duration::from_secs(10);
        let ready = loop {
            if root.path().join("ready.json").is_file() {
                break serde_json::from_slice::<serde_json::Value>(
                    &std::fs::read(root.path().join("ready.json")).unwrap(),
                )
                .unwrap();
            }
            assert!(
                owner.0.try_wait().unwrap().is_none(),
                "owned fixture exited before readiness"
            );
            assert!(
                Instant::now() < deadline,
                "owned fixture did not become ready"
            );
            std::thread::sleep(Duration::from_millis(10));
        };
        let receipt: JobReceipt = serde_json::from_value(ready["receipt"].clone()).unwrap();
        assert_eq!(
            receipt.stopped_after_owner_exit().unwrap_err().code,
            "EXPERIMENT_CLEANUP_OWNER_ACTIVE"
        );
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                ready["suspended_pid"].as_u64().unwrap() as u32,
            )
        };
        assert!(!raw.is_null());
        let process = unsafe { OwnedHandle::from_raw_handle(raw) };
        assert_eq!(
            unsafe { WaitForSingleObject(process.as_raw_handle(), 0) },
            WAIT_TIMEOUT
        );
        // A deliberately retained test-owned handle prevents last-close kill.
        // The production verifier must report this live Job without killing it.
        let held = if retain_job {
            let name: Vec<_> = receipt.name.encode_utf16().chain(Some(0)).collect();
            let raw = unsafe {
                OpenJobObjectW(JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE, 0, name.as_ptr())
            };
            assert!(!raw.is_null());
            Some(unsafe { OwnedHandle::from_raw_handle(raw) })
        } else {
            None
        };
        owner.0.kill().unwrap();
        owner.0.wait().unwrap();
        if let Some(held) = &held {
            assert_eq!(
                receipt.stopped_after_owner_exit().unwrap_err().code,
                "EXPERIMENT_CLEANUP_JOB_ACTIVE"
            );
            assert_eq!(
                unsafe { WaitForSingleObject(process.as_raw_handle(), 0) },
                WAIT_TIMEOUT
            );
            // Explicitly stop only this test's known owned process tree.
            assert_ne!(unsafe { TerminateJobObject(held.as_raw_handle(), 1) }, 0);
        }
        assert_eq!(
            unsafe { WaitForSingleObject(process.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
        receipt.stopped_after_owner_exit().unwrap();
    }
    #[test]
    fn real_owner_death_kills_the_suspended_child_and_proves_job_exit() {
        owner_death_probe(false);
    }
    #[test]
    fn another_handle_retaining_a_live_job_blocks_read_only_exit_verification() {
        owner_death_probe(true);
    }
}
