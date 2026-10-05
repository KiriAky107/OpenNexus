//! Closed Windows execution path. No request can supply argv, env or a shell.
use crate::{
    experiment_cleanup::Attempt,
    experiment_disk::DiskBudget,
    experiment_log::{CaptureSnapshot, LogBuffer, LogCapture},
    experiment_outputs::{CollectedOutputs, OutputReport},
    experiment_owner::RunOwner,
    experiment_runtime::{RuntimeInfo, RUNTIME_ID},
    experiment_runtime_bound::PinnedRuntime,
    experiment_sources::{RunSources, SourceEntry},
    experiment_store::{Outcome, RunResult},
    extension_container::Profile,
    extension_job::Job,
    extension_launch_data::LaunchData,
    extension_pinned::{BoundEntry, PackageAccess},
    extension_process::Suspended,
    workspace::{HostError, Result, Workspace},
};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};

pub type WorkspaceSlot = Arc<Mutex<Option<Workspace>>>;
#[derive(Clone, serde::Serialize)]
pub struct LiveRun {
    pub operation_id: String,
    pub elapsed_ms: u64,
    pub logs: CaptureSnapshot,
}
#[derive(serde::Serialize)]
pub struct ExecutionResult {
    pub result: RunResult,
    pub remaining_processes: u32,
    pub runtime: RuntimeInfo,
    pub profile_removed: bool,
    pub profile_registry_removed: bool,
    pub cleanup_complete: bool,
    #[serde(skip)]
    pub(crate) outputs: Option<CollectedOutputs>,
}
// Closed proof: only this execution module can attest to the whole stopped
// Job, revoked borrowed grants and physically removed owned roots.
pub(crate) struct CleanupProof<'a> {
    attempt: &'a Attempt,
}
impl CleanupProof<'_> {
    pub(crate) fn attempt(&self) -> &Attempt {
        self.attempt
    }
}
struct Bindings<'a> {
    runtime: &'a PinnedRuntime,
    sources: &'a RunSources<'a>,
    _runtime_access: &'a PackageAccess<'a>,
    _source_access: &'a PackageAccess<'a>,
    _runtime_entry: &'a BoundEntry<'a>,
    _source_entry: &'a SourceEntry<'a>,
}
/// No Clone/Deserialize or public constructor. The process must borrow every
/// bound object and the unique run owner until native cleanup has been proved.
pub(crate) struct NativePermit<'a> {
    owner: &'a RunOwner,
    _bindings: Bindings<'a>,
    watch: Option<StopWatch>,
}
impl<'a> NativePermit<'a> {
    fn issue(
        owner: &'a RunOwner,
        bindings: Bindings<'a>,
        workspace: &WorkspaceSlot,
    ) -> Result<Self> {
        if bindings.runtime.info().runtime_id != RUNTIME_ID
            || owner.run().record().summary.request.runtime_id != RUNTIME_ID
            || !bindings.sources.is_bound_to(owner.run())
        {
            return Err(HostError::new("EXPERIMENT_EXECUTION_BINDING_FAILED"));
        }
        let mut slot = workspace.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
        let ws = slot
            .as_mut()
            .ok_or_else(|| HostError::new("VAULT_NOT_OPEN"))?;
        owner.mark_running(ws)?;
        let permit = Self {
            owner,
            _bindings: bindings,
            watch: None,
        };
        permit.check()?;
        Ok(permit)
    }
    pub(crate) fn arm(&mut self, job: &Job) -> Result<()> {
        self.watch = Some(StopWatch::arm(job, self.owner)?);
        Ok(())
    }
    pub(crate) fn check(&self) -> Result<()> {
        if self.owner.cancellation_requested() {
            Err(HostError::new("EXPERIMENT_CANCELLED"))
        } else {
            Ok(())
        }
    }
}
struct StopWatch {
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}
impl StopWatch {
    fn arm(job: &Job, owner: &RunOwner) -> Result<Self> {
        let job = job.clone_for_deadline()?;
        let cancelled = owner.cancellation_signal();
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let worker = std::thread::Builder::new()
            .name("experiment-cancel".into())
            .spawn(move || {
                while !worker_stop.load(Ordering::Acquire) {
                    if cancelled.load(Ordering::Acquire) && job.terminate().is_ok() {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
            })
            .map_err(|_| HostError::new("EXPERIMENT_CANCEL_WATCH_FAILED"))?;
        Ok(Self {
            stop,
            worker: Some(worker),
        })
    }
}
impl Drop for StopWatch {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn windows_root() -> Result<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    let mut path = vec![0u16; 32768];
    let length = unsafe {
        windows_sys::Win32::System::SystemInformation::GetWindowsDirectoryW(
            path.as_mut_ptr(),
            path.len() as u32,
        )
    } as usize;
    if length == 0 || length >= path.len() {
        return Err(HostError::new("EXPERIMENT_SYSTEM_PATH_FAILED"));
    }
    Ok(PathBuf::from(std::ffi::OsString::from_wide(
        &path[..length],
    )))
}
fn empty_logs() -> CaptureSnapshot {
    CaptureSnapshot {
        stdout: LogBuffer::new(0).snapshot(),
        stderr: LogBuffer::new(0).snapshot(),
    }
}
pub(crate) fn failure(code: String, elapsed_ms: u64) -> RunResult {
    RunResult {
        outputs: None,
        outcome: outcome(&code),
        exit_code: None,
        elapsed_ms,
        error: Some(code),
        user_cpu_ticks: None,
        peak_memory_bytes: None,
        final_disk_bytes: None,
        logs: empty_logs(),
    }
}
fn outcome(code: &str) -> Outcome {
    match code {
        "EXPERIMENT_CANCELLED" => Outcome::Cancelled,
        "EXTENSION_TOOL_DEADLINE_EXCEEDED"
        | "EXTENSION_RESOURCE_CPU_EXCEEDED"
        | "EXTENSION_RESOURCE_MEMORY_EXCEEDED"
        | "EXTENSION_RESOURCE_PROCESSES_EXCEEDED"
        | "EXTENSION_RESOURCE_SCRATCH_EXCEEDED"
        | "EXPERIMENT_DISK_LIMIT_EXCEEDED"
        | "EXPERIMENT_WRITE_IO_LIMIT_EXCEEDED" => Outcome::Limited,
        _ => Outcome::Failed,
    }
}
fn current_workspace(owner: &RunOwner, workspace: &WorkspaceSlot) -> Result<()> {
    let slot = workspace.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
    if slot
        .as_ref()
        .is_none_or(|ws| ws.vault_id != owner.run().record().summary.request.vault_id)
    {
        return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
    }
    Ok(())
}

/// Call inside the owned run worker. Inventory comes from the Host build and
/// resource directory; caller metadata cannot pick another executable/module.
pub(crate) fn execute(
    owner: &RunOwner,
    attempt: &Attempt,
    resource_root: &Path,
    workspace: &WorkspaceSlot,
    live: impl FnMut(LiveRun),
) -> Result<ExecutionResult> {
    let runtime = PinnedRuntime::open(resource_root)?;
    execute_pinned(owner, attempt, &runtime, workspace, live)
}
fn execute_pinned(
    owner: &RunOwner,
    attempt: &Attempt,
    runtime: &PinnedRuntime,
    workspace: &WorkspaceSlot,
    mut live: impl FnMut(LiveRun),
) -> Result<ExecutionResult> {
    let started = Instant::now();
    let limits = owner.run().record().summary.request.limits.validate()?;
    // Durable intent precedes the OS factory. A collision or unknown creation
    // result never transfers ownership of an existing profile.
    attempt.before_create()?;
    let profile = Profile::create_named(attempt.profile_name().into())?;
    let folder = profile.folder()?;
    attempt.created(
        folder
            .parent()
            .ok_or_else(|| HostError::new("EXPERIMENT_CLEANUP_JOURNAL_FAILED"))?,
        &profile.sid_bytes()?,
    )?;
    let sources = RunSources::create_for_execution(owner.run(), attempt)?;
    let runtime_entry = runtime.bind_entry()?;
    attempt.runtime_bound(
        runtime_entry
            .launch_path()
            .parent()
            .ok_or_else(|| HostError::new("EXPERIMENT_CLEANUP_JOURNAL_FAILED"))?,
    )?;
    let source_entry = sources.entry()?;
    let runtime_access = runtime.access_for_execution(&profile, attempt)?;
    let source_access = sources.access_for_execution(&profile, attempt)?;
    let data = LaunchData::new(
        runtime_entry.launch_path(),
        &[
            "-I".into(),
            "-B".into(),
            "-X".into(),
            "utf8".into(),
            source_entry
                .path()
                .to_str()
                .ok_or_else(|| HostError::new("EXPERIMENT_SOURCE_BINDING_FAILED"))?
                .into(),
        ],
        &windows_root()?,
        &folder,
        &folder.join("Temp"),
        &BTreeMap::new(),
    )?;
    let (suspended, io) =
        Suspended::create_bound_experiment_with_stdio(&profile, &runtime_entry, data, &limits)?;
    let capture = LogCapture::start(io, &limits)?;
    let permit = NativePermit::issue(
        owner,
        Bindings {
            runtime,
            sources: &sources,
            _runtime_access: &runtime_access,
            _source_access: &source_access,
            _runtime_entry: &runtime_entry,
            _source_entry: &source_entry,
        },
        workspace,
    )?;
    let running = suspended.resume_experiment(permit)?;
    let mut error = None;
    let mut exit_code = None;
    let mut next_update = Instant::now();
    loop {
        let check = running.check_authorization().and_then(|_| capture.check());
        if let Err(cause) = check {
            error = Some(cause.code);
            break;
        }
        match running.wait(Duration::from_millis(10)) {
            Ok(code) => {
                exit_code = code.or(exit_code);
            }
            Err(cause) => {
                error = Some(cause.code);
                break;
            }
        }
        match running.job().active_processes() {
            Ok(0) if exit_code.is_some() => {
                // A monitor can set its cause just before killing the Job.
                if let Err(cause) = running.check_authorization() {
                    error = Some(cause.code);
                }
                break;
            }
            Ok(_) => {}
            Err(cause) => {
                error = Some(cause.code);
                break;
            }
        }
        if Instant::now() >= next_update {
            if let Err(cause) = current_workspace(owner, workspace) {
                error = Some(cause.code);
                break;
            }
            live(LiveRun {
                operation_id: owner.run().record().summary.request.operation_id.clone(),
                elapsed_ms: started.elapsed().as_millis() as u64,
                logs: capture.snapshot()?,
            });
            next_update = Instant::now() + Duration::from_millis(100);
        }
    }
    if error.is_some() {
        let _ = running.terminate();
    }
    // Never free the slot or borrowed objects based on a cleanup timeout. An
    // unknown/failed query retains the worker and retries only its owned Job.
    let remaining_processes = loop {
        if let Ok(0) = running.job().active_processes() {
            break 0;
        }
        let _ = running.terminate();
        std::thread::sleep(Duration::from_millis(10));
    };
    exit_code = running.wait(Duration::ZERO).ok().flatten().or(exit_code);
    let user_cpu_ticks = running
        .job()
        .user_cpu_ticks()
        .ok()
        .and_then(|n| u64::try_from(n).ok());
    let peak_memory_bytes = running.job().peak_memory_bytes().ok();
    let logs = capture.finish()?;
    if error.is_none()
        && (!logs.stdout.complete
            || !logs.stderr.complete
            || logs.stdout.read_error
            || logs.stderr.read_error)
    {
        error = Some("EXPERIMENT_LOG_INCOMPLETE".into());
    }
    let final_disk_bytes = match DiskBudget::open(
        folder
            .parent()
            .ok_or_else(|| HostError::new("EXPERIMENT_DISK_INSPECTION_FAILED"))?,
        &limits,
    )
    .and_then(|disk| disk.usage())
    {
        Ok(bytes) => Some(bytes),
        Err(cause) => {
            if error.is_none() {
                error = Some(cause.code);
            }
            None
        }
    };
    if error.is_none() && exit_code != Some(0) {
        error = Some("EXPERIMENT_NONZERO_EXIT".into());
    }
    // The complete Job is zero and both streams have drained. Capture immutable
    // bytes before deleting the owned profile; never accept caller output paths.
    let (outputs, output_report) = match crate::experiment_outputs::collect(&profile, &limits) {
        Ok(outputs) => {
            let report = outputs.report();
            (Some(outputs), report)
        }
        Err(cause) => {
            if error.is_none() {
                error = Some(cause.code.clone());
            }
            (None, OutputReport::Rejected { error: cause.code })
        }
    };
    let profile_root = folder
        .parent()
        .ok_or_else(|| HostError::new("EXPERIMENT_PROFILE_CLEANUP_INCOMPLETE"))?
        .to_owned();
    let mut result = RunResult {
        outputs: Some(output_report),
        outcome: error.as_deref().map_or(Outcome::Completed, outcome),
        exit_code,
        elapsed_ms: started.elapsed().as_millis() as u64,
        error,
        user_cpu_ticks,
        peak_memory_bytes,
        final_disk_bytes,
        logs,
    };
    drop(running); // Full Job is zero; also join cancellation/deadline monitors.
    let cleanup = source_access.finish().and(runtime_access.finish());
    drop(source_entry);
    let cleanup = cleanup.and(sources.finish());
    let cleanup = cleanup.and(profile.remove());
    let resources_clean = cleanup.is_ok();
    if let Err(cause) = cleanup {
        result.outcome = Outcome::Failed;
        result.error = Some(cause.code);
    }
    let profile_removed = matches!(std::fs::symlink_metadata(&profile_root),Err(error) if error.kind()==std::io::ErrorKind::NotFound);
    let profile_registry_removed =
        crate::experiment_registry::profile_is_absent(attempt.profile_name()).unwrap_or(false);
    if !profile_removed || !profile_registry_removed {
        result.outcome = Outcome::Failed;
        result.error = Some("EXPERIMENT_PROFILE_CLEANUP_INCOMPLETE".into());
    }
    let mut cleanup_complete = false;
    if resources_clean && profile_removed && profile_registry_removed {
        match attempt.complete(CleanupProof { attempt }) {
            Ok(()) => cleanup_complete = true,
            Err(cause) => {
                result.outcome = Outcome::Failed;
                result.error = Some(cause.code);
            }
        }
    }
    Ok(ExecutionResult {
        result,
        remaining_processes,
        runtime: runtime.info().clone(),
        profile_removed,
        profile_registry_removed,
        cleanup_complete,
        outputs,
    })
}
#[cfg(test)]
pub(crate) fn execute_for_probe(
    owner: &RunOwner,
    attempt: &Attempt,
    runtime_root: &Path,
    workspace: &WorkspaceSlot,
    live: impl FnMut(LiveRun),
) -> Result<ExecutionResult> {
    let expected = std::fs::read_to_string(runtime_root.join("runtime.json"))?;
    let runtime = PinnedRuntime::pin_for_probe(runtime_root, &expected)?;
    execute_pinned(owner, attempt, &runtime, workspace, live)
}
