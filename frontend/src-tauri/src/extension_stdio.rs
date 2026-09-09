//! 每次启动的匿名管道。只有子端进入显式继承列表。运行时拥有 Host 端，必须绑定帧并取消阻塞的 IO。
use crate::workspace::{HostError, Result};
#[cfg(any(feature = "desktop", test))]
use std::os::windows::io::FromRawHandle;
use std::{
    fs::File,
    os::windows::io::{AsRawHandle, OwnedHandle},
};
use windows_sys::Win32::Foundation::*;
#[cfg(any(feature = "desktop", test))]
use windows_sys::Win32::System::Pipes::CreatePipe;

pub struct HostIo {
    pub input: File,
    pub output: File,
    pub error: File,
}
pub(crate) struct ChildIo {
    input: OwnedHandle,
    output: OwnedHandle,
    error: OwnedHandle,
}
#[cfg(any(feature = "desktop", test))]
fn pair() -> Result<(OwnedHandle, OwnedHandle)> {
    let mut read = std::ptr::null_mut();
    let mut write = std::ptr::null_mut();
    if unsafe { CreatePipe(&mut read, &mut write, std::ptr::null(), 4096) } == 0 {
        return Err(HostError::new("EXTENSION_PIPE_CREATE_FAILED"));
    }
    Ok(unsafe {
        (
            OwnedHandle::from_raw_handle(read),
            OwnedHandle::from_raw_handle(write),
        )
    })
}
impl ChildIo {
    #[cfg(any(feature = "desktop", test))]
    pub(crate) fn create() -> Result<(Self, HostIo)> {
        let (input, host_input) = pair()?;
        let (host_output, output) = pair()?;
        let (host_error, error) = pair()?;
        let child = Self {
            input,
            output,
            error,
        };
        Ok((
            child,
            HostIo {
                input: host_input.into(),
                output: host_output.into(),
                error: host_error.into(),
            },
        ))
    }
    /// 同时持有管道端点与启动锁。字段的销毁顺序会先关闭所有可继承端点，
    /// 再允许其他并发 Host 启动继续执行。
    pub(crate) fn inherit(self) -> Result<InheritedIo> {
        let lock = crate::process_creation::lock().map_err(HostError::new)?;
        let guarded = InheritedIo {
            child: self,
            _creation: lock,
        };
        for handle in guarded.handles() {
            if unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) }
                == 0
            {
                return Err(HostError::new("EXTENSION_PIPE_CREATE_FAILED"));
            }
        }
        Ok(guarded)
    }
    pub(crate) fn handles(&self) -> [HANDLE; 3] {
        [
            self.input.as_raw_handle(),
            self.output.as_raw_handle(),
            self.error.as_raw_handle(),
        ]
    }
}

pub(crate) struct InheritedIo {
    child: ChildIo,
    _creation: std::sync::MutexGuard<'static, ()>,
}
impl InheritedIo {
    pub(crate) fn handles(&self) -> [HANDLE; 3] {
        self.child.handles()
    }
}

