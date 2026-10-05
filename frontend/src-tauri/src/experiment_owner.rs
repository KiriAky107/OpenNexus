//! One Host-wide execution owner, acquired before consuming durable approval.
//! This coordinates lifetime and cancellation; it is not a native run permit.
use crate::{
    experiment_store::{ClaimedRun, RunRecord, RunResult},
    workspace::{HostError, Result, Workspace},
};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex, OnceLock,
};

#[derive(Default)]
struct Coordinator(Mutex<Option<Active>>);
struct Active {
    generation: uuid::Uuid,
    vault: String,
    operation: String,
    cancelled: Arc<AtomicBool>,
}
struct Reservation {
    coordinator: Arc<Coordinator>,
    generation: uuid::Uuid,
    cancelled: Arc<AtomicBool>,
}
fn global() -> &'static Arc<Coordinator> {
    static COORDINATOR: OnceLock<Arc<Coordinator>> = OnceLock::new();
    COORDINATOR.get_or_init(|| Arc::new(Coordinator::default()))
}
#[cfg(all(test, windows))]
pub(crate) static TEST_EXECUTION_LOCK: Mutex<()> = Mutex::new(());
fn unavailable() -> HostError {
    HostError::new("EXPERIMENT_RUN_OWNERSHIP_FAILED")
}
impl Coordinator {
    fn reserve(self: &Arc<Self>, vault: &str, operation: &str) -> Result<Reservation> {
        let mut state = self.0.lock().map_err(|_| unavailable())?;
        if state.is_some() {
            return Err(HostError::new("EXPERIMENT_RUN_BUSY"));
        }
        let generation = uuid::Uuid::new_v4();
        let cancelled = Arc::new(AtomicBool::new(false));
        *state = Some(Active {
            generation,
            vault: vault.into(),
            operation: operation.into(),
            cancelled: Arc::clone(&cancelled),
        });
        Ok(Reservation {
            coordinator: Arc::clone(self),
            generation,
            cancelled,
        })
    }
    fn cancel(&self, vault: &str, operation: Option<&str>) -> Result<bool> {
        let state = self.0.lock().map_err(|_| unavailable())?;
        let Some(active) = state.as_ref().filter(|active| {
            active.vault == vault && operation.is_none_or(|id| active.operation == id)
        }) else {
            return Ok(false);
        };
        active.cancelled.store(true, Ordering::Release);
        // Cancellation cannot release the slot: native resources still belong
        // to the worker. It must stop the complete Job and finish cleanup first.
        Ok(true)
    }
}
impl Drop for Reservation {
    fn drop(&mut self) {
        // Even after poison, only this generation may clear its own slot.
        // Future acquisition still fails on poison rather than bypassing it.
        let mut state = self.coordinator.0.lock().unwrap_or_else(|e| e.into_inner());
        if state
            .as_ref()
            .is_some_and(|active| active.generation == self.generation)
        {
            *state = None;
        }
    }
}

