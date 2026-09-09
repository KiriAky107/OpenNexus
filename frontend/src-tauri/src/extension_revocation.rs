//! 本机实例监视器；撤销不依赖于调用者轮询。
use crate::{
    extension_job::Job,
    extension_permit::Lease,
    workspace::{HostError, Result},
};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Condvar, Mutex,
    },
    thread::JoinHandle,
    time::Duration,
};
struct KillOnExit(Job);
impl Drop for KillOnExit {
    fn drop(&mut self) {
        let _ = self.0.terminate();
    }
}
pub(crate) struct Watch {
    lease: Lease,
    cancelled: Arc<(Mutex<bool>, Condvar)>,
    failed: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}
impl Watch {
    pub(crate) fn arm(job: &Job, lease: Lease) -> Result<Self> {
        lease.check()?;
        let job = KillOnExit(job.clone_for_deadline()?);
        let cancelled = Arc::new((Mutex::new(false), Condvar::new()));
        let failed = Arc::new(AtomicBool::new(false));
        let thread_cancel = Arc::clone(&cancelled);
        let thread_failed = Arc::clone(&failed);
        let thread_lease = lease.clone();
        let worker = std::thread::Builder::new()
            .name("extension-revocation".into())
            .spawn(move || {
                let mut stop = thread_cancel.0.lock().unwrap_or_else(|e| e.into_inner());
                while !*stop {
                    if thread_lease.check().is_err() {
                        if job.0.terminate().is_err() {
                            thread_failed.store(true, Ordering::Release);
                        }
                        return;
                    }
                    stop = thread_cancel
                        .1
                        .wait_timeout(stop, Duration::from_millis(50))
                        .unwrap_or_else(|e| e.into_inner())
                        .0;
                }
            })
            .map_err(|_| HostError::new("EXTENSION_REVOCATION_WATCH_UNAVAILABLE"))?;
        Ok(Self {
            lease,
            cancelled,
            failed,
            worker: Some(worker),
        })
    }
    pub(crate) fn check(&self) -> Result<()> {
        if self.failed.load(Ordering::Acquire) {
            return Err(HostError::new("EXTENSION_RESOURCE_TERMINATE_FAILED"));
        }
        self.lease.check()?;
        if self
            .worker
            .as_ref()
            .is_some_and(|worker| worker.is_finished())
        {
            return Err(HostError::new("EXTENSION_REVOCATION_WATCH_FAILED"));
        }
        Ok(())
    }
}
impl Drop for Watch {
    fn drop(&mut self) {
        *self.cancelled.0.lock().unwrap_or_else(|e| e.into_inner()) = true;
        self.cancelled.1.notify_all();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
