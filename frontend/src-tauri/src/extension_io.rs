//! 用于 Host 创建的匿名管道的有界 IO。切勿在 UI 线程上运行关闭：取消等待本机管道操作确认它。
use crate::{
    extension_job::Job,
    extension_stdio::{write_frame, Frames, HostIo, MAX_FRAME_BYTES},
    workspace::{HostError, Result},
};
use std::{
    io::{BufReader, Read},
    os::windows::io::AsRawHandle,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, SyncSender},
        Arc, Mutex,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use windows_sys::Win32::System::IO::CancelSynchronousIo;
const QUEUED_FRAMES: usize = 4;
const STDERR_BYTES: usize = 1024 * 1024;
const OUTPUT_BYTES_PER_SECOND: usize = 8 * 1024 * 1024;
const OUTPUT_FRAMES_PER_SECOND: usize = 128;
#[derive(Debug)]
pub enum Event {
    Frame(Vec<u8>),
    Closed,
}
struct State {
    stopped: AtomicBool,
    #[cfg(test)]
    writing: AtomicBool,
    error: Mutex<Option<String>>,
    job: Job,
}
impl State {
    fn fail(&self, error: &str) {
        let mut saved = self.error.lock().unwrap_or_else(|e| e.into_inner());
        if saved.is_none() {
            *saved = Some(error.to_owned());
        }
        self.stopped.store(true, Ordering::Release);
        let _ = self.job.terminate();
    }
    fn check(&self) -> Result<()> {
        if let Some(error) = &*self.error.lock().unwrap_or_else(|e| e.into_inner()) {
            return Err(HostError::new(error));
        }
        if self.stopped.load(Ordering::Acquire) {
            return Err(HostError::new("EXTENSION_IO_CLOSED"));
        }
        Ok(())
    }
}
struct Worker {
    state: Arc<State>,
    thread: Option<JoinHandle<()>>,
}
impl Worker {
    fn spawn(
        state: &Arc<State>,
        name: &str,
        task: impl FnOnce(&State) -> Result<()> + Send + 'static,
    ) -> Result<Self> {
        let local = Arc::clone(state);
        let thread = std::thread::Builder::new()
            .name(name.into())
            .spawn(move || {
                match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| task(&local))) {
                    Ok(Err(error)) if !local.stopped.load(Ordering::Acquire) => {
                        local.fail(&error.code)
                    }
                    Err(_) => local.fail("EXTENSION_IO_WORKER_FAILED"),
                    _ => {}
                }
            })
            .map_err(|_| HostError::new("EXTENSION_IO_UNAVAILABLE"))?;
        Ok(Self {
            state: Arc::clone(state),
            thread: Some(thread),
        })
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        self.state.stopped.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            // Cancellation is not sticky: retry to cover the interval between
            // the worker checking stopped and actually entering Read/WriteFile.
            // These workers only issue anonymous-pipe IO, never arbitrary device IO.
            while !thread.is_finished() {
                unsafe {
                    CancelSynchronousIo(thread.as_raw_handle());
                }
                std::thread::sleep(Duration::from_millis(2));
            }
            let _ = thread.join();
        }
    }
}
pub struct Pump {
    state: Arc<State>,
    input: Option<SyncSender<Vec<u8>>>,
    output: Receiver<Event>,
    workers: Vec<Worker>,
}
impl Pump {
    pub(crate) fn start(io: HostIo, job: Job) -> Result<Self> {
        let state = Arc::new(State {
            stopped: AtomicBool::new(false),
            #[cfg(test)]
            writing: AtomicBool::new(false),
            error: Mutex::new(None),
            job,
        });
        let (input, writes) = mpsc::sync_channel::<Vec<u8>>(1);
        let (events, output) = mpsc::sync_channel(QUEUED_FRAMES);
        let mut pump = Self {
            state,
            input: Some(input),
            output,
            workers: Vec::with_capacity(3),
        };
        let HostIo {
            mut input,
            output,
            mut error,
        } = io;
        pump.workers.push(Worker::spawn(
            &pump.state,
            "extension-stdin",
            move |state| {
                while !state.stopped.load(Ordering::Acquire) {
                    match writes.recv_timeout(Duration::from_millis(20)) {
                        Ok(frame) => {
                            state.check()?;
                            #[cfg(test)]
                            state.writing.store(true, Ordering::Release);
                            write_frame(&mut input, &frame)?;
                            #[cfg(test)]
                            state.writing.store(false, Ordering::Release);
                        }
                        Err(RecvTimeoutError::Timeout) => {}
                        Err(RecvTimeoutError::Disconnected) => return Ok(()),
                    }
                }
                Ok(())
            },
        )?);
        pump.workers.push(Worker::spawn(
            &pump.state,
            "extension-stdout",
            move |state| {
                let mut frames = Frames::new(BufReader::new(output));
                let mut window = Instant::now();
                let mut bytes = 0;
                let mut count = 0;
                while !state.stopped.load(Ordering::Acquire) {
                    let frame = frames.read()?;
                    state.check()?;
                    if window.elapsed() >= Duration::from_secs(1) {
                        window = Instant::now();
                        bytes = 0;
                        count = 0;
                    }
                    if let Some(frame) = frame {
                        bytes += frame.len();
                        count += 1;
                        if bytes > OUTPUT_BYTES_PER_SECOND || count > OUTPUT_FRAMES_PER_SECOND {
                            return Err(HostError::new("EXTENSION_IO_RATE_LIMITED"));
                        }
                        events
                            .try_send(Event::Frame(frame))
                            .map_err(|_| HostError::new("EXTENSION_IO_OUTPUT_BACKPRESSURE"))?;
                    } else {
                        events
                            .try_send(Event::Closed)
                            .map_err(|_| HostError::new("EXTENSION_IO_OUTPUT_BACKPRESSURE"))?;
                        return Ok(());
                    }
                }
                Ok(())
            },
        )?);
        pump.workers.push(Worker::spawn(
            &pump.state,
            "extension-stderr",
            move |state| {
                // Drain without persisting possible secrets. Diagnostic retention
                // needs an explicit redaction policy before it can be enabled.
                let mut buffer = [0; 4096];
                let mut total = 0;
                while !state.stopped.load(Ordering::Acquire) {
                    let count = error
                        .read(&mut buffer)
                        .map_err(|_| HostError::new("EXTENSION_PIPE_READ_FAILED"))?;
                    if count == 0 {
                        return Ok(());
                    }
                    total += count;
                    if total > STDERR_BYTES {
                        return Err(HostError::new("EXTENSION_STDERR_LIMIT_EXCEEDED"));
                    }
                }
                Ok(())
            },
        )?);
        Ok(pump)
    }
    /// Nonblocking admission; at most one pending write plus one in progress.
    pub fn send(&self, frame: Vec<u8>) -> Result<()> {
        self.send_wait(frame, Duration::ZERO)
    }
    /// Bounded admission for serial protocol notifications immediately followed
    /// by a request; retries retain the same frame, without allocating copies.
    pub(crate) fn send_wait(&self, mut frame: Vec<u8>, timeout: Duration) -> Result<()> {
        self.state.check()?;
        if timeout > Duration::from_secs(1) {
            return Err(HostError::new("EXTENSION_IO_TIMEOUT_INVALID"));
        }
        if frame.is_empty()
            || frame.len() > MAX_FRAME_BYTES
            || frame.contains(&b'\n')
            || frame.contains(&b'\r')
        {
            return Err(HostError::new("EXTENSION_PIPE_INVALID_FRAME"));
        }
        let sender = self
            .input
            .as_ref()
            .ok_or_else(|| HostError::new("EXTENSION_IO_INPUT_CLOSED"))?;
        let started = Instant::now();
        loop {
            self.state.check()?;
            match sender.try_send(frame) {
                Ok(()) => return Ok(()),
                Err(mpsc::TrySendError::Disconnected(_)) => {
                    return Err(HostError::new("EXTENSION_IO_INPUT_CLOSED"))
                }
                Err(mpsc::TrySendError::Full(value)) => frame = value,
            }
            if started.elapsed() >= timeout {
                return Err(HostError::new("EXTENSION_IO_INPUT_BACKPRESSURE"));
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    }
    pub fn close_input(&mut self) {
        self.input.take();
    }
    pub fn receive(&self, timeout: Duration) -> Result<Event> {
        self.state.check()?;
        if timeout > Duration::from_secs(60) {
            return Err(HostError::new("EXTENSION_IO_TIMEOUT_INVALID"));
        }
        let started = Instant::now();
        loop {
            let remaining = timeout.saturating_sub(started.elapsed());
            let result = self
                .output
                .recv_timeout(remaining.min(Duration::from_millis(20)));
            self.state.check()?;
            match result {
                Ok(event) => return Ok(event),
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(HostError::new("EXTENSION_IO_CLOSED"))
                }
                Err(RecvTimeoutError::Timeout) if started.elapsed() >= timeout => {
                    return Err(HostError::new("EXTENSION_IO_TIMEOUT"))
                }
                Err(RecvTimeoutError::Timeout) => {}
            }
        }
    }

    pub fn check(&self) -> Result<()> {
        self.state.check()
    }
    /// Stop the process group first, then cancel and join every pipe worker.
    pub fn shutdown(mut self) -> Result<()> {
        let result = self.state.job.terminate();
        self.state.stopped.store(true, Ordering::Release);
        self.input.take();
        self.workers.clear();
        result
    }
}
impl Drop for Pump {
    fn drop(&mut self) {
        self.state.stopped.store(true, Ordering::Release);
        let _ = self.state.job.terminate();
        self.input.take();
        self.workers.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs::File, io::Write, os::windows::io::FromRawHandle};
    fn pipes() -> (HostIo, [File; 3]) {
        fn pair() -> (File, File) {
            let mut read = std::ptr::null_mut();
            let mut write = std::ptr::null_mut();
            assert_ne!(
                unsafe {
                    windows_sys::Win32::System::Pipes::CreatePipe(
                        &mut read,
                        &mut write,
                        std::ptr::null(),
                        4096,
                    )
                },
                0
            );
            unsafe { (File::from_raw_handle(read), File::from_raw_handle(write)) }
        }
        let (child_input, input) = pair();
        let (output, child_output) = pair();
        let (error, child_error) = pair();
        (
            HostIo {
                input,
                output,
                error,
            },
            [child_input, child_output, child_error],
        )
    }
    #[test]
    fn cancellation_joins_blocked_pipe_workers_even_when_peers_stay_open() {
        for _ in 0..20 {
            let (io, peers) = pipes();
            let pump = Pump::start(io, Job::new().unwrap()).unwrap();
            pump.send(vec![b'x'; MAX_FRAME_BYTES]).unwrap();
            let started = Instant::now();
            loop {
                let pending = pump.workers.iter().all(|worker| {
                    let mut pending = 0;
                    assert_ne!(
                        unsafe {
                            windows_sys::Win32::System::Threading::GetThreadIOPendingFlag(
                                worker.thread.as_ref().unwrap().as_raw_handle(),
                                &mut pending,
                            )
                        },
                        0
                    );
                    pending != 0
                });
                if pending && pump.state.writing.load(Ordering::Acquire) {
                    break;
                }
                assert!(
                    started.elapsed() < Duration::from_secs(5),
                    "workers did not enter native IO"
                );
                std::thread::sleep(Duration::from_millis(2));
            }
            pump.send(b"{}".to_vec()).unwrap();
            assert_eq!(
                pump.send(b"{}".to_vec()).unwrap_err().code,
                "EXTENSION_IO_INPUT_BACKPRESSURE"
            );
            let started = Instant::now();
            pump.shutdown().unwrap();
            assert!(started.elapsed() < Duration::from_secs(2));
            // The peer handles remained open throughout shutdown. No process
            // exit or peer EOF is available to mask broken IO cancellation.
            drop(peers);
        }
    }
    #[test]
    fn output_overflow_and_stderr_flood_fail_without_waiting_for_receive_timeout() {
        for stderr in [false, true] {
            let (io, [input, mut output, mut error]) = pipes();
            let pump = Pump::start(io, Job::new().unwrap()).unwrap();
            let sender = std::thread::spawn(move || {
                if stderr {
                    let _ = error.write_all(&vec![b'x'; STDERR_BYTES + 8192]);
                } else {
                    output
                        .write_all(&b"{}\n".repeat(QUEUED_FRAMES + 2))
                        .unwrap();
                }
                (input, output, error)
            });
            let start = Instant::now();
            while pump.check().is_ok() {
                assert!(start.elapsed() < Duration::from_secs(5));
                std::thread::sleep(Duration::from_millis(2));
            }
            let expected = if stderr {
                "EXTENSION_STDERR_LIMIT_EXCEEDED"
            } else {
                "EXTENSION_IO_OUTPUT_BACKPRESSURE"
            };
            assert_eq!(pump.check().unwrap_err().code, expected);
            let start = Instant::now();
            assert_eq!(
                pump.receive(Duration::from_secs(60)).unwrap_err().code,
                expected
            );
            assert!(start.elapsed() < Duration::from_secs(1));
            pump.shutdown().unwrap();
            drop(sender.join().unwrap());
        }
    }
}
