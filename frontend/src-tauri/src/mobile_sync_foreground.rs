//! Native foreground and identity epochs around the existing Sync transport.
//! A lease never becomes current again after backgrounding or changing account.
use crate::{
    mobile_sync_auth::AuthenticatedDevice,
    mobile_workspace::MobileWorkspace,
    sync_client::{SyncError, WorkspaceAccess},
    sync_progress::Event,
};
use std::{
    future::Future,
    sync::{Arc, Mutex},
};
use tokio::sync::watch;

type Result<T> = std::result::Result<T, SyncError>;

#[derive(Clone, Copy, PartialEq, Eq)]
struct Snapshot {
    active: bool,
    epoch: u64,
}

/// Updated by Activity lifecycle callbacks, never by a WebView visibility flag.
/// Logout/account changes invalidate before waiting for the async request gate.
#[derive(Clone)]
pub struct Foreground {
    state: watch::Sender<Snapshot>,
}
impl Default for Foreground {
    fn default() -> Self {
        let (state, _) = watch::channel(Snapshot {
            active: false,
            epoch: 0,
        });
        Self { state }
    }
}
impl Foreground {
    fn change(&self, active: Option<bool>) {
        self.state.send_if_modified(|state| {
            let next_active = active.unwrap_or(state.active);
            if active.is_some() && state.active == next_active {
                return false;
            }
            match state.epoch.checked_add(1) {
                Some(epoch) => {
                    state.epoch = epoch;
                    state.active = next_active;
                }
                None => state.active = false, // Exhaustion must never revive an old lease.
            }
            true
        });
    }
    pub fn on_start(&self) {
        self.change(Some(true));
    }
    pub fn on_stop(&self) {
        self.change(Some(false));
    }
    pub fn invalidate_identity(&self) {
        self.change(None);
    }
    pub fn lease(&self) -> Result<Lease> {
        let state = *self.state.borrow();
        if !state.active {
            return Err(SyncError::new("SYNC_FOREGROUND_REQUIRED"));
        }
        Ok(Lease {
            owner: self.clone(),
            epoch: state.epoch,
        })
    }
}

#[derive(Clone)]
pub struct Lease {
    owner: Foreground,
    epoch: u64,
}
impl Lease {
    fn matches(&self, state: &Snapshot) -> bool {
        state.active && state.epoch == self.epoch
    }
    /// No await inside this closure. The read guard orders a short durable
    /// mutation/progress delivery before onStop, or refuses it afterward.
    /// The callback must not reenter lifecycle/identity mutation.
    pub fn with_current<T>(&self, action: impl FnOnce() -> Result<T>) -> Result<T> {
        let state = self.owner.state.borrow();
        if !self.matches(&state) {
            return Err(SyncError::new("SYNC_FOREGROUND_CHANGED"));
        }
        action()
    }
    pub fn check(&self) -> Result<()> {
        self.with_current(|| Ok(()))
    }

    /// Dropping an interrupted push runs the existing Attempt guard, preserving
    /// its original outbox id. Cancellation never invents a server receipt.
    pub async fn guard<T>(&self, action: impl Future<Output = Result<T>>) -> Result<T> {
        let mut changes = self.owner.state.subscribe();
        if !self.matches(&changes.borrow_and_update()) {
            return Err(SyncError::new("SYNC_FOREGROUND_CHANGED"));
        }
        tokio::select! {
            biased;
            _ = changes.changed() => Err(SyncError::new("SYNC_FOREGROUND_CHANGED")),
            value = action => self.with_current(|| value),
        }
    }
    pub fn progress(
        &self,
        deliver: Arc<dyn Fn(Event) + Send + Sync>,
    ) -> Arc<dyn Fn(Event) + Send + Sync> {
        let lease = self.clone();
        Arc::new(move |event| {
            let _ = lease.with_current(|| {
                deliver(event);
                Ok(())
            });
        })
    }
    pub fn workspace(&self, engine: Arc<Mutex<Option<MobileWorkspace>>>) -> ScopedWorkspace {
        ScopedWorkspace {
            engine,
            lease: self.clone(),
            device: None,
        }
    }
}

/// Uses the same native MobileWorkspace/dirty-draft/CAS protection port as Sync.
/// Stale responses cannot enter the current workspace even if HTTP completed.
pub struct ScopedWorkspace {
    engine: Arc<Mutex<Option<MobileWorkspace>>>,
    lease: Lease,
    device: Option<AuthenticatedDevice>,
}

