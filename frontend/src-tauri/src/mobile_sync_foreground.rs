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
}
impl WorkspaceAccess for ScopedWorkspace {
    fn access<T>(
        &self,
        action: impl FnOnce(&mut crate::workspace::Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T> {
        self.lease.with_current(|| {
            self.engine.access(|ws| {
                if let Some(device) = &self.device {
                    if let Some(binding) = ws.sync_binding()? {
                        if binding.endpoint != device.scope().endpoint()
                            || binding.account != device.scope().account()
                        {
                            return Err(crate::workspace::HostError::new(
                                "SYNC_AUTH_IDENTITY_CHANGED",
                            ));
                        }
                    }
                }
                action(ws)
            })
        })
    }
    fn start_attempt(&self, job: &crate::sync_state::Job) -> Result<()> {
        self.lease.with_current(|| {
            let device = self
                .device
                .as_ref()
                .ok_or_else(|| SyncError::new("SYNC_AUTH_RECONCILIATION_REQUIRED"))?;
            self.engine
                .access(|ws| ws.mobile_sync_attempt_start(job, device.scope(), device.device()))
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
