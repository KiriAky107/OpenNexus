//! Drain canceled overlapped reads before closing/reusing directory handles.
//! https://learn.microsoft.com/windows/win32/api/ioapiset/nf-ioapiset-cancelioex
use super::Hints;
use notify::{Event, EventKind};
use std::io;
use std::os::windows::ffi::OsStringExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc, Mutex};
use std::thread::{self, JoinHandle};
use windows_sys::Win32::Foundation::{
    GetLastError, ERROR_IO_PENDING, ERROR_NOTIFY_ENUM_DIR, HANDLE, INVALID_HANDLE_VALUE,
    WAIT_OBJECT_0,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, ReadDirectoryChangesW, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OVERLAPPED,
    FILE_LIST_DIRECTORY, FILE_NOTIFY_CHANGE_ATTRIBUTES, FILE_NOTIFY_CHANGE_DIR_NAME,
    FILE_NOTIFY_CHANGE_FILE_NAME, FILE_NOTIFY_CHANGE_LAST_WRITE, FILE_NOTIFY_CHANGE_SIZE,
    FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
};
use windows_sys::Win32::System::Threading::{
    CreateEventW, ResetEvent, SetEvent, WaitForMultipleObjects, INFINITE,
};
use windows_sys::Win32::System::IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED};

