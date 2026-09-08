//! 原生文件所有权与持久化 outbox；本库不依赖 WebView，可独立执行破坏性故障测试。

pub mod core;
pub mod credentials;
pub mod recent;
#[cfg(feature = "desktop")]
pub mod request_lifecycle;
mod runtime_compat;
#[cfg(windows)]
pub mod session_lock;
pub mod workspace;
