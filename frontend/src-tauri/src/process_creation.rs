//! 在存在可继承手柄的情况下协调 Host 控制的 Windows 启动。这不会序列化绕过此 Host 边界的外部库。
use std::sync::{Mutex, MutexGuard};
static CREATION: Mutex<()> = Mutex::new(());

pub(crate) fn lock() -> Result<MutexGuard<'static, ()>, &'static str> {
    CREATION.lock().map_err(|_| "PROCESS_CREATION_LOCK_FAILED")
}
