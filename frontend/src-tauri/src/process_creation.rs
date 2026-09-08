//! Coordinate Host-controlled Windows launches while inheritable handles exist.
//! This does not serialize foreign libraries that bypass this Host boundary.
use std::sync::{Mutex, MutexGuard};
static CREATION: Mutex<()> = Mutex::new(());

pub(crate) fn lock() -> Result<MutexGuard<'static, ()>, &'static str> {
    CREATION.lock().map_err(|_| "PROCESS_CREATION_LOCK_FAILED")
}
