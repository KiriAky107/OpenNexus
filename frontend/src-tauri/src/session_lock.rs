//! Windows session notifications. Revocation is atomic and never waits for a KDF.
//! https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change
use std::cell::RefCell;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use windows_sys::Win32::{
    Foundation::*,
    System::{LibraryLoader::GetModuleHandleW, RemoteDesktop::*},
    UI::WindowsAndMessaging::*,
};

thread_local! { static SIGNAL: RefCell<Option<Arc<AtomicU64>>> = const { RefCell::new(None) }; }

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == WM_WTSSESSION_CHANGE
        && matches!(
            wparam as u32,
            WTS_SESSION_LOCK | WTS_SESSION_LOGOFF | WTS_CONSOLE_DISCONNECT | WTS_REMOTE_DISCONNECT
        )
    {
        SIGNAL.with(|s| {
            if let Some(signal) = s.borrow().as_ref() {
                signal.fetch_add(1, Ordering::SeqCst);
            }
        });
    }
    if message == WM_DESTROY {
        WTSUnRegisterSessionNotification(hwnd);
        PostQuitMessage(0);
        return 0;
    }
    DefWindowProcW(hwnd, message, wparam, lparam)
}

pub struct SessionMonitor {
    window: usize,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl SessionMonitor {
    pub fn start(signal: Arc<AtomicU64>) -> Result<Self, String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        let thread = std::thread::spawn(move || unsafe {
            SIGNAL.with(|s| *s.borrow_mut() = Some(signal));
            let class: Vec<u16> = format!("OpenNexusSession-{}\0", uuid::Uuid::new_v4())
                .encode_utf16()
                .collect();
            let module = GetModuleHandleW(std::ptr::null());
            let descriptor = WNDCLASSW {
                lpfnWndProc: Some(window_proc),
                hInstance: module,
                lpszClassName: class.as_ptr(),
                ..std::mem::zeroed()
            };
            if RegisterClassW(&descriptor) == 0 {
                let _ = tx.send(Err("SESSION_MONITOR_UNAVAILABLE".to_string()));
                return;
            }
            let window = CreateWindowExW(
                0,
                class.as_ptr(),
                class.as_ptr(),
                0,
                0,
                0,
                0,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                module,
                std::ptr::null(),
            );
            if window.is_null()
                || WTSRegisterSessionNotification(window, NOTIFY_FOR_THIS_SESSION) == 0
            {
                if !window.is_null() {
                    DestroyWindow(window);
                }
                UnregisterClassW(class.as_ptr(), module);
                let _ = tx.send(Err("SESSION_MONITOR_UNAVAILABLE".to_string()));
                return;
            }
            if tx.send(Ok(window as usize)).is_err() {
                DestroyWindow(window);
            } else {
                let mut message: MSG = std::mem::zeroed();
                while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {
                    TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
            }
            UnregisterClassW(class.as_ptr(), module);
        });
        match rx
            .recv()
            .map_err(|_| "SESSION_MONITOR_UNAVAILABLE".to_string())?
        {
            Ok(window) => Ok(Self {
                window,
                thread: Some(thread),
            }),
            Err(error) => {
                let _ = thread.join();
                Err(error)
            }
        }
    }
}
impl Drop for SessionMonitor {
    fn drop(&mut self) {
        unsafe {
            PostMessageW(self.window as HWND, WM_CLOSE, 0, 0);
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_message_revokes_without_unlocking_on_session_return() {
        let signal = Arc::new(AtomicU64::new(0));
        let monitor = SessionMonitor::start(signal.clone()).unwrap();
        // Inject only into our hidden test window; never lock the user's desktop.
        unsafe {
            SendMessageW(
                monitor.window as HWND,
                WM_WTSSESSION_CHANGE,
                WTS_SESSION_LOCK as usize,
                0,
            );
        }
        assert_eq!(signal.load(Ordering::SeqCst), 1);
        unsafe {
            SendMessageW(
                monitor.window as HWND,
                WM_WTSSESSION_CHANGE,
                WTS_SESSION_UNLOCK as usize,
                0,
            );
        }
        assert_eq!(signal.load(Ordering::SeqCst), 1);
    }
}
