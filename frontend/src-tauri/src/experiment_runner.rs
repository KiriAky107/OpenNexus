//! Owned background workers and durable experiment lifecycle. Native user
//! consent is consumed through a vault-bound Host entry point.
use crate::{
    experiment_cleanup::{CleanupStatus, Journal},
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
    cleanup: std::sync::OnceLock<std::result::Result<Arc<Journal>, String>>,
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
fn with_vault<T>(
    slot: &WorkspaceSlot,
    vault: &str,
    f: impl FnOnce(&mut Workspace) -> Result<T>,
) -> Result<T> {
    with_workspace(slot, |ws| {
        if ws.vault_id != vault {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        f(ws)
    })
}
impl Runner {
    /// Fixed Host default storage, initialized once. Relocating vaults or the
    /// configurable data root cannot bypass an existing cleanup obligation.
    pub fn initialize_cleanup(&self, host_root: &std::path::Path) -> Result<()> {
        let _initialization = self.worker.lock().map_err(|_| busy())?;
        if self.cleanup.get().is_some() {
            return Err(HostError::new("HOST_ALREADY_INITIALIZED"));
        }
        let journal = Journal::open(host_root).map_err(|e| e.code);
        let error = journal.as_ref().err().cloned();
        self.cleanup
            .set(journal)
            .map_err(|_| HostError::new("HOST_ALREADY_INITIALIZED"))?;
        match error {
            Some(code) => Err(HostError::new(&code)),
            None => Ok(()),
        }
    }
    fn cleanup_journal(&self) -> Result<&Arc<Journal>> {
        self.cleanup
            .get()
            .ok_or_else(|| HostError::new("EXPERIMENT_HOST_NOT_READY"))?
            .as_ref()
            .map_err(|code| HostError::new(code))
    }
    pub fn cleanup_status(&self) -> Result<Option<CleanupStatus>> {
        self.cleanup_journal()?.status()
    }
    pub fn cleanup_review(&self) -> Result<Option<crate::experiment_cleanup::CleanupReview>> {
        self.cleanup_journal()?.review()
    }
    /// Called only after the main window obtains a separate native user
    /// confirmation. The OS lease and scope fingerprint are rechecked here.
    pub fn recover_cleanup(&self, fingerprint: &str) -> Result<()> {
        self.cleanup_journal()?.recover(fingerprint)
    }
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
    pub fn confirm_from_user_for_vault(
        &self,
        workspace: &WorkspaceSlot,
        vault: &str,
        operation: &str,
        fingerprint: &str,
    ) -> Result<RunRecord> {
        with_vault(workspace, vault, |ws| {
            ws.experiment_approve(operation, fingerprint)
        })
    }
    pub fn record(&self, workspace: &WorkspaceSlot, operation: &str) -> Result<Option<RunRecord>> {
        with_workspace(workspace, |ws| ws.experiment_record(operation))
    }
    pub fn history(
        &self,
        workspace: &WorkspaceSlot,
        limit: usize,
        cursor: Option<&crate::experiment_store::HistoryCursor>,
    ) -> Result<crate::experiment_store::HistoryPage> {
        with_workspace(workspace, |ws| ws.experiment_history(limit, cursor))
    }
    pub fn retention_usage(
        &self,
        workspace: &WorkspaceSlot,
    ) -> Result<crate::experiment_store::RetentionUsage> {
        with_workspace(workspace, |ws| ws.experiment_retention_usage())
    }
    pub fn source_preview(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        path: &str,
    ) -> Result<crate::experiment_preview::SourcePreview> {
        with_workspace(workspace, |ws| {
            ws.experiment_source_preview(operation, path)
        })
    }
    pub fn source_read(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        path: &str,
        offset: usize,
        limit: usize,
    ) -> Result<crate::experiment_preview::SourceChunk> {
        with_workspace(workspace, |ws| {
            ws.experiment_source_read(operation, path, offset, limit)
        })
    }
    pub fn output_preview(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        path: &str,
    ) -> Result<crate::experiment_preview::OutputPreview> {
        with_workspace(workspace, |ws| {
            ws.experiment_output_preview(operation, path)
        })
    }
    pub fn output_read(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        path: &str,
        offset: usize,
        limit: usize,
    ) -> Result<crate::experiment_outputs::OutputChunk> {
        with_workspace(workspace, |ws| {
            ws.experiment_output_read(operation, path, offset, limit)
        })
    }
    pub fn import_prepare(
        &self,
        workspace: &WorkspaceSlot,
        request: &crate::experiment_import::ImportRequest,
    ) -> Result<crate::experiment_import::ImportRecord> {
        with_workspace(workspace, |ws| ws.experiment_import_prepare(request))
    }
    pub fn import_record(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
    ) -> Result<Option<crate::experiment_import::ImportRecord>> {
        with_workspace(workspace, |ws| ws.experiment_import_record(operation))
    }
    /// Trusted user review, independent of the experiment's execution consent.
    pub fn confirm_import_from_user(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        fingerprint: &str,
    ) -> Result<crate::experiment_import::ImportRecord> {
        with_workspace(workspace, |ws| {
            ws.experiment_import_approve(operation, fingerprint)
        })
    }
    pub fn import_next(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
    ) -> Result<crate::experiment_import::ImportRecord> {
        with_workspace(workspace, |ws| ws.experiment_import_next(operation))
    }
    pub fn confirm_import_from_user_for_vault(
        &self,
        workspace: &WorkspaceSlot,
        vault: &str,
        operation: &str,
        fingerprint: &str,
    ) -> Result<crate::experiment_import::ImportRecord> {
        with_vault(workspace, vault, |ws| {
            ws.experiment_import_approve(operation, fingerprint)
        })
    }
    pub fn import_next_for_vault(
        &self,
        workspace: &WorkspaceSlot,
        vault: &str,
        operation: &str,
    ) -> Result<crate::experiment_import::ImportRecord> {
        with_vault(workspace, vault, |ws| ws.experiment_import_next(operation))
    }
    pub fn import_cancel(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        fingerprint: &str,
    ) -> Result<crate::experiment_import::ImportRecord> {
        with_workspace(workspace, |ws| {
            ws.experiment_import_cancel(operation, fingerprint)
        })
    }
    pub fn artifact_origins(
        &self,
        workspace: &WorkspaceSlot,
        file_id: &str,
        limit: usize,
        cursor: Option<&crate::experiment_store::HistoryCursor>,
    ) -> Result<crate::experiment_import::OriginPage> {
        with_workspace(workspace, |ws| {
            ws.experiment_artifact_origins(file_id, limit, cursor)
        })
    }
    pub fn forget_import_from_user(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        fingerprint: &str,
    ) -> Result<()> {
        with_workspace(workspace, |ws| {
            ws.experiment_import_forget(operation, fingerprint)
        })
    }
    /// Trusted user action only; model retries may read history but cannot
    /// discard pending cleanup diagnostics or a worker still returning.
    pub fn forget_from_user(
        &self,
        workspace: &WorkspaceSlot,
        operation: &str,
        fingerprint: &str,
    ) -> Result<crate::experiment_store::ForgetReceipt> {
        self.forget_in_vault(workspace, None, operation, fingerprint)
    }
    pub fn forget_from_user_for_vault(
        &self,
        workspace: &WorkspaceSlot,
        vault: &str,
        operation: &str,
        fingerprint: &str,
    ) -> Result<crate::experiment_store::ForgetReceipt> {
        self.forget_in_vault(workspace, Some(vault), operation, fingerprint)
    }
    fn forget_in_vault(
        &self,
        workspace: &WorkspaceSlot,
        vault: Option<&str>,
        operation: &str,
        fingerprint: &str,
    ) -> Result<crate::experiment_store::ForgetReceipt> {
        let worker = self.worker.lock().map_err(|_| busy())?;
        let cleanup = self.cleanup_status()?;
        with_workspace(workspace, |ws| {
            if vault.is_some_and(|vault| vault != ws.vault_id) {
                return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
            }
            if cleanup
                .as_ref()
                .is_some_and(|p| p.vault_id == ws.vault_id && p.operation_id == operation)
            {
                return Err(HostError::new("EXPERIMENT_CLEANUP_REQUIRED"));
            }
            if worker.as_ref().is_some_and(|w| {
                w.vault == ws.vault_id && w.operation == operation && !w.handle.is_finished()
            }) {
                return Err(HostError::new("EXPERIMENT_RUN_BUSY"));
            }
            ws.experiment_forget(operation, fingerprint)
        })
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
    #[cfg(test)]
    pub(crate) fn start_approved_for_probe(
        &self,
        workspace: WorkspaceSlot,
        runtime_root: PathBuf,
        operation: &str,
    ) -> Result<RunRecord> {
        self.start(workspace, Runtime::Probe(runtime_root), operation)
    }
    pub fn start_approved_for_vault(
        &self,
        workspace: WorkspaceSlot,
        vault: &str,
        resource_root: PathBuf,
        operation: &str,
    ) -> Result<RunRecord> {
        self.start_in_vault(
            workspace,
            Some(vault),
            Runtime::Bundled(resource_root),
            operation,
        )
    }
    fn start(
        &self,
        workspace: WorkspaceSlot,
        runtime: Runtime,
        operation: &str,
    ) -> Result<RunRecord> {
        self.start_in_vault(workspace, None, runtime, operation)
    }
    fn start_in_vault(
        &self,
        workspace: WorkspaceSlot,
        vault: Option<&str>,
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
        let journal = self.cleanup_journal()?;
        let (owner, attempt) = with_workspace(&workspace, |ws| {
            if vault.is_some_and(|vault| vault != ws.vault_id) {
                return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
            }
            // Gate before consuming user approval. A different vault or new
            // Runner cannot discard the previous Host's durable obligation.
            let attempt = journal.reserve(&ws.vault_id, operation)?;
            match RunOwner::claim(ws, operation) {
                Ok(owner) => Ok((Arc::new(owner), attempt)),
                Err(error) => {
                    attempt.release_unstarted()?;
                    Err(error)
                }
            }
        })?;
        let record = owner.run().record().clone();
        let started = Instant::now();
        let child_owner = Arc::clone(&owner);
        let child_attempt = Arc::clone(&attempt);
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
                            &child_attempt,
                            &root,
                            &child_workspace,
                            emit,
                        ),
                        #[cfg(test)]
                        Runtime::Probe(root) => experiment_execution::execute_for_probe(
                            &child_owner,
                            &child_attempt,
                            &root,
                            &child_workspace,
                            emit,
                        ),
                    }
                }));
                let (mut result, outputs) = match executed {
                    Ok(Ok(mut execution)) => {
                        let outputs = execution.outputs.take();
                        #[cfg(test)]
                        {
                            let result = execution.result.clone();
                            stopped.lock().unwrap().push(execution);
                            (result, outputs)
                        }
                        #[cfg(not(test))]
                        {
                            (execution.result, outputs)
                        }
                    }
                    Ok(Err(error)) => (
                        experiment_execution::failure(
                            error.code,
                            started.elapsed().as_millis() as u64,
                        ),
                        None,
                    ),
                    Err(_) => (
                        experiment_execution::failure(
                            "EXPERIMENT_WORKER_FAILED".into(),
                            started.elapsed().as_millis() as u64,
                        ),
                        None,
                    ),
                };
                // Preflight failures are known not to have entered the native
                // factory. All later failures/unwinds keep the durable gate.
                if let Err(cause) = child_attempt.release_unstarted() {
                    result.outcome = crate::experiment_store::Outcome::Failed;
                    result.error = Some(cause.code);
                }
                if let Some(code) = &result.error {
                    let _ = child_attempt.note_error(code);
                }
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
                    child_owner.finish_outputs(ws, result, outputs.as_ref())
                });
                if let Ok(mut latest) = live.lock() {
                    *latest = None;
                }
                // Release after the native Job has stopped and persistence was
                // attempted. Unproven resource cleanup retains the durable gate.
                finished
            });
        let handle = match handle {
            Ok(handle) => handle,
            Err(_) => {
                let code = attempt
                    .release_unstarted()
                    .err()
                    .map_or_else(|| "EXPERIMENT_WORKER_START_FAILED".to_owned(), |e| e.code);
                let result = experiment_execution::failure(
                    code.clone(),
                    started.elapsed().as_millis() as u64,
                );
                let _ = with_workspace(&workspace, |ws| owner.finish(ws, result));
                return Err(HostError::new(&code));
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
    fn delayed_user_decisions_never_approve_start_or_forget_a_replacement_vault() {
        let runner = Runner::default();
        let host_root = tempfile::tempdir().unwrap();
        runner.initialize_cleanup(host_root.path()).unwrap();
        let (_root, slot, request) = fixture("print(1)", ExecutionLimits::default());
        let original = runner.prepare(&slot, &request).unwrap();
        let (_other_root, other, mut other_request) =
            fixture("print(1)", ExecutionLimits::default());
        other_request.operation_id = request.operation_id.clone();
        let replacement = runner.prepare(&other, &other_request).unwrap();
        let previous = slot
            .lock()
            .unwrap()
            .replace(other.lock().unwrap().take().unwrap());
        for result in [
            runner
                .confirm_from_user_for_vault(
                    &slot,
                    &request.vault_id,
                    &request.operation_id,
                    &original.summary.fingerprint,
                )
                .map(|_| ()),
            runner
                .start_approved_for_vault(
                    slot.clone(),
                    &request.vault_id,
                    host_root.path().to_owned(),
                    &request.operation_id,
                )
                .map(|_| ()),
            runner
                .forget_from_user_for_vault(
                    &slot,
                    &request.vault_id,
                    &request.operation_id,
                    &original.summary.fingerprint,
                )
                .map(|_| ()),
            runner
                .confirm_import_from_user_for_vault(
                    &slot,
                    &request.vault_id,
                    &request.operation_id,
                    &original.summary.fingerprint,
                )
                .map(|_| ()),
            runner
                .import_next_for_vault(&slot, &request.vault_id, &request.operation_id)
                .map(|_| ()),
        ] {
            assert_eq!(result.unwrap_err().code, "VAULT_PERMISSION_CHANGED");
        }
        assert_eq!(
            runner
                .record(&slot, &request.operation_id)
                .unwrap()
                .unwrap()
                .summary
                .fingerprint,
            replacement.summary.fingerprint
        );
        assert_eq!(
            runner
                .record(&slot, &request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::AwaitingConfirmation
        );
        assert!(runner.cleanup_status().unwrap().is_none());
        *slot.lock().unwrap() = previous;
        assert_eq!(
            runner
                .record(&slot, &request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::AwaitingConfirmation
        );
    }
    #[test]
    fn no_approval_no_launch_and_missing_bundle_records_real_failure() {
        let _serial = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
        let runner = Runner::default();
        let (_root, workspace, request) = fixture("print(1)", ExecutionLimits::default());
        let host_root = tempfile::tempdir().unwrap();
        runner.initialize_cleanup(host_root.path()).unwrap();
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
        drop(runner);
        host_root.close().unwrap();
    }
    #[test]
    fn pending_cleanup_survives_restart_and_vault_switch_without_consuming_approval() {
        let _serial = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
        let host_root = tempfile::tempdir().unwrap();
        let alternate = host_root.path().join("unselected-host-root");
        let runner = Runner::default();
        runner.initialize_cleanup(host_root.path()).unwrap();
        let (v, o) = (
            uuid::Uuid::new_v4().to_string(),
            uuid::Uuid::new_v4().to_string(),
        );
        let attempt = runner.cleanup_journal().unwrap().reserve(&v, &o).unwrap();
        attempt.before_create().unwrap();
        drop(attempt);
        drop(runner);
        let runner = Runner::default();
        runner.initialize_cleanup(host_root.path()).unwrap();
        assert_eq!(
            runner.initialize_cleanup(&alternate).unwrap_err().code,
            "HOST_ALREADY_INITIALIZED"
        );
        assert!(!alternate.exists());
        let (_vault, workspace, request) = fixture("print(1)", ExecutionLimits::default());
        let waiting = runner.prepare(&workspace, &request).unwrap();
        let approved = runner
            .confirm_from_user(
                &workspace,
                &request.operation_id,
                &waiting.summary.fingerprint,
            )
            .unwrap();
        assert_eq!(
            runner
                .start_approved(
                    workspace.clone(),
                    PathBuf::from("Z:/missing-runtime"),
                    &request.operation_id
                )
                .unwrap_err()
                .code,
            "EXPERIMENT_CLEANUP_REQUIRED"
        );
        let unchanged = runner
            .record(&workspace, &request.operation_id)
            .unwrap()
            .unwrap();
        assert_eq!(unchanged.state, RunState::Approved);
        assert_eq!(unchanged.approval_id, approved.approval_id);
        let pending = runner.cleanup_status().unwrap().unwrap();
        assert_eq!(pending.vault_id, v);
        assert_eq!(pending.operation_id, o);
        assert_eq!(
            pending.phase,
            crate::experiment_cleanup::CleanupPhase::Creating
        );
    }
    #[test]
    fn forgotten_retry_does_not_read_moved_sources_or_create_another_proposal() {
        let host_root = tempfile::tempdir().unwrap();
        let runner = Runner::default();
        runner.initialize_cleanup(host_root.path()).unwrap();
        let (vault, workspace, request) = fixture("print(1)", ExecutionLimits::default());
        let waiting = runner.prepare(&workspace, &request).unwrap();
        runner
            .reject(
                &workspace,
                &request.operation_id,
                &waiting.summary.fingerprint,
            )
            .unwrap();
        runner
            .forget_from_user(
                &workspace,
                &request.operation_id,
                &waiting.summary.fingerprint,
            )
            .unwrap();
        std::fs::rename(
            vault.path().join("experiments/main.py"),
            vault.path().join("experiments/moved.py"),
        )
        .unwrap();
        assert_eq!(
            runner.prepare(&workspace, &request).unwrap_err().code,
            "EXPERIMENT_RECORD_FORGOTTEN"
        );
        assert!(runner
            .history(&workspace, 10, None)
            .unwrap()
            .items
            .is_empty());
        assert_eq!(
            runner
                .retention_usage(&workspace)
                .unwrap()
                .forgotten_operations,
            1
        );
        drop(runner);
        host_root.close().unwrap();
    }
    #[test]
    fn user_forget_cannot_discard_unresolved_native_cleanup_diagnostics() {
        let host_root = tempfile::tempdir().unwrap();
        let runner = Runner::default();
        runner.initialize_cleanup(host_root.path()).unwrap();
        let (_vault, workspace, request) = fixture("print(1)", ExecutionLimits::default());
        let waiting = runner.prepare(&workspace, &request).unwrap();
        runner
            .reject(
                &workspace,
                &request.operation_id,
                &waiting.summary.fingerprint,
            )
            .unwrap();
        let attempt = runner
            .cleanup_journal()
            .unwrap()
            .reserve(&request.vault_id, &request.operation_id)
            .unwrap();
        attempt.before_create().unwrap();
        assert_eq!(
            runner
                .forget_from_user(
                    &workspace,
                    &request.operation_id,
                    &waiting.summary.fingerprint
                )
                .unwrap_err()
                .code,
            "EXPERIMENT_CLEANUP_REQUIRED"
        );
        assert_eq!(
            runner
                .record(&workspace, &request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::Rejected
        );
        assert_eq!(
            runner.cleanup_status().unwrap().unwrap().operation_id,
            request.operation_id
        );
        drop(attempt);
        drop(runner);
        host_root.close().unwrap();
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
            "basic",
            "nonzero",
            "logs",
            "cancel",
            "switch",
            "shutdown",
            "wall",
            "cpu",
            "bad-output",
            "named-stream",
            "png-output",
            "journal-failure",
        ] {
            let script = include_str!("../../../scripts/fixtures/experiment_worker_probe.py")
                .replace("MODE = None", &format!("MODE = {mode:?}"));
            let limits = ExecutionLimits {
                wall_seconds: if mode == "wall" {
                    1
                } else if mode == "cpu" {
                    60
                } else {
                    // Functional cases must reach their intended output/lifecycle
                    // checks even when a shared runner delays Python startup.
                    30
                },
                cpu_seconds: if mode == "wall" || mode == "cpu" {
                    1
                } else {
                    10
                },
                disk_mib: 8,
                output_mib: 1,
                log_kib: 16,
                processes: 4,
                ..Default::default()
            };
            let (vault, workspace, request) = fixture(&script, limits);
            let host_root = tempfile::tempdir().unwrap();
            let runner = Runner::default();
            runner.initialize_cleanup(host_root.path()).unwrap();
            if mode == "journal-failure" {
                let db =
                    rusqlite::Connection::open(host_root.path().join("experiment-cleanup.sqlite3"))
                        .unwrap();
                db.execute_batch("CREATE TRIGGER fail_cleanup BEFORE DELETE ON cleanup BEGIN SELECT RAISE(ABORT,'injected cleanup commit failure');END;").unwrap();
            }
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
                "basic" | "logs" | "png-output" => {
                    assert_eq!(result.outcome, Outcome::Completed);
                    assert_eq!(result.exit_code, Some(0));
                }
                "journal-failure" => {
                    assert_eq!(result.outcome, Outcome::Failed);
                    assert_eq!(result.exit_code, Some(0));
                    assert_eq!(
                        result.error.as_deref(),
                        Some("EXPERIMENT_CLEANUP_JOURNAL_FAILED")
                    );
                }
                "nonzero" => {
                    assert_eq!(result.outcome, Outcome::Failed);
                    assert_eq!(result.exit_code, Some(7));
                }
                "bad-output" | "named-stream" => {
                    assert_eq!(result.outcome, Outcome::Failed, "{mode}: {result:?}");
                    assert_eq!(result.exit_code, Some(0));
                    assert!(matches!(
                        result.outputs,
                        Some(crate::experiment_outputs::OutputReport::Rejected { .. })
                    ));
                    assert_eq!(
                        result.error.as_deref(),
                        Some(if mode == "bad-output" {
                            "EXPERIMENT_OUTPUT_INVALID"
                        } else {
                            "EXPERIMENT_OUTPUT_NAMED_STREAM_REJECTED"
                        })
                    );
                }
                "cancel" | "switch" | "shutdown" => assert_eq!(result.outcome, Outcome::Cancelled),
                "wall" | "cpu" => assert_eq!(result.outcome, Outcome::Limited),
                _ => unreachable!(),
            }
            assert!(result.peak_memory_bytes.unwrap() > 0);
            assert!(result.final_disk_bytes.is_some());
            assert!(result.logs.stdout.complete && result.logs.stderr.complete);
            if mode == "basic" || mode == "journal-failure" {
                use crate::experiment_outputs::OutputReport;
                let Some(OutputReport::Collected { summary }) = &result.outputs else {
                    panic!("outputs were not collected")
                };
                assert_eq!(summary.files.len(), 2);
                assert_eq!(summary.total_bytes, 22);
                let slot = workspace.lock().unwrap();
                let ws = slot.as_ref().unwrap();
                assert_eq!(
                    ws.experiment_output(&request.operation_id, "worker-output.txt")
                        .unwrap()
                        .content(),
                    b"owned synthetic output"
                );
                assert!(ws
                    .experiment_output(&request.operation_id, "protected-descriptor.txt")
                    .unwrap()
                    .content()
                    .is_empty());
                drop(slot);
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
            if mode == "png-output" {
                let slot = workspace.lock().unwrap();
                let ws = slot.as_ref().unwrap();
                let output = ws
                    .experiment_output(&request.operation_id, "result.png")
                    .unwrap();
                assert_eq!(
                    output.manifest().kind,
                    crate::experiment_outputs::OutputKind::Png
                );
                assert!(output.content().starts_with(b"\x89PNG\r\n\x1a\n"));
                assert!(result.logs.stdout.text.contains("generated-png"));
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
                    } else if mode == "journal-failure" {
                        "EXPERIMENT_CLEANUP_REQUIRED"
                    } else {
                        "EXPERIMENT_STATE_CHANGED"
                    }
                );
                if mode == "journal-failure" {
                    let mut next = request.clone();
                    next.operation_id = uuid::Uuid::new_v4().to_string();
                    let waiting = runner.prepare(&workspace, &next).unwrap();
                    let approved = runner
                        .confirm_from_user(
                            &workspace,
                            &next.operation_id,
                            &waiting.summary.fingerprint,
                        )
                        .unwrap();
                    assert_eq!(
                        runner
                            .start(
                                workspace.clone(),
                                Runtime::Probe(runtime.clone()),
                                &next.operation_id
                            )
                            .unwrap_err()
                            .code,
                        "EXPERIMENT_CLEANUP_REQUIRED"
                    );
                    let unchanged = runner
                        .record(&workspace, &next.operation_id)
                        .unwrap()
                        .unwrap();
                    assert_eq!(unchanged.state, RunState::Approved);
                    assert_eq!(unchanged.approval_id, approved.approval_id);
                }
                runner.shutdown().unwrap();
            }
            let stopped = runner.stopped.lock().unwrap();
            assert_eq!(stopped.len(), 1);
            assert_eq!(stopped[0].remaining_processes, 0);
            assert!(
                stopped[0].profile_removed,
                "profile directory survived cleanup: {mode}"
            );
            assert!(
                stopped[0].profile_registry_removed,
                "owned registry survived cleanup: {mode}"
            );
            assert_eq!(stopped[0].cleanup_complete, mode != "journal-failure");
            let pending = runner.cleanup_status().unwrap();
            assert_eq!(pending.is_some(), mode == "journal-failure");
            if let Some(pending) = &pending {
                assert_eq!(pending.vault_id, request.vault_id);
                assert_eq!(pending.operation_id, request.operation_id);
                let objects = pending
                    .objects
                    .as_ref()
                    .expect("physical ownership receipts must survive journal failure");
                assert!(objects.profile.is_some() && objects.profile_sid.is_some());
                assert!(objects.source.is_some() && objects.runtime.is_some());
                assert!(objects.source_grants.as_ref().unwrap().len() >= 3);
                assert!(objects.runtime_grants.as_ref().unwrap().len() >= 36);
                assert_eq!(
                    pending.job.as_ref().unwrap().phase,
                    crate::experiment_cleanup::JobPhase::Configured
                );
                assert_eq!(
                    pending.error.as_deref(),
                    Some("EXPERIMENT_CLEANUP_JOURNAL_FAILED")
                );
                for path in [&pending.profile_root, &pending.source_root] {
                    assert!(
                        matches!(std::fs::symlink_metadata(path.as_ref().unwrap()),Err(e) if e.kind()==std::io::ErrorKind::NotFound)
                    );
                }
            }
            let mut evidence = serde_json::to_value(&stopped[0]).unwrap();
            let mut persisted = serde_json::Map::new();
            if mode == "basic" || mode == "png-output" || mode == "journal-failure" {
                let slot = workspace.lock().unwrap();
                let ws = slot.as_ref().unwrap();
                let paths: &[&str] = if mode != "png-output" {
                    &["worker-output.txt", "protected-descriptor.txt"]
                } else {
                    &["result.png"]
                };
                for path in paths {
                    let chunk = ws
                        .experiment_output_read(&request.operation_id, path, 0, 256 * 1024)
                        .unwrap();
                    assert!(chunk.next_offset.is_none());
                    persisted.insert(
                        (*path).into(),
                        serde_json::Value::String(chunk.content_base64),
                    );
                }
            }
            evidence["cleanup_pending"] = serde_json::json!(pending.is_some());
            if mode == "journal-failure" {
                let review = runner.cleanup_review().unwrap().unwrap();
                assert!(!review.profile_present);
                assert_eq!(review.temporary_objects, 0);
                let db =
                    rusqlite::Connection::open(host_root.path().join("experiment-cleanup.sqlite3"))
                        .unwrap();
                db.execute_batch("DROP TRIGGER fail_cleanup;").unwrap();
                runner.recover_cleanup(&review.fingerprint).unwrap();
                assert!(runner.cleanup_status().unwrap().is_none());
                // Recovery only clears temporary ownership. The original run
                // result and retained output bytes remain available afterward.
                assert!(runner
                    .record(&workspace, &request.operation_id)
                    .unwrap()
                    .unwrap()
                    .result
                    .is_some());
                let output = with_vault(&workspace, &request.vault_id, |ws| {
                    ws.experiment_output_read(
                        &request.operation_id,
                        "worker-output.txt",
                        0,
                        256 * 1024,
                    )
                })
                .unwrap();
                assert_eq!(
                    Some(&serde_json::Value::String(output.content_base64)),
                    persisted.get("worker-output.txt")
                );
                evidence["cleanup_recovered"] = serde_json::json!(true);
            }
            evidence["persisted_output_bytes"] = serde_json::Value::Object(persisted);
            reports.insert(mode, evidence);
            drop(stopped);
            drop(runner);
            host_root.close().unwrap();
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