fn own(handle: HANDLE) -> io::Result<OwnedHandle> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: this newly created handle has one owner; OwnedHandle closes it once.
    Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
}
pub(super) struct NativeWatcher {
    stop: Arc<OwnedHandle>,
    thread: Option<JoinHandle<()>>,
}
impl NativeWatcher {
    pub(super) fn new(root: PathBuf, hints: Arc<Mutex<Hints>>) -> io::Result<Self> {
        use std::os::windows::ffi::OsStrExt;
        let path: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
        let directory = own(unsafe {
            CreateFileW(
                path.as_ptr(),
                FILE_LIST_DIRECTORY,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                std::ptr::null(),
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED,
                std::ptr::null_mut(),
            )
        })?;
        let stop = Arc::new(own(unsafe {
            CreateEventW(std::ptr::null(), 1, 0, std::ptr::null())
        })?);
        let completed = own(unsafe { CreateEventW(std::ptr::null(), 1, 0, std::ptr::null()) })?;
        let (ready, started) = mpsc::sync_channel(1);
        let signal = stop.clone();
        let thread = thread::Builder::new()
            .name("OpenNexus workspace watch".into())
            .spawn(move || {
                run(root, hints, directory, completed, signal, ready);
            })?;
        let watcher = Self {
            stop,
            thread: Some(thread),
        };
        if started.recv() != Ok(true) {
            return Err(io::Error::other("workspace watch unavailable"));
        }
        Ok(watcher)
    }
}
impl Drop for NativeWatcher {
    fn drop(&mut self) {
        unsafe {
            SetEvent(self.stop.as_raw_handle());
        }
        // Callback only locks Hints, never Workspace. No handle closes until the
        // worker has drained its outstanding IO and released the buffer.
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn failed(hints: &Mutex<Hints>) {
    if let Ok(mut hints) = hints.lock() {
        hints.full = true;
        hints.error = Some("WORKSPACE_WATCH_ERROR".into());
    }
}
fn events(root: &Path, hints: &Mutex<Hints>, bytes: &[u8]) {
    let mut offset = 0usize;
    let mut event = Event::new(EventKind::Any);
    while offset + 12 <= bytes.len() {
        let next = u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let size = u32::from_le_bytes(bytes[offset + 8..offset + 12].try_into().unwrap()) as usize;
        if !size.is_multiple_of(2) || size > bytes.len() - offset - 12 {
            failed(hints);
            return;
        }
        let name: Vec<u16> = bytes[offset + 12..offset + 12 + size]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u16::from_le_bytes(*pair))
            .collect();
        event = event.add_path(root.join(std::ffi::OsString::from_wide(&name)));
        if next == 0 {
            break;
        }
        if next < 12 + size || next > bytes.len() - offset {
            failed(hints);
            return;
        }
        offset += next;
    }
    if let Ok(mut hints) = hints.lock() {
        if bytes.is_empty() {
            hints.full = true;
        } else {
            hints.receive(root, Ok(event));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unicode_notifications_overflow_and_truncated_records_are_safe() {
        let root = Path::new("C:/synthetic-vault");
        let hints = Mutex::new(Hints::default());
        let name: Vec<_> = "文件夹/笔记😀.md"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect();
        let mut bytes = vec![0; 8];
        bytes.extend_from_slice(&(name.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&name);
        events(root, &hints, &bytes);
        assert!(hints.lock().unwrap().paths.contains("文件夹/笔记😀.md"));
        events(root, &hints, &[]);
        assert!(hints.lock().unwrap().full);
        bytes.pop();
        events(root, &hints, &bytes);
        assert_eq!(
            hints.lock().unwrap().error.as_deref(),
            Some("WORKSPACE_WATCH_ERROR")
        );
    }
}

fn run(
    root: PathBuf,
    hints: Arc<Mutex<Hints>>,
    directory: OwnedHandle,
    completed: OwnedHandle,
    stop: Arc<OwnedHandle>,
    ready: mpsc::SyncSender<bool>,
) {
    // DWORD alignment is required by ReadDirectoryChangesW.
    let mut buffer = vec![0u32; 16384];
    let mut ready = Some(ready);
    loop {
        let mut overlapped: OVERLAPPED = unsafe { std::mem::zeroed() };
        overlapped.hEvent = completed.as_raw_handle();
        unsafe {
            ResetEvent(completed.as_raw_handle());
        }
        let queued = unsafe {
            ReadDirectoryChangesW(
                directory.as_raw_handle(),
                buffer.as_mut_ptr().cast(),
                (buffer.len() * 4) as u32,
                1,
                FILE_NOTIFY_CHANGE_FILE_NAME
                    | FILE_NOTIFY_CHANGE_DIR_NAME
                    | FILE_NOTIFY_CHANGE_ATTRIBUTES
                    | FILE_NOTIFY_CHANGE_SIZE
                    | FILE_NOTIFY_CHANGE_LAST_WRITE,
                std::ptr::null_mut(),
                &mut overlapped,
                None,
            )
        };
        if queued == 0 && unsafe { GetLastError() } != ERROR_IO_PENDING {
            if let Some(ready) = ready.take() {
                let _ = ready.send(false);
            }
            failed(&hints);
            break;
        }
        if let Some(ready) = ready.take() {
            let _ = ready.send(true);
        }
        let handles = [stop.as_raw_handle(), completed.as_raw_handle()];
        let result = unsafe { WaitForMultipleObjects(2, handles.as_ptr(), 0, INFINITE) };
        let mut count = 0;
        if result != WAIT_OBJECT_0 + 1 {
            // SAFETY: overlapped and buffer stay alive through cancellation and
            // completion; handles close only after this worker returns.
            unsafe {
                CancelIoEx(directory.as_raw_handle(), &overlapped);
                GetOverlappedResult(directory.as_raw_handle(), &overlapped, &mut count, 1);
            }
            if result != WAIT_OBJECT_0 {
                failed(&hints);
            }
            break;
        }
        if unsafe { GetOverlappedResult(directory.as_raw_handle(), &overlapped, &mut count, 0) }
            == 0
        {
            if unsafe { GetLastError() } == ERROR_NOTIFY_ENUM_DIR {
                if let Ok(mut hints) = hints.lock() {
                    hints.full = true;
                }
                continue;
            }
            failed(&hints);
            break;
        }
        if count as usize > buffer.len() * 4 {
            failed(&hints);
            break;
        }
        // SAFETY: IO completed, count was checked against aligned allocation.
        let bytes =
            unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), count as usize) };
        events(&root, &hints, bytes);
    }
}
