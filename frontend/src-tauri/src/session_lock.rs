//! Windows 会话通知。撤销是原子性的，永远不会等待 KDF。 https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change
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

thread_local! { static SIGNALS: RefCell<Vec<Arc<AtomicU64>>> = const { RefCell::new(Vec::new()) }; }

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
        SIGNALS.with(|s| {
            for signal in s.borrow().iter() {
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
        Self::start_many(vec![signal])
    }
    pub fn start_many(signals: Vec<Arc<AtomicU64>>) -> Result<Self, String> {
        if signals.is_empty()
            || signals.len() > 8
            || signals
                .iter()
                .enumerate()
                .any(|(i, signal)| signals[..i].iter().any(|other| Arc::ptr_eq(signal, other)))
        {
            return Err("SESSION_MONITOR_UNAVAILABLE".into());
        }
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        let thread = std::thread::Builder::new()
            .name("host-session-monitor".into())
            .spawn(move || unsafe {
                SIGNALS.with(|s| *s.borrow_mut() = signals);
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
            })
            .map_err(|_| "SESSION_MONITOR_UNAVAILABLE".to_string())?;
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
    use crate::credentials::{CredentialBroker, CredentialId, Scope};
    use std::time::{Duration, Instant};
    use zeroize::Zeroizing;

    fn password() -> Zeroizing<Vec<u8>> {
        Zeroizing::new(b"b03-test-password-123".to_vec())
    }
    #[test]
    fn native_message_revokes_without_unlocking_on_session_return() {
        let signal = Arc::new(AtomicU64::new(0));
        let monitor = SessionMonitor::start(signal.clone()).unwrap();
        // 仅注入到我们的隐藏测试窗口中；永远不要锁定用户的桌面。
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
    #[test]
    fn b03_native_and_manual_lock_reject_new_resolves_within_one_second() {
        let temp = tempfile::tempdir().unwrap();
        let mut broker = CredentialBroker::new(temp.path().join("credentials.v1"));
        broker.unlock(password()).unwrap();
        let id = CredentialId::legacy("b03-provider");
        broker
            .put(&id, Zeroizing::new(b"b03-lock-fixture".to_vec()))
            .unwrap();
        let signal = broker.lock_signal();
        let monitor = SessionMonitor::start(signal).unwrap();

        let started = Instant::now();
        unsafe {
            SendMessageW(
                monitor.window as HWND,
                WM_WTSSESSION_CHANGE,
                WTS_SESSION_LOCK as usize,
                0,
            );
        }
        let error = broker.resolve(&Scope::Provider, &id).unwrap_err();
        assert_eq!(error, "CREDENTIALS_LOCKED");
        assert!(started.elapsed() < Duration::from_secs(1));

        broker.unlock(password()).unwrap();
        let started = Instant::now();
        broker.lock();
        let error = broker.resolve(&Scope::Provider, &id).unwrap_err();
        assert_eq!(error, "CREDENTIALS_LOCKED");
        assert!(started.elapsed() < Duration::from_secs(1));
        drop(monitor);
    }
    #[test]
    fn native_lock_logoff_and_disconnect_revoke_every_bound_domain() {
        let credential = Arc::new(AtomicU64::new(0));
        let extension = Arc::new(AtomicU64::new(0));
        assert!(SessionMonitor::start_many(vec![credential.clone(), credential.clone()]).is_err());
        let monitor =
            SessionMonitor::start_many(vec![credential.clone(), extension.clone()]).unwrap();
        for (index, event) in [
            WTS_SESSION_LOCK,
            WTS_SESSION_LOGOFF,
            WTS_CONSOLE_DISCONNECT,
            WTS_REMOTE_DISCONNECT,
        ]
        .into_iter()
        .enumerate()
        {
            unsafe {
                SendMessageW(
                    monitor.window as HWND,
                    WM_WTSSESSION_CHANGE,
                    event as usize,
                    0,
                );
            }
            assert_eq!(credential.load(Ordering::SeqCst), index as u64 + 1);
            assert_eq!(extension.load(Ordering::SeqCst), index as u64 + 1);
        }
        unsafe {
            SendMessageW(
                monitor.window as HWND,
                WM_WTSSESSION_CHANGE,
                WTS_SESSION_UNLOCK as usize,
                0,
            );
        }
        assert_eq!(credential.load(Ordering::SeqCst), 4);
        assert_eq!(extension.load(Ordering::SeqCst), 4);
    }
}