/// Move this unique owner into the run worker. Borrow it for the lifetime of
/// native execution and cleanup; dropping it is the sole slot-release path.
/// It cannot be cloned or decoded from IPC. A successful claim still requires
/// runtime/input binding and trusted native authorization before any resume.
pub struct RunOwner {
    run: ClaimedRun,
    reservation: Reservation,
}
impl RunOwner {
    pub fn claim(ws: &mut Workspace, operation: &str) -> Result<Self> {
        Self::claim_with(global(), ws, operation)
    }
    fn claim_with(
        coordinator: &Arc<Coordinator>,
        ws: &mut Workspace,
        operation: &str,
    ) -> Result<Self> {
        let reservation = coordinator.reserve(&ws.vault_id, operation)?;
        // Failure drops the reservation without changing another operation's
        // approval. A busy attempt never reaches the durable claim at all.
        let run = ws.experiment_claim(operation)?;
        Ok(Self { run, reservation })
    }
    pub fn run(&self) -> &ClaimedRun {
        &self.run
    }
    pub fn cancellation_requested(&self) -> bool {
        self.reservation.cancelled.load(Ordering::Acquire)
    }
    #[cfg(windows)]
    pub(crate) fn cancellation_signal(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.reservation.cancelled)
    }
    pub fn mark_running(&self, ws: &mut Workspace) -> Result<()> {
        if self.cancellation_requested() {
            return Err(HostError::new("EXPERIMENT_CANCELLED"));
        }
        ws.experiment_running(&self.run)
    }
    /// Persist only after the complete Job has stopped and logs have drained.
    /// This does not release ownership; keep this guard through ACL/temp cleanup.
    pub fn finish(&self, ws: &mut Workspace, result: RunResult) -> Result<RunRecord> {
        ws.experiment_finish(&self.run, result)
    }
    #[cfg(windows)]
    pub(crate) fn finish_outputs(
        &self,
        ws: &mut Workspace,
        result: RunResult,
        outputs: Option<&crate::experiment_outputs::CollectedOutputs>,
    ) -> Result<RunRecord> {
        ws.experiment_finish_outputs(&self.run, result, outputs)
    }
    /// The Host first records cancellation, then signals the matching worker.
    /// A different operation or vault never receives the stop signal.
    pub fn request_cancel(ws: &mut Workspace, operation: &str) -> Result<RunRecord> {
        let record = ws.experiment_cancel(operation)?;
        global().cancel(&ws.vault_id, Some(operation))?;
        Ok(record)
    }
    /// Used before vault switch/close. The caller must join its owned worker
    /// before changing workspace resources; this signal alone is not cleanup.
    pub fn request_workspace_stop(vault: &str) -> Result<bool> {
        global().cancel(vault, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_input::{PreparedInputs, RunRequest, SelectedFile},
        experiment_policy::ExecutionLimits,
        experiment_runtime::RUNTIME_ID,
        experiment_store::RunState,
    };
    fn approved() -> (tempfile::TempDir, Workspace, String) {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(root.path().join("experiments/main.py"), b"print(1)").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let file = ws.read("experiments/main.py").unwrap().entry;
        let request = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry: SelectedFile {
                file_id: file.file_id,
                path: file.path,
                hash: file.hash,
                revision: file.revision,
            },
            inputs: vec![],
            limits: ExecutionLimits::default(),
        };
        let inputs = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        let record = ws.experiment_prepare(inputs).unwrap();
        ws.experiment_approve(&request.operation_id, &record.summary.fingerprint)
            .unwrap();
        (root, ws, request.operation_id)
    }
    #[test]
    fn busy_vault_keeps_approval_and_failed_claim_releases_only_its_reservation() {
        let coordinator = Arc::new(Coordinator::default());
        let (_a, mut first, first_id) = approved();
        let (_b, mut second, second_id) = approved();
        assert!(RunOwner::claim_with(&coordinator, &mut first, "missing").is_err());
        let owner = RunOwner::claim_with(&coordinator, &mut first, &first_id).unwrap();
        assert_eq!(
            RunOwner::claim_with(&coordinator, &mut second, &second_id)
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_RUN_BUSY"
        );
        assert_eq!(
            second.experiment_record(&second_id).unwrap().unwrap().state,
            RunState::Approved
        );
        assert!(!coordinator
            .cancel(&second.vault_id, Some(&first_id))
            .unwrap());
        assert!(!coordinator
            .cancel(&first.vault_id, Some(&second_id))
            .unwrap());
        assert!(!owner.cancellation_requested());
        first.experiment_cancel(&first_id).unwrap();
        assert!(coordinator
            .cancel(&first.vault_id, Some(&first_id))
            .unwrap());
        assert!(owner.cancellation_requested());
        assert_eq!(
            owner.mark_running(&mut first).unwrap_err().code,
            "EXPERIMENT_CANCELLED"
        );
        assert!(coordinator.reserve(&second.vault_id, &second_id).is_err());
        drop(owner);
        let next = RunOwner::claim_with(&coordinator, &mut second, &second_id).unwrap();
        drop(next);
        // A released slot cannot replay an already consumed durable approval.
        assert!(RunOwner::claim_with(&coordinator, &mut second, &second_id).is_err());
        assert!(coordinator.reserve("vault", "fresh").is_ok());
    }
    #[test]
    fn concurrent_attempts_have_one_owner_and_cancellation_does_not_unlock() {
        let coordinator = Arc::new(Coordinator::default());
        let start = Arc::new(std::sync::Barrier::new(16));
        let attempted = Arc::new(std::sync::Barrier::new(16));
        let mut workers = vec![];
        for _ in 0..16 {
            let c = Arc::clone(&coordinator);
            let s = Arc::clone(&start);
            let a = Arc::clone(&attempted);
            workers.push(std::thread::spawn(move || {
                s.wait();
                let slot = c.reserve("vault", "operation");
                a.wait();
                if let Ok(slot) = slot {
                    assert!(c.cancel("vault", None).unwrap());
                    assert!(slot.cancelled.load(Ordering::Acquire));
                    assert!(c.reserve("vault", "other").is_err());
                    true
                } else {
                    false
                }
            }));
        }
        assert_eq!(
            workers
                .into_iter()
                .map(|w| usize::from(w.join().unwrap()))
                .sum::<usize>(),
            1
        );
        assert!(coordinator.reserve("next", "operation").is_ok());
    }
}
