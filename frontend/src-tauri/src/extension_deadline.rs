//! 每个工具调用的截止时间。持久服务器的生命周期不是 60 秒。
use crate::{
    extension_job::Job,
    workspace::{HostError, Result},
};
use std::{
    sync::{
        atomic::{AtomicU8, Ordering},
        Arc, Condvar, Mutex,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};

pub const TOOL_BUDGET: Duration = Duration::from_secs(60);
struct State {
    cancelled: Mutex<bool>,
    wake: Condvar,
    outcome: AtomicU8,
}
/// Host 拥有的防护，在调度工具调用之前创建。仅当呼叫完成时才完成。即使调用者从不轮询，过期也会杀死整个实例组。未完成就丢弃会中止实例；只有显式成功完成才能取消计时器，同时保留服务器。
pub struct ToolDeadline {
    state: Arc<State>,
    job: Arc<Job>,
    expires: Instant,
    worker: Option<JoinHandle<()>>,
}
impl ToolDeadline {
    pub(crate) fn arm(job: &Job) -> Result<Self> {
        Self::with_budget(job, TOOL_BUDGET)
    }
    fn with_budget(job: &Job, budget: Duration) -> Result<Self> {
        if budget.is_zero() || budget > TOOL_BUDGET {
            return Err(HostError::new("EXTENSION_TOOL_BUDGET_INVALID"));
        }
        let expires = Instant::now() + budget;
        let job = Arc::new(job.clone_for_deadline()?);
        let state = Arc::new(State {
            cancelled: Mutex::new(false),
            wake: Condvar::new(),
            outcome: AtomicU8::new(0),
        });
        let thread_job = Arc::clone(&job);
        let thread_state = Arc::clone(&state);
        let worker = std::thread::Builder::new()
            .name("extension-tool-deadline".into())
            .spawn(move || {
                let mut cancelled = thread_state
                    .cancelled
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                loop {
                    if *cancelled {
                        return;
                    }
                    let remaining = expires.saturating_duration_since(Instant::now());
                    if remaining.is_zero() {
                        break;
                    }
                    cancelled = thread_state
                        .wake
                        .wait_timeout(cancelled, remaining)
                        .unwrap_or_else(|e| e.into_inner())
                        .0;
                }
                thread_state.outcome.store(1, Ordering::Release);
                drop(cancelled);
                if thread_job.terminate().is_err() {
                    thread_state.outcome.store(2, Ordering::Release);
                }
            })
            .map_err(|_| HostError::new("EXTENSION_TOOL_WATCHDOG_UNAVAILABLE"))?;
        Ok(Self {
            state,
            job,
            expires,
            worker: Some(worker),
        })
    }
    pub fn check(&self) -> Result<()> {
        match self.state.outcome.load(Ordering::Acquire) {
            2 => Err(HostError::new("EXTENSION_RESOURCE_TERMINATE_FAILED")),
            3 => Err(HostError::new("EXTENSION_TOOL_WATCHDOG_FAILED")),
            1 => Err(HostError::new("EXTENSION_TOOL_DEADLINE_EXCEEDED")),
            _ if Instant::now() >= self.expires => {
                Err(HostError::new("EXTENSION_TOOL_DEADLINE_EXCEEDED"))
            }
            _ => Ok(()),
        }
    }
    /// 完成无法取消已用完的预算，即使尚未安排计时器线程来观察到期情况。
    pub fn finish(mut self) -> Result<()> {
        self.stop();
        match self.state.outcome.load(Ordering::Acquire) {
            0 => Ok(()),
            2 => Err(HostError::new("EXTENSION_RESOURCE_TERMINATE_FAILED")),
            3 => Err(HostError::new("EXTENSION_TOOL_WATCHDOG_FAILED")),
            _ => Err(HostError::new("EXTENSION_TOOL_DEADLINE_EXCEEDED")),
        }
    }
    /// 显式放弃终止实例并报告终止失败。
    pub fn cancel(mut self) -> Result<()> {
        let result = self.job.terminate();
        self.stop();
        result
    }
    fn stop(&mut self) {
        if self.worker.is_none() {
            return;
        }
        let mut cancelled = self
            .state
            .cancelled
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if Instant::now() < self.expires {
            *cancelled = true;
        }
        self.state.wake.notify_all();
        drop(cancelled);
        if self.worker.take().unwrap().join().is_err() {
            let _ = self.job.terminate();
            self.state.outcome.store(3, Ordering::Release);
        }
    }
    #[cfg(test)]
    pub(crate) fn arm_test(job: &Job, budget: Duration) -> Result<Self> {
        Self::with_budget(job, budget)
    }
}
impl Drop for ToolDeadline {
    fn drop(&mut self) {
        if self.worker.is_some() {
            let _ = self.job.terminate();
            self.stop();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_invalid_budgets_and_finishes_without_waiting_for_full_budget() {
        let job = Job::new().unwrap();
        assert!(ToolDeadline::with_budget(&job, Duration::ZERO).is_err());
        assert!(ToolDeadline::with_budget(&job, Duration::from_secs(61)).is_err());
        let deadline = ToolDeadline::arm(&job).unwrap();
        assert!(deadline.check().is_ok());
        let start = Instant::now();
        deadline.finish().unwrap();
        assert!(start.elapsed() < Duration::from_secs(5));
    }
}