impl crate::workspace_commit::CommitGuard for Lease {
    fn check(&self) -> crate::workspace::Result<()> {
        Lease::check(self).map_err(|error| crate::workspace::HostError::new(&error.code))
    }
    fn commit(&self, transaction: rusqlite::Transaction<'_>) -> crate::workspace::Result<()> {
        self.with_current(|| {
            transaction
                .commit()
                .map_err(crate::workspace::HostError::from)?;
            Ok(())
        })
        .map_err(|error| crate::workspace::HostError::new(&error.code))
    }
}

impl ScopedWorkspace {
    pub(crate) fn authenticated(
        engine: Arc<Mutex<Option<MobileWorkspace>>>,
        lease: Lease,
        device: AuthenticatedDevice,
    ) -> Self {
        Self {
            engine,
            lease,
            device: Some(device),
        }
    }

    fn work<'a, T: Send + 'static>(
        &'a self,
        binding: &'a str,
        action: impl FnOnce(&mut crate::workspace::Workspace, &Lease, &str) -> Result<T>
            + Send
            + 'static,
    ) -> impl Future<Output = Result<T>> + Send + 'a {
        let engine = self.engine.clone();
        let lease = self.lease.clone();
        let scope = self.device.as_ref().map(|device| {
            (
                device.scope().endpoint().to_owned(),
                device.scope().account().to_owned(),
            )
        });
        let binding = binding.to_owned();
        async move {
            let (endpoint, account) =
                scope.ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
            tokio::task::spawn_blocking(move || {
                lease.check()?;
                // The original epoch is checked around the owned editor lock,
                // never held while waiting for it or doing checked file IO.
                let mut guard = engine.lock().map_err(|_| SyncError::new("HOST_BUSY"))?;
                lease.check()?;
                let ws = &mut guard
                    .as_mut()
                    .ok_or_else(|| SyncError::new("MOBILE_STORAGE_UNAVAILABLE"))?
                    .workspace;
                let current = ws
                    .sync_binding()?
                    .ok_or_else(|| SyncError::new("SYNC_NOT_BOUND"))?;
                if current.id != binding
                    || current.endpoint != endpoint
                    || current.account != account
                {
                    return Err(SyncError::new("SYNC_AUTH_IDENTITY_CHANGED"));
                }
                action(ws, &lease, &binding)
            })
            .await
            .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))?
        }
    }

    fn binding_request(&self, request: &crate::mobile_sync_binding::Request) -> Result<()> {
        let device = self
            .device
            .as_ref()
            .ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
        if device.selection() != &request.selection {
            return Err(SyncError::new("SYNC_AUTH_SELECTION_CHANGED"));
        }
        self.lease.check()
    }
    pub fn check_initial_target(&self, vault_id: &str) -> Result<()> {
        if self.device.is_none() {
            return Err(SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"));
        }
        self.access(|ws| {
            if ws.vault_id != vault_id {
                return Err(crate::workspace::HostError::new("VAULT_CHANGED"));
            }
            if ws.sync_binding()?.is_some() {
                return Err(crate::workspace::HostError::new("SYNC_ALREADY_BOUND"));
            }
            Ok(())
        })
    }

    /// Persist the original confirmation before any remote snapshot/preparation.
    /// A repeated confirmation returns metadata and never another work ticket.
    pub fn begin_initial_binding(
        &self,
        request: &crate::mobile_sync_binding::Request,
    ) -> Result<crate::mobile_sync_binding::Prepared> {
        self.binding_request(request)?;
        self.access(|ws| ws.mobile_binding_begin(request))
    }
    /// Only an explicit confirmation of a previously read attempt revision can
    /// issue a replacement ticket; the old worker can no longer commit it.
    pub fn resume_initial_binding(
        &self,
        request: &crate::mobile_sync_binding::Request,
        observed_revision: i64,
    ) -> Result<crate::mobile_sync_binding::Prepared> {
        self.binding_request(request)?;
        self.access(|ws| ws.mobile_binding_resume(request, observed_revision))
    }
    pub fn initial_binding_operation(
        &self,
        operation: &str,
    ) -> Result<Option<crate::mobile_sync_binding::Intent>> {
        let device = self
            .device
            .as_ref()
            .ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
        self.access(|ws| ws.mobile_binding_read(device.scope(), operation))
    }
    pub fn pending_initial_bindings(
        &self,
        after: Option<&str>,
        limit: usize,
    ) -> Result<crate::mobile_sync_binding::Page> {
        let device = self
            .device
            .as_ref()
            .ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
        self.access(|ws| ws.mobile_binding_pending(device.scope(), after, limit))
    }

    /// Blocking worker only. Hash/spool with cancellation checks, then consume
    /// the ticket and publish its receipt within the same short SQL commit.
    pub fn commit_initial_binding(
        &self,
        ticket: crate::mobile_sync_binding::Ticket,
        snapshot: &crate::sync_initial::Snapshot,
    ) -> Result<crate::sync_state::Binding> {
        let request = ticket.request().clone();
        self.binding_request(&request)?;
        let mut guard = self
            .engine
            .lock()
            .map_err(|_| SyncError::new("HOST_BUSY"))?;
        self.lease.check()?;
        let ws = &mut guard
            .as_mut()
            .ok_or_else(|| SyncError::new("MOBILE_STORAGE_UNAVAILABLE"))?
            .workspace;
        if ws.vault_id != request.local_vault {
            return Err(SyncError::new("VAULT_CHANGED"));
        }
        let prepared = ws.sync_prepare_initial(
            &request.selection.endpoint,
            &request.remote_vault,
            &request.selection.account,
            snapshot,
            &request.preview_fingerprint,
            &|| {
                self.lease
                    .check()
                    .map_err(|error| crate::workspace::HostError::new(&error.code))
            },
        )?;
        self.lease.with_current(|| {
            prepared
                .commit_with(move |tx, binding| ticket.record(tx, binding))
                .map_err(Into::into)
        })
    }

    /// Call on a blocking worker, never the Activity/UI thread. File hashing is
    /// read-only and checks the original lease between paths and chunks rather
    /// than retaining its lifecycle read guard throughout filesystem I/O.
    pub fn initial_preview(
        &self,
        vault_id: &str,
        remote_vault: &str,
        snapshot: &crate::sync_initial::Snapshot,
    ) -> Result<crate::sync_initial::Preview> {
        self.lease.check()?;
        crate::sync_client::identifier(remote_vault)?;
        let device = self
            .device
            .as_ref()
            .ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
        let guard = self
            .engine
            .lock()
            .map_err(|_| SyncError::new("HOST_BUSY"))?;
        self.lease.check()?;
        let workspace = &guard
            .as_ref()
            .ok_or_else(|| SyncError::new("MOBILE_STORAGE_UNAVAILABLE"))?
            .workspace;
        if workspace.vault_id != vault_id {
            return Err(SyncError::new("VAULT_CHANGED"));
        }
        let preview = workspace.sync_preview_checked(
            device.scope().endpoint(),
            remote_vault,
            device.scope().account(),
            snapshot,
            &|| {
                self.lease
                    .check()
                    .map_err(|error| crate::workspace::HostError::new(&error.code))
            },
        )?;
        self.lease.check()?;
        Ok(preview)
    }
}
impl WorkspaceAccess for ScopedWorkspace {
    fn discover<'a>(&'a self, binding: &'a str) -> impl Future<Output = Result<usize>> + Send + 'a {
        self.work(binding, |ws, lease, binding| {
            let prepared = ws.sync_prepare_discovery(binding, &|| {
                lease
                    .check()
                    .map_err(|error| crate::workspace::HostError::new(&error.code))
            })?;
            lease.with_current(|| prepared.commit().map_err(Into::into))
        })
    }
    fn capture<'a>(&'a self, binding: &'a str) -> impl Future<Output = Result<()>> + Send + 'a {
        self.work(binding, |ws, lease, binding| {
            ws.sync_capture_checked(binding, lease).map_err(Into::into)
        })
    }
    fn access<T>(
        &self,
        action: impl FnOnce(&mut crate::workspace::Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T> {
        // The workspace may be busy saving an editor draft or importing an
        // attachment. Never retain a lifecycle read guard while waiting for
        // that mutex: Activity.onStop must be able to invalidate this lease.
        let mut guard = self
            .engine
            .lock()
            .map_err(|_| SyncError::new("HOST_BUSY"))?;
        self.lease.with_current(|| {
            let engine = guard
                .as_mut()
                .ok_or_else(|| SyncError::new("MOBILE_STORAGE_UNAVAILABLE"))?;
            let ws = &mut engine.workspace;
            if let Some(device) = &self.device {
                if let Some(binding) = ws.sync_binding()? {
                    if binding.endpoint != device.scope().endpoint()
                        || binding.account != device.scope().account()
                    {
                        return Err(SyncError::new("SYNC_AUTH_IDENTITY_CHANGED"));
                    }
                }
            }
            action(ws).map_err(Into::into)
        })
    }
    fn start_attempt(&self, job: &crate::sync_state::Job) -> Result<()> {
        self.access(|ws| {
            let device = self.device.as_ref().ok_or_else(|| {
                crate::workspace::HostError::new("SYNC_AUTH_RECONCILIATION_REQUIRED")
            })?;
            ws.mobile_sync_attempt_start(job, device.scope(), device.device())
        })
    }
    fn interrupt_attempt(&self, job: &crate::sync_state::Job) -> Result<()> {
        // This fixed operation only changes the already-running attempt's
        // outcome after check_job. It cannot apply content, advance a cursor,
        // acknowledge a remote commit, or create a replacement operation id.
        self.engine.interrupt_attempt(job)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::atomic::{AtomicBool, AtomicUsize, Ordering},
        time::Duration,
    };
    use tokio::sync::oneshot;

    #[test]
    fn native_sync_capture_preparation_yields_to_stop_between_payload_chunks() {
        use crate::workspace_commit::CommitGuard;
        struct Observed {
            lease: Lease,
            calls: std::cell::Cell<usize>,
            entered: std::sync::mpsc::Sender<()>,
            release: Arc<std::sync::Barrier>,
        }
        impl CommitGuard for Observed {
            fn check(&self) -> crate::workspace::Result<()> {
                self.calls.set(self.calls.get() + 1);
                if self.calls.get() == 6 {
                    self.entered.send(()).unwrap();
                    self.release.wait();
                }
                CommitGuard::check(&self.lease)
            }
            fn commit(
                &self,
                transaction: rusqlite::Transaction<'_>,
            ) -> crate::workspace::Result<()> {
                CommitGuard::commit(&self.lease, transaction)
            }
        }
        let root = tempfile::tempdir().unwrap();
        let mut ws = crate::workspace::Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example/", "remote", "account")
            .unwrap();
        let operation = uuid::Uuid::new_v4().to_string();
        let bytes = vec![7; 256 * 1024];
        ws.write_operation("original.md", "", &bytes, "local", &operation)
            .unwrap();
        let changes = ws.db.total_changes();
        let foreground = Foreground::default();
        foreground.on_start();
        let (entered, ready) = std::sync::mpsc::channel();
        let release = Arc::new(std::sync::Barrier::new(2));
        let observed = Observed {
            lease: foreground.lease().unwrap(),
            calls: std::cell::Cell::new(0),
            entered,
            release: release.clone(),
        };
        let worker = std::thread::spawn(move || {
            let error = ws
                .sync_prepare_capture(&binding.id, &|| observed.check())
                .err()
                .unwrap();
            (ws, error)
        });
        ready.recv_timeout(Duration::from_secs(2)).unwrap();
        let native = foreground.clone();
        let (stopped, notice) = std::sync::mpsc::channel();
        let activity = std::thread::spawn(move || {
            native.on_stop();
            stopped.send(()).unwrap();
        });
        let returned = notice.recv_timeout(Duration::from_secs(2)).is_ok();
        release.wait();
        activity.join().unwrap();
        let (ws, error) = worker.join().unwrap();
        assert!(
            returned,
            "Payload preparation retained a lifecycle guard and blocked onStop"
        );
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(ws.db.total_changes(), changes);
        let state: String = ws
            .db
            .query_row(
                "SELECT state FROM outbox WHERE operation_id=?1",
                [&operation],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(state, "pending");
        assert_eq!(
            std::fs::read(root.path().join("original.md")).unwrap(),
            bytes
        );
        let jobs: i64 = ws
            .db
            .query_row("SELECT COUNT(*) FROM sync_jobs", [], |row| row.get(0))
            .unwrap();
        assert_eq!(jobs, 0);
    }

    #[test]
    fn busy_editor_workspace_does_not_block_native_stop_and_stale_access_is_refused() {
        let foreground = Foreground::default();
        foreground.on_start();
        let engine = Arc::new(Mutex::new(None));
        let port = foreground.lease().unwrap().workspace(engine.clone());
        let editor = engine.lock().unwrap();
        let (entered, ready) = std::sync::mpsc::channel();
        let pending = std::thread::spawn(move || {
            entered.send(()).unwrap();
            port.access::<()>(|_| panic!("Backgrounded access entered the owned workspace"))
        });
        ready.recv_timeout(Duration::from_secs(2)).unwrap();
        // Allow the worker to enter its contended mutex wait. The stopped
        // Activity itself never waits for the editor to finish that work.
        std::thread::sleep(Duration::from_millis(50));
        let native = foreground.clone();
        let (stopped, notice) = std::sync::mpsc::channel();
        let activity = std::thread::spawn(move || {
            native.on_stop();
            stopped.send(()).unwrap();
        });
        let returned = notice.recv_timeout(Duration::from_secs(2)).is_ok();
        // Release before asserting, including the failure path, so an older
        // lock order produces a bounded failure rather than hanging the run.
        drop(editor);
        activity.join().unwrap();
        let error = pending.join().unwrap().unwrap_err();
        assert!(returned, "Activity.onStop waited for the editor workspace");
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
    }

    #[test]
    fn native_background_and_resume_never_reactivate_an_old_lease() {
        let foreground = Foreground::default();
        assert_eq!(
            foreground.lease().err().unwrap().code,
            "SYNC_FOREGROUND_REQUIRED"
        );
        foreground.on_start();
        let old = foreground.lease().unwrap();
        foreground.on_start(); // Duplicate Android callback is not an account change.
        old.check().unwrap();
        foreground.on_stop();
        assert_eq!(old.check().unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
        foreground.on_start();
        assert!(old.check().is_err());
        foreground.lease().unwrap().check().unwrap();
    }

    #[tokio::test]
    async fn native_on_stop_drops_a_running_future_without_waiting_for_network_timeout() {
        struct Dropped(Arc<AtomicBool>);
        impl Drop for Dropped {
            fn drop(&mut self) {
                self.0.store(true, Ordering::SeqCst);
            }
        }
        let foreground = Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        let dropped = Arc::new(AtomicBool::new(false));
        let observed = dropped.clone();
        let (entered, started) = oneshot::channel();
        let pending = tokio::spawn(async move {
            lease
                .guard(async move {
                    let _drop = Dropped(observed);
                    let _ = entered.send(());
                    std::future::pending::<Result<()>>().await
                })
                .await
        });
        started.await.unwrap();
        foreground.on_stop();
        let result = tokio::time::timeout(Duration::from_secs(1), pending)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(result.unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
        assert!(dropped.load(Ordering::SeqCst));
    }

    #[test]
    fn account_invalidation_keeps_foreground_but_refuses_old_progress_and_workspace_access() {
        let foreground = Foreground::default();
        foreground.on_start();
        let old = foreground.lease().unwrap();
        let engine = Arc::new(Mutex::new(None));
        let port = old.workspace(engine.clone());
        let calls = Arc::new(AtomicUsize::new(0));
        let observed = calls.clone();
        let progress = old.progress(Arc::new(move |_| {
            observed.fetch_add(1, Ordering::SeqCst);
        }));
        progress(Event::Processed(crate::sync_progress::Direction::Upload));
        foreground.invalidate_identity();
        progress(Event::Processed(crate::sync_progress::Direction::Upload));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert_eq!(
            port.access(|_| Ok(())).unwrap_err().code,
            "SYNC_FOREGROUND_CHANGED"
        );
        // Current port still refuses a missing workspace; the guard cannot
        // silently open a different root or bypass the existing native port.
        assert_eq!(
            foreground
                .lease()
                .unwrap()
                .workspace(engine)
                .access(|_| Ok(()))
                .unwrap_err()
                .code,
            "MOBILE_STORAGE_UNAVAILABLE"
        );
    }

    #[tokio::test]
    async fn response_completed_after_identity_change_cannot_escape_to_the_caller() {
        let foreground = Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        let result = lease
            .guard(async {
                foreground.invalidate_identity();
                Ok(42)
            })
            .await;
        assert_eq!(result.unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
    }

    #[tokio::test]
    async fn already_stale_request_is_not_polled_or_sent_after_resume() {
        let foreground = Foreground::default();
        foreground.on_start();
        let old = foreground.lease().unwrap();
        foreground.on_stop();
        foreground.on_start();
        let sent = AtomicBool::new(false);
        let result = old
            .guard(async {
                sent.store(true, Ordering::SeqCst);
                Ok(())
            })
            .await;
        assert_eq!(result.unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
        assert!(!sent.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn current_transport_error_remains_an_error_without_retry_or_identity_change() {
        let foreground = Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        let calls = AtomicUsize::new(0);
        let result: Result<()> = lease
            .guard(async {
                calls.fetch_add(1, Ordering::SeqCst);
                Err(SyncError::new("SYNC_SESSION_EXPIRED"))
            })
            .await;
        assert_eq!(result.unwrap_err().code, "SYNC_SESSION_EXPIRED");
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        lease.check().unwrap();
    }

    #[test]
    fn current_native_workspace_preserves_the_existing_error_and_denies_late_mutation() {
        let root = tempfile::tempdir().unwrap();
        let engine = Arc::new(Mutex::new(Some(
            MobileWorkspace::open(root.path()).unwrap(),
        )));
        let foreground = Foreground::default();
        foreground.on_start();
        let port = foreground.lease().unwrap().workspace(engine);
        let calls = AtomicUsize::new(0);
        let result: Result<()> = port.access(|_| {
            calls.fetch_add(1, Ordering::SeqCst);
            Err(crate::workspace::HostError::new("OWNED_SYNTHETIC_FAILURE"))
        });
        assert_eq!(result.unwrap_err().code, "OWNED_SYNTHETIC_FAILURE");
        foreground.on_stop();
        assert!(port
            .access(|_| {
                calls.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
            .is_err());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn exhausted_epoch_fails_closed_instead_of_reusing_a_previous_generation() {
        let foreground = Foreground::default();
        foreground.state.send_replace(Snapshot {
            active: true,
            epoch: u64::MAX,
        });
        let old = foreground.lease().unwrap();
        foreground.invalidate_identity();
        foreground.on_start();
        assert!(old.check().is_err());
        assert!(foreground.lease().is_err());
    }

    #[test]
    fn cancelled_attempt_keeps_its_original_queue_and_payload_without_allowing_late_content() {
        let root = tempfile::tempdir().unwrap();
        let engine = Arc::new(Mutex::new(Some(
            MobileWorkspace::open(root.path()).unwrap(),
        )));
        let foreground = Foreground::default();
        foreground.on_start();
        let port = foreground.lease().unwrap().workspace(engine.clone());
        let (binding, job, payload) = port
            .access(|ws| {
                ws.write("owned.md", "", b"owned original", "local")?;
                let binding =
                    ws.sync_bind_empty("https://sync.example", "owned-vault", "owned-account")?;
                let job = ws.sync_next(&binding.id)?.unwrap();
                let payload = ws.sync_commit_payload(&job)?;
                ws.sync_attempt_start(&job)?;
                Ok((binding, job, payload))
            })
            .unwrap();
        foreground.on_stop();
        port.interrupt_attempt(&job).unwrap();
        assert!(port
            .access(|ws| ws.write("late.md", "", b"late", "local").map(|_| ()))
            .is_err());
        foreground.on_start();
        let current = foreground.lease().unwrap().workspace(engine);
        current
            .access(|ws| {
                assert_eq!(
                    ws.sync_next(&binding.id)?.unwrap().operation_id,
                    job.operation_id
                );
                assert_eq!(ws.sync_commit_payload(&job)?, payload);
                let attempts = ws.sync_attempts(&binding.id)?;
                assert_eq!(attempts[0]["outcome"], "interrupted");
                assert_eq!(attempts[0]["attempts"], 1);
                assert!(!ws.root.join("late.md").exists());
                assert_eq!(std::fs::read(ws.root.join("owned.md"))?, b"owned original");
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn foreground_workspace_without_an_authenticated_device_cannot_start_a_network_attempt() {
        let root = tempfile::tempdir().unwrap();
        let engine = Arc::new(Mutex::new(Some(
            MobileWorkspace::open(root.path()).unwrap(),
        )));
        let foreground = Foreground::default();
        foreground.on_start();
        let port = foreground.lease().unwrap().workspace(engine);
        let (binding, job) = port
            .access(|ws| {
                ws.write("original.md", "", b"retained", "local")?;
                let binding = ws.sync_bind_empty("https://sync.example/", "remote", "account")?;
                let job = ws.sync_next(&binding.id)?.unwrap();
                Ok((binding, job))
            })
            .unwrap();
        assert_eq!(
            port.start_attempt(&job).unwrap_err().code,
            "SYNC_AUTH_RECONCILIATION_REQUIRED"
        );
        assert!(port
            .access(|ws| ws.sync_attempts(&binding.id))
            .unwrap()
            .is_empty());
        foreground.on_stop();
        assert_eq!(
            port.start_attempt(&job).unwrap_err().code,
            "SYNC_FOREGROUND_CHANGED"
        );
    }
}
