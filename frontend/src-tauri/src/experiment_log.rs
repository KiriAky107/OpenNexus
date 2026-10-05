//! Bounded raw log prefixes. Pipe draining continues after retention is full.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamSnapshot {
    pub text: String,
    pub bytes_seen: u64,
    pub retained_bytes: usize,
    pub truncated: bool,
    pub invalid_utf8: bool,
    pub complete: bool,
    pub read_error: bool,
}

pub struct LogBuffer {
    bytes: Vec<u8>,
    limit: usize,
    seen: u64,
    complete: bool,
    read_error: bool,
}
impl LogBuffer {
    pub fn new(limit: usize) -> Self {
        Self {
            bytes: Vec::new(),
            limit,
            seen: 0,
            complete: false,
            read_error: false,
        }
    }
    pub fn append(&mut self, bytes: &[u8]) {
        self.seen = self.seen.saturating_add(bytes.len() as u64);
        let keep = bytes.len().min(self.limit.saturating_sub(self.bytes.len()));
        self.bytes.extend_from_slice(&bytes[..keep]);
    }
    pub fn raw(&self) -> &[u8] {
        &self.bytes
    }
    pub fn snapshot(&self) -> StreamSnapshot {
        StreamSnapshot {
            text: String::from_utf8_lossy(&self.bytes).into_owned(),
            bytes_seen: self.seen,
            retained_bytes: self.bytes.len(),
            truncated: self.seen > self.bytes.len() as u64,
            invalid_utf8: std::str::from_utf8(&self.bytes).is_err(),
            complete: self.complete,
            read_error: self.read_error,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureSnapshot {
    pub stdout: StreamSnapshot,
    pub stderr: StreamSnapshot,
}

#[cfg(windows)]
mod native {
    use super::*;
    use crate::{
        experiment_policy::ValidatedLimits,
        extension_stdio::HostIo,
        workspace::{HostError, Result},
    };
    use std::{
        fs::File,
        io::Read,
        os::windows::io::AsRawHandle,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc, Mutex,
        },
        thread::JoinHandle,
        time::{Duration, Instant},
    };
    use windows_sys::Win32::System::IO::CancelSynchronousIo;

    struct Stream {
        log: LogBuffer,
        done: bool,
    }
    pub struct LogCapture {
        stdout: Arc<Mutex<Stream>>,
        stderr: Arc<Mutex<Stream>>,
        stop: Arc<AtomicBool>,
        workers: Vec<JoinHandle<()>>,
    }
    impl LogCapture {
        pub fn start(io: HostIo, limits: &ValidatedLimits) -> Result<Self> {
            // No interactive stdin. EOF also prevents an unattended input() hang.
            let HostIo {
                input,
                output,
                error,
            } = io;
            drop(input);
            let stream = || {
                Arc::new(Mutex::new(Stream {
                    log: LogBuffer::new(limits.log_bytes() / 2),
                    done: false,
                }))
            };
            let mut value = Self {
                stdout: stream(),
                stderr: stream(),
                stop: Arc::new(AtomicBool::new(false)),
                workers: Vec::new(),
            };
            for (name, file, state) in [
                ("experiment-stdout", output, Arc::clone(&value.stdout)),
                ("experiment-stderr", error, Arc::clone(&value.stderr)),
            ] {
                let stop = Arc::clone(&value.stop);
                let worker = std::thread::Builder::new()
                    .name(name.into())
                    .spawn(move || {
                        let outcome = drain(file, &state, &stop);
                        if let Ok(mut state) = state.lock() {
                            state.done = true;
                            match outcome {
                                Ok(eof) => state.log.complete = eof,
                                Err(()) => state.log.read_error = true,
                            }
                        }
                    })
                    .map_err(|_| HostError::new("EXPERIMENT_LOG_START_FAILED"))?;
                value.workers.push(worker);
            }
            Ok(value)
        }
        /// Call while observing the run; an IO failure must stop execution.
        pub fn check(&self) -> Result<()> {
            for (state, worker) in [&self.stdout, &self.stderr].into_iter().zip(&self.workers) {
                let state = state
                    .lock()
                    .map_err(|_| HostError::new("EXPERIMENT_LOG_READ_FAILED"))?;
                if state.log.read_error || (worker.is_finished() && !state.done) {
                    return Err(HostError::new("EXPERIMENT_LOG_READ_FAILED"));
                }
            }
            Ok(())
        }
        pub fn snapshot(&self) -> Result<CaptureSnapshot> {
            let snapshot = |state: &Mutex<Stream>| {
                state
                    .lock()
                    .map(|state| state.log.snapshot())
                    .map_err(|_| HostError::new("EXPERIMENT_LOG_READ_FAILED"))
            };
            Ok(CaptureSnapshot {
                stdout: snapshot(&self.stdout)?,
                stderr: snapshot(&self.stderr)?,
            })
        }
        /// The caller first terminates/waits for its owned Job. Descendants may
        /// hold pipe writers after the entry exits, so EOF waiting is bounded.
        pub fn finish(mut self) -> Result<CaptureSnapshot> {
            let start = Instant::now();
            while !self.workers.iter().all(JoinHandle::is_finished)
                && start.elapsed() < Duration::from_secs(2)
            {
                std::thread::sleep(Duration::from_millis(5));
            }
            self.stop_join();
            self.snapshot()
        }
        fn stop_join(&mut self) {
            self.stop.store(true, Ordering::Release);
            // Cancellation targets only our two anonymous-pipe reader threads.
            // Retry until both exit: a stop check can race with entering ReadFile,
            // and one cancellation does not apply to a subsequent IO request.
            while self.workers.iter().any(|worker| !worker.is_finished()) {
                for worker in &self.workers {
                    if !worker.is_finished() {
                        unsafe { CancelSynchronousIo(worker.as_raw_handle()) };
                    }
                }
                std::thread::sleep(Duration::from_millis(2));
            }
            for worker in self.workers.drain(..) {
                if worker.join().is_err() {
                    for state in [&self.stdout, &self.stderr] {
                        if let Ok(mut state) = state.lock() {
                            state.log.read_error = true;
                        }
                    }
                }
            }
        }
    }
    impl Drop for LogCapture {
        fn drop(&mut self) {
            self.stop_join();
        }
    }

    fn drain(
        mut file: File,
        state: &Mutex<Stream>,
        stop: &AtomicBool,
    ) -> std::result::Result<bool, ()> {
        let mut buffer = [0u8; 8192];
        while !stop.load(Ordering::Acquire) {
            match file.read(&mut buffer) {
                Ok(0) => return Ok(true),
                Ok(count) => state.lock().map_err(|_| ())?.log.append(&buffer[..count]),
                Err(_) if stop.load(Ordering::Acquire) => return Ok(false),
                Err(_) => return Err(()),
            }
        }
        Ok(false)
    }
    #[cfg(test)]
    mod tests {
        #[test]
        fn stopping_reader_does_not_wait_for_an_open_silent_writer() {
            use std::{
                os::windows::io::AsRawHandle,
                time::{Duration, Instant},
            };
            for _ in 0..20 {
                let (_writer, io) = crate::extension_stdio::ChildIo::create().unwrap();
                let limits = crate::experiment_policy::ExecutionLimits::default()
                    .validate()
                    .unwrap();
                let pump = super::LogCapture::start(io, &limits).unwrap();
                let start = Instant::now();
                loop {
                    let blocked = pump.workers.iter().all(|worker| {
                        let mut pending = 0;
                        assert_ne!(
                            unsafe {
                                windows_sys::Win32::System::Threading::GetThreadIOPendingFlag(
                                    worker.as_raw_handle(),
                                    &mut pending,
                                )
                            },
                            0
                        );
                        pending != 0
                    });
                    if blocked {
                        break;
                    }
                    assert!(start.elapsed() < Duration::from_secs(5));
                    std::thread::sleep(Duration::from_millis(2));
                }
                let start = Instant::now();
                drop(pump);
                assert!(start.elapsed() < Duration::from_secs(1));
            }
        }
    }
}

#[cfg(windows)]
pub use native::LogCapture;

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn prefixes_stay_bounded_and_decoding_loss_is_explicit() {
        let mut log = LogBuffer::new(5);
        log.append(b"hello");
        for _ in 0..100 {
            log.append(&[b'x'; 8192]);
        }
        assert_eq!(log.raw(), b"hello");
        let snapshot = log.snapshot();
        assert_eq!(snapshot.bytes_seen, 819205);
        assert_eq!(snapshot.retained_bytes, 5);
        assert!(snapshot.truncated && !snapshot.complete && !snapshot.read_error);
        let mut utf8 = LogBuffer::new(2);
        utf8.append("中".as_bytes());
        assert!(utf8.snapshot().invalid_utf8);
        assert!(utf8.snapshot().truncated);
        assert_eq!(utf8.raw(), &[0xe4, 0xb8]);
    }
}
