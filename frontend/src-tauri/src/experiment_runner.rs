//! Owned background workers and durable experiment lifecycle. No Core RPC.
use crate::{
    experiment_execution::{self, LiveRun, WorkspaceSlot},
    experiment_input::{fingerprint, PreparedInputs, RunRequest},
    experiment_owner::RunOwner,
    experiment_store::{RunRecord, RunState},
    workspace::{HostError, Result, Workspace},
};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread::JoinHandle,
    time::Instant,
};
struct Worker {
    vault: String,
    operation: String,
    cancelled: Arc<AtomicBool>,
    workspace: WorkspaceSlot,
    handle: JoinHandle<Result<RunRecord>>,
}
#[derive(Default)]
pub struct Runner {
    worker: Mutex<Option<Worker>>,
    live: Arc<Mutex<Option<LiveRun>>>,
    shutdown: AtomicBool,
    #[cfg(test)]
    stopped: Arc<Mutex<Vec<experiment_execution::ExecutionResult>>>,
}
enum Runtime {
    Bundled(PathBuf),
    #[cfg(test)]
    Probe(PathBuf),
}
fn busy() -> HostError {
    HostError::new("HOST_BUSY")
}
fn with_workspace<T>(
    slot: &WorkspaceSlot,
    f: impl FnOnce(&mut Workspace) -> Result<T>,
) -> Result<T> {
    let mut guard = slot.lock().map_err(|_| busy())?;
    f(guard
        .as_mut()
        .ok_or_else(|| HostError::new("VAULT_NOT_OPEN"))?)
}
impl Runner {
    /// Retries find the existing operation before touching possibly moved or
    /// edited source files. New metadata cannot replace the existing proposal.
    pub fn prepare(&self, workspace: &WorkspaceSlot, request: &RunRequest) -> Result<RunRecord> {
        let validated = request.validate()?;
        with_workspace(workspace, |ws| {
            if request.vault_id != ws.vault_id {
                return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
            }
            if let Some(old) = ws.experiment_record(&request.operation_id)? {
                return if old.summary.fingerprint == fingerprint(validated.request())? {
                    Ok(old)
                } else {
                    Err(HostError::new("EXPERIMENT_OPERATION_MISMATCH"))
                };
            }
            let inputs = PreparedInputs::prepare(ws, &validated)?;
            ws.experiment_prepare(inputs)
        })
    }
    /// Host's trusted user-confirmation route only. Never export approval as a
    /// Core/model tool or interpret file-write consent as execution approval.
    pub fn confirm_from_user(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        fingerprint: &str,
    ) -> Result<RunRecord> {
        with_workspace(workspace, |ws| {
            ws.experiment_approve(operation, fingerprint)
        })
    }
    pub fn record(&self, workspace: &WorkspaceSlot, operation: &str) -> Result<Option<RunRecord>> {
        with_workspace(workspace, |ws| ws.experiment_record(operation))
    }
    pub fn reject(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        fingerprint: &str,
    ) -> Result<RunRecord> {
        with_workspace(workspace, |ws| ws.experiment_reject(operation, fingerprint))
    }
    pub fn cancel(&self, workspace: &WorkspaceSlot, operation: &str) -> Result<RunRecord> {
        with_workspace(workspace, |ws| RunOwner::request_cancel(ws, operation))
    }
    pub fn live(&self, vault: &str, operation: &str) -> Result<Option<LiveRun>> {
        let worker = self.worker.lock().map_err(|_| busy())?;
        if worker
            .as_ref()
            .is_none_or(|worker| worker.vault != vault || worker.operation != operation)
        {
            return Ok(None);
        }
        Ok(self.live.lock().map_err(|_| busy())?.clone())
    }
    pub fn start_approved(
        &self,
        workspace: WorkspaceSlot,
        resource_root: PathBuf,
        operation: &str,
    ) -> Result<RunRecord> {
        self.start(workspace, Runtime::Bundled(resource_root), operation)
    }
    fn start(
        &self,
        workspace: WorkspaceSlot,
        runtime: Runtime,
        operation: &str,
    ) -> Result<RunRecord> {
        let mut worker = self.worker.lock().map_err(|_| busy())?;
        if self.shutdown.load(Ordering::Acquire) {
            return Err(HostError::new("EXPERIMENT_HOST_CLOSING"));
        }
        if worker
            .as_ref()
            .is_some_and(|worker| !worker.handle.is_finished())
        {
            return Err(HostError::new("EXPERIMENT_RUN_BUSY"));
        }
        if let Some(previous) = worker.take() {
            let _ = previous.handle.join();
        }
        let owner = Arc::new(with_workspace(&workspace, |ws| {
            RunOwner::claim(ws, operation)
        })?);
        let record = owner.run().record().clone();
        let started = Instant::now();
        let child_owner = Arc::clone(&owner);
        let child_workspace = Arc::clone(&workspace);
        let live = Arc::clone(&self.live);
        #[cfg(test)]
        let stopped = Arc::clone(&self.stopped);
        let handle = std::thread::Builder::new()
            .name("experiment-run".into())
            .spawn(move || {
                // Unwind drops the native process first; its Drop proves the whole
                // Job empty before freeing borrowed profile/runtime/source grants.
                let executed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    let emit = |snapshot| {
                        if let Ok(mut latest) = live.lock() {
                            *latest = Some(snapshot);
                        }
                    };
                    match runtime {
                        Runtime::Bundled(root) => experiment_execution::execute(
                            &child_owner,
                            &root,
                            &child_workspace,
                            emit,
                        ),
                        #[cfg(test)]
                        Runtime::Probe(root) => experiment_execution::execute_for_probe(
                            &child_owner,
                            &root,
                            &child_workspace,
                            emit,
                        ),
                    }
                }));
                let result = match executed {
                    Ok(Ok(execution)) => {
                        #[cfg(test)]
                        {
                            let result = execution.result.clone();
                            stopped.lock().unwrap().push(execution);
                            result
                        }
                        #[cfg(not(test))]
                        {
                            execution.result
                        }
                    }
                    Ok(Err(error)) => experiment_execution::failure(
                        error.code,
                        started.elapsed().as_millis() as u64,
                    ),
                    Err(_) => experiment_execution::failure(
                        "EXPERIMENT_WORKER_FAILED".into(),
                        started.elapsed().as_millis() as u64,
                    ),
                };
                let finished = with_workspace(&child_workspace, |ws| {
                    let old = ws
                        .experiment_record(
                            &child_owner.run().record().summary.request.operation_id,
                        )?
                        .ok_or_else(|| HostError::new("EXPERIMENT_STATE_CHANGED"))?;
                    if old.summary.fingerprint != child_owner.run().record().summary.fingerprint {
                        return Err(HostError::new("EXPERIMENT_STATE_CHANGED"));
                    }
                    // Revalidation may have failed/expired before any resume. Keep
                    // that durable state; do not overwrite a newer approval lease.
                    if old.approval_id != child_owner.run().record().approval_id
                        || !matches!(
                            old.state,
                            RunState::Starting | RunState::Running | RunState::CancelRequested
                        )
                    {
                        return Ok(old);
                    }
                    child_owner.finish(ws, result)
                });
                if let Ok(mut latest) = live.lock() {
                    *latest = None;
                }
                // The last owner releases the global slot only after all native,
                // ACL/temp and durable result cleanup above has completed.
                finished
            });
        let handle = match handle {
            Ok(handle) => handle,
            Err(_) => {
                let result = experiment_execution::failure(
                    "EXPERIMENT_WORKER_START_FAILED".into(),
                    started.elapsed().as_millis() as u64,
                );
                let _ = with_workspace(&workspace, |ws| owner.finish(ws, result));
                return Err(HostError::new("EXPERIMENT_WORKER_START_FAILED"));
            }
        };
        *worker = Some(Worker {
            vault: record.summary.request.vault_id.clone(),
            operation: operation.into(),
            cancelled: owner.cancellation_signal(),
            workspace,
            handle,
        });
        Ok(record)
    }
    fn stop_worker(worker: &mut Option<Worker>) -> Result<()> {
        let Some(owned) = worker.take() else {
            return Ok(());
        };
        // Persist first, then signal. On lost/poisoned workspace, still stop only
        // our worker; never write this operation into a replacement vault.
        let _ = with_workspace(&owned.workspace, |ws| {
            if ws.vault_id != owned.vault {
                return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
            }
            ws.experiment_cancel(&owned.operation)
        });
        owned.cancelled.store(true, Ordering::Release);
        owned
            .handle
            .join()
            .map_err(|_| HostError::new("EXPERIMENT_WORKER_FAILED"))??;
        Ok(())
    }
    /// Serialize switch/close with start, and join without holding the workspace
    /// mutex. Callers must enter here before taking/changing workspace state.
    pub fn transition<T>(
        &self,
        workspace: &WorkspaceSlot,
        change: impl FnOnce(&mut Option<Workspace>) -> Result<T>,
    ) -> Result<T> {
        self.transition_if(workspace, |_| Ok(None), change)
    }
    /// Check a no-op while start and workspace changes are serialized. Opening
    /// the already active vault must not cancel its experiment.
    pub fn transition_if<T>(
        &self,
        workspace: &WorkspaceSlot,
        unchanged: impl FnOnce(&Option<Workspace>) -> Result<Option<T>>,
        change: impl FnOnce(&mut Option<Workspace>) -> Result<T>,
    ) -> Result<T> {
        let mut worker = self.worker.lock().map_err(|_| busy())?;
        {
            let slot = workspace.lock().map_err(|_| busy())?;
            if let Some(value) = unchanged(&slot)? {
                return Ok(value);
            }
        }
        Self::stop_worker(&mut worker)?;
        let mut slot = workspace.lock().map_err(|_| busy())?;
        change(&mut slot)
    }
    pub fn shutdown(&self) -> Result<()> {
        self.shutdown.store(true, Ordering::Release);
        let mut worker = self.worker.lock().map_err(|_| busy())?;
        Self::stop_worker(&mut worker)
    }
}
impl Drop for Runner {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::Release);
        let worker = self
            .worker
            .get_mut()
            .unwrap_or_else(|error| error.into_inner());
        let _ = Self::stop_worker(worker);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_input::SelectedFile, experiment_policy::ExecutionLimits,
        experiment_runtime::RUNTIME_ID, experiment_store::Outcome,
    };
    use std::time::Duration;
    fn fixture(
        script: &str,
        limits: ExecutionLimits,
    ) -> (tempfile::TempDir, WorkspaceSlot, RunRequest) {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(root.path().join("experiments/main.py"), script).unwrap();
        std::fs::write(
            root.path().join("experiments/input.csv"),
            "name,value\r\n中文,7\r\nexample,11\r\n",
        )
        .unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let mut select = |path: &str| {
            let file = ws.read(path).unwrap().entry;
            SelectedFile {
                file_id: file.file_id,
                path: file.path,
                revision: file.revision,
                hash: file.hash,
            }
        };
        let entry = select("experiments/main.py");
        let input = select("experiments/input.csv");
        let request = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry,
            inputs: vec![input],
            limits,
        };
        (root, Arc::new(Mutex::new(Some(ws))), request)
    }
    fn wait(runner: &Runner, workspace: &WorkspaceSlot, operation: &str) -> RunRecord {
        let until = Instant::now() + Duration::from_secs(80);
        loop {
            let record = runner.record(workspace, operation).unwrap().unwrap();
            if matches!(
                record.state,
                RunState::Completed | RunState::Failed | RunState::Limited | RunState::Cancelled
            ) {
                return record;
            }
            assert!(
                Instant::now() < until,
                "worker failed to stop: {:?}",
                record.state
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    #[test]
    fn retries_find_existing_proposal_before_reading_moved_sources() {
        let runner = Runner::default();
        let (root, workspace, request) = fixture("print(1)", ExecutionLimits::default());
        let waiting = runner.prepare(&workspace, &request).unwrap();
        std::fs::rename(
            root.path().join("experiments/main.py"),
            root.path().join("experiments/moved.py"),
        )
        .unwrap();
        assert_eq!(
            runner
                .prepare(&workspace, &request)
                .unwrap()
                .summary
                .fingerprint,
            waiting.summary.fingerprint
        );
        let mut changed = request.clone();
        changed.limits.wall_seconds = 90;
        assert_eq!(
            runner.prepare(&workspace, &changed).unwrap_err().code,
            "EXPERIMENT_OPERATION_MISMATCH"
        );
        assert_eq!(
            runner
                .confirm_from_user(
                    &workspace,
                    &request.operation_id,
                    &waiting.summary.fingerprint
                )
                .unwrap_err()
                .code,
            "EXPERIMENT_INPUT_CHANGED"
        );
        assert_eq!(
            runner
                .record(&workspace, &request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::Failed
        );
    }
    #[test]
    fn no_approval_no_launch_and_missing_bundle_records_real_failure() {
        let _serial = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
        let runner = Runner::default();
        let (_root, workspace, request) = fixture("print(1)", ExecutionLimits::default());
        let waiting = runner.prepare(&workspace, &request).unwrap();
        assert!(runner
            .start_approved(
                workspace.clone(),
                PathBuf::from("Z:/missing-runtime"),
                &request.operation_id
            )
            .is_err());
        assert_eq!(
            runner
                .record(&workspace, &request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::AwaitingConfirmation
        );
        runner
            .confirm_from_user(
                &workspace,
                &request.operation_id,
                &waiting.summary.fingerprint,
            )
            .unwrap();
        runner
            .start_approved(
                workspace.clone(),
                PathBuf::from("Z:/missing-runtime"),
                &request.operation_id,
            )
            .unwrap();
        let failed = wait(&runner, &workspace, &request.operation_id);
        let result = failed.result.unwrap();
        assert_eq!(result.outcome, Outcome::Failed);
        assert!(result.error.unwrap().starts_with("EXPERIMENT_RUNTIME_"));
        assert_eq!(result.exit_code, None);
        assert_eq!(result.user_cpu_ticks, None);
        assert_eq!(result.peak_memory_bytes, None);
        assert!(runner
            .start_approved(
                workspace.clone(),
                PathBuf::from("Z:/missing-runtime"),
                &request.operation_id
            )
            .is_err());
        runner.shutdown().unwrap();
        assert_eq!(
            runner
                .start_approved(
                    workspace,
                    PathBuf::from("Z:/missing-runtime"),
                    &request.operation_id
                )
                .unwrap_err()
                .code,
            "EXPERIMENT_HOST_CLOSING"
        );
    }
    pub(super) fn native() {
        let _serial = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        let runtime = std::env::var_os("OPENNEXUS_PROBE_RUNTIME_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(".build/experiment-runtime").join(RUNTIME_ID));
        let mut reports = std::collections::BTreeMap::new();
        for mode in [
            "basic", "nonzero", "logs", "cancel", "switch", "shutdown", "wall", "cpu",
        ] {
            let script = include_str!("../../../scripts/fixtures/experiment_worker_probe.py")
                .replace("MODE = None", &format!("MODE = {mode:?}"));
            let limits = ExecutionLimits {
                wall_seconds: if mode == "wall" {
                    1
                } else if mode == "cpu" {
                    60
                } else {
                    8
                },
                cpu_seconds: if mode == "wall" || mode == "cpu" {
                    1
                } else {
                    2
                },
                disk_mib: 8,
                output_mib: 1,
                log_kib: 16,
                processes: 4,
                ..Default::default()
            };
            let (vault, workspace, request) = fixture(&script, limits);
            let runner = Runner::default();
            let waiting = runner.prepare(&workspace, &request).unwrap();
            runner
                .confirm_from_user(
                    &workspace,
                    &request.operation_id,
                    &waiting.summary.fingerprint,
                )
                .unwrap();
            runner
                .start(
                    workspace.clone(),
                    Runtime::Probe(runtime.clone()),
                    &request.operation_id,
                )
                .unwrap();
            if mode == "cancel" || mode == "switch" || mode == "shutdown" {
                let until = Instant::now() + Duration::from_secs(5);
                loop {
                    if runner
                        .live(&request.vault_id, &request.operation_id)
                        .unwrap()
                        .is_some_and(|live| live.logs.stdout.text.contains("child-ready"))
                    {
                        break;
                    }
                    assert!(
                        Instant::now() < until,
                        "child did not actually start: {mode}"
                    );
                    std::thread::sleep(Duration::from_millis(10));
                }
                assert_eq!(
                    runner
                        .start(
                            workspace.clone(),
                            Runtime::Probe(runtime.clone()),
                            &request.operation_id
                        )
                        .unwrap_err()
                        .code,
                    "EXPERIMENT_RUN_BUSY"
                );
                assert!(runner
                    .live("wrong-vault", &request.operation_id)
                    .unwrap()
                    .is_none());
                let current = runner
                    .transition_if(
                        &workspace,
                        |slot| Ok(slot.as_ref().map(|ws| ws.vault_id.clone())),
                        |_| panic!("opening the current vault changed workspace"),
                    )
                    .unwrap();
                assert_eq!(current, request.vault_id);
                assert_eq!(
                    runner
                        .record(&workspace, &request.operation_id)
                        .unwrap()
                        .unwrap()
                        .state,
                    RunState::Running
                );
            }
            let record = if mode == "switch" {
                let began = Instant::now();
                let record = runner
                    .transition(&workspace, |slot| {
                        let old = slot
                            .as_ref()
                            .unwrap()
                            .experiment_record(&request.operation_id)?
                            .unwrap();
                        assert_eq!(old.state, RunState::Cancelled);
                        *slot = None;
                        Ok(old)
                    })
                    .unwrap();
                assert!(
                    began.elapsed() < Duration::from_secs(3),
                    "join held workspace lock"
                );
                record
            } else {
                if mode == "cancel" {
                    runner.cancel(&workspace, &request.operation_id).unwrap();
                } else if mode == "shutdown" {
                    runner.shutdown().unwrap();
                }
                wait(&runner, &workspace, &request.operation_id)
            };
            let result = record.result.unwrap();
            match mode {
                "basic" | "logs" => {
                    assert_eq!(result.outcome, Outcome::Completed);
                    assert_eq!(result.exit_code, Some(0));
                }
                "nonzero" => {
                    assert_eq!(result.outcome, Outcome::Failed);
                    assert_eq!(result.exit_code, Some(7));
                }
                "cancel" | "switch" | "shutdown" => assert_eq!(result.outcome, Outcome::Cancelled),
                "wall" | "cpu" => assert_eq!(result.outcome, Outcome::Limited),
                _ => unreachable!(),
            }
            assert!(result.peak_memory_bytes.unwrap() > 0);
            assert!(result.final_disk_bytes.is_some());
            assert!(result.logs.stdout.complete && result.logs.stderr.complete);
            if mode == "basic" {
                assert!(result
                    .logs
                    .stdout
                    .text
                    .contains("NATIVE_WORKER:中文:18:3.13.16"));
                let report: serde_json::Value = serde_json::from_str(
                    result
                        .logs
                        .stdout
                        .text
                        .lines()
                        .find_map(|line| line.strip_prefix("WORKER_REPORT:"))
                        .unwrap(),
                )
                .unwrap();
                let acl = &report["scratch_acl_access"];
                for field in [
                    "root",
                    "parent",
                    "created_file",
                    "root_acl_change",
                    "file_acl_change",
                    "outside_create",
                    "explicit_descriptor_acl_access",
                ] {
                    assert_eq!(acl[field], 5, "{field}: {acl}");
                }
                assert_eq!(acl["cwd_is_scratch"], true);
                assert_eq!(acl["scratch_read_write_delete"], true);
                // An explicit protected descriptor can grant rights on a NEW
                // child; it cannot replace the pinned parent's descriptor.
                assert_eq!(acl["protected_descriptor_acl_access"], 0);
            }
            if mode == "cpu" {
                assert!(result.user_cpu_ticks.unwrap() >= 10_000_000);
            }
            if mode == "logs" {
                assert_eq!(result.logs.stdout.bytes_seen, 2 * 1024 * 1024);
                assert!(result.logs.stderr.bytes_seen >= 2 * 1024 * 1024);
                assert!(result.logs.stdout.truncated && result.logs.stderr.truncated);
            }
            assert_eq!(
                std::fs::read_to_string(vault.path().join("experiments/main.py")).unwrap(),
                script
            );
            if mode != "switch" {
                let until = Instant::now() + Duration::from_secs(3);
                while runner
                    .worker
                    .lock()
                    .unwrap()
                    .as_ref()
                    .is_some_and(|worker| !worker.handle.is_finished())
                {
                    assert!(Instant::now() < until, "terminal worker did not return");
                    std::thread::sleep(Duration::from_millis(10));
                }
                assert_eq!(
                    runner
                        .start(
                            workspace.clone(),
                            Runtime::Probe(runtime.clone()),
                            &request.operation_id
                        )
                        .unwrap_err()
                        .code,
                    if mode == "shutdown" {
                        "EXPERIMENT_HOST_CLOSING"
                    } else {
                        "EXPERIMENT_STATE_CHANGED"
                    }
                );
                runner.shutdown().unwrap();
            }
            let stopped = runner.stopped.lock().unwrap();
            assert_eq!(stopped.len(), 1);
            assert_eq!(stopped[0].remaining_processes, 0);
            reports.insert(mode, serde_json::to_value(&stopped[0]).unwrap());
        }
        let evidence = std::env::var_os("OPENNEXUS_PROBE_RECEIPT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(".build/experiment-runtime"));
        std::fs::write(evidence.join("worker-runtime-result.json"),serde_json::to_vec_pretty(&serde_json::json!({"schema_version":1,"runtime_id":RUNTIME_ID,"runs":reports,"production_ui_enabled":false})).unwrap()).unwrap();
    }
}
#[cfg(test)]
pub(crate) fn native_worker_probe() {
    tests::native();
}