/// NDJSON maximum excludes the line terminator. A protocol/IO error poisons
/// the decoder; the runtime must terminate the instance and close its pipes.
/// This synchronous decoder needs a separate IO cancellation/deadline owner.
pub const MAX_FRAME_BYTES: usize = 2 * 1024 * 1024;
pub struct Frames<R> {
    reader: R,
    failed: bool,
}
impl<R: std::io::BufRead> Frames<R> {
    pub fn new(reader: R) -> Self {
        Self {
            reader,
            failed: false,
        }
    }
    pub fn read(&mut self) -> Result<Option<Vec<u8>>> {
        if self.failed {
            return Err(HostError::new("EXTENSION_PIPE_CLOSED"));
        }
        let result = self.read_inner();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn read_inner(&mut self) -> Result<Option<Vec<u8>>> {
        let mut frame = Vec::new();
        loop {
            let available = self
                .reader
                .fill_buf()
                .map_err(|_| HostError::new("EXTENSION_PIPE_READ_FAILED"))?;
            if available.is_empty() {
                return if frame.is_empty() {
                    Ok(None)
                } else {
                    Err(HostError::new("EXTENSION_PIPE_TRUNCATED_FRAME"))
                };
            }
            let end = available.iter().position(|b| *b == b'\n');
            let count = end.unwrap_or(available.len());
            if count > MAX_FRAME_BYTES - frame.len() {
                return Err(HostError::new("EXTENSION_BROKER_REQUEST_TOO_LARGE"));
            }
            frame.extend_from_slice(&available[..count]);
            self.reader.consume(count + usize::from(end.is_some()));
            if end.is_some() {
                if frame.last() == Some(&b'\r') {
                    frame.pop();
                }
                if frame.is_empty() {
                    return Err(HostError::new("EXTENSION_PIPE_EMPTY_FRAME"));
                }
                return Ok(Some(frame));
            }
        }
    }
}
pub fn write_frame(writer: &mut impl std::io::Write, frame: &[u8]) -> Result<()> {
    if frame.is_empty()
        || frame.len() > MAX_FRAME_BYTES
        || frame.contains(&b'\n')
        || frame.contains(&b'\r')
    {
        return Err(HostError::new("EXTENSION_PIPE_INVALID_FRAME"));
    }
    writer
        .write_all(frame)
        .and_then(|_| writer.write_all(b"\n"))
        .and_then(|_| writer.flush())
        .map_err(|_| HostError::new("EXTENSION_PIPE_WRITE_FAILED"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pipes_are_private_until_the_serialized_creation_window() {
        let (child, host) = ChildIo::create().unwrap();
        let ends = child
            .handles()
            .into_iter()
            .chain([
                host.input.as_raw_handle(),
                host.output.as_raw_handle(),
                host.error.as_raw_handle(),
            ])
            .collect::<Vec<_>>();
        for handle in &ends {
            let mut flags = 0;
            assert_ne!(unsafe { GetHandleInformation(*handle, &mut flags) }, 0);
            assert_eq!(flags & HANDLE_FLAG_INHERIT, 0);
        }
        let child = child.inherit().unwrap();
        for (index, handle) in ends.iter().enumerate() {
            let mut flags = 0;
            assert_ne!(unsafe { GetHandleInformation(*handle, &mut flags) }, 0);
            assert_eq!(flags & HANDLE_FLAG_INHERIT != 0, index < 3);
            assert!(!ends[..index].contains(handle));
        }
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (acquired_tx, acquired_rx) = std::sync::mpsc::channel();
        let worker = std::thread::spawn(move || {
            started_tx.send(()).unwrap();
            let _creation = crate::process_creation::lock().unwrap();
            acquired_tx.send(()).unwrap();
        });
        started_rx.recv().unwrap();
        assert!(acquired_rx
            .recv_timeout(std::time::Duration::from_millis(30))
            .is_err());
        drop(child);
        acquired_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .unwrap();
        worker.join().unwrap();
        // The last writer was closed before the lock was released; no child
        // process was launched in this ownership test, so Host sees EOF.
        use std::io::Read;
        let mut output = host.output;
        assert_eq!(output.read(&mut [0; 1]).unwrap(), 0);
    }
    #[test]
    fn frames_are_bounded_across_fragmentation_and_poison_after_errors() {
        use std::io::{BufReader, Cursor};
        let mut frames = Frames::new(BufReader::with_capacity(
            1,
            Cursor::new(b"{\"id\":1}\n{\"id\":2}\r\n"),
        ));
        assert_eq!(frames.read().unwrap().unwrap(), b"{\"id\":1}");
        assert_eq!(frames.read().unwrap().unwrap(), b"{\"id\":2}");
        assert!(frames.read().unwrap().is_none());
        let mut largest = vec![b'x'; MAX_FRAME_BYTES];
        largest.push(b'\n');
        assert_eq!(
            Frames::new(BufReader::with_capacity(127, Cursor::new(&largest)))
                .read()
                .unwrap()
                .unwrap()
                .len(),
            MAX_FRAME_BYTES
        );
        largest.insert(0, b'x');
        let mut overflow = Frames::new(BufReader::with_capacity(127, Cursor::new(largest)));
        assert_eq!(
            overflow.read().unwrap_err().code,
            "EXTENSION_BROKER_REQUEST_TOO_LARGE"
        );
        assert_eq!(overflow.read().unwrap_err().code, "EXTENSION_PIPE_CLOSED");
        let mut truncated = Frames::new(Cursor::new(b"{}"));
        assert_eq!(
            truncated.read().unwrap_err().code,
            "EXTENSION_PIPE_TRUNCATED_FRAME"
        );
        assert_eq!(truncated.read().unwrap_err().code, "EXTENSION_PIPE_CLOSED");
        assert_eq!(
            Frames::new(Cursor::new(b"\n")).read().unwrap_err().code,
            "EXTENSION_PIPE_EMPTY_FRAME"
        );
        let mut output = Vec::new();
        for invalid in [&b""[..], &b"{}\n{}"[..], &b"{}\r"[..]] {
            assert!(write_frame(&mut output, invalid).is_err());
            assert!(output.is_empty());
        }
        write_frame(&mut output, b"{}").unwrap();
        assert_eq!(output, b"{}\n");
    }
}
