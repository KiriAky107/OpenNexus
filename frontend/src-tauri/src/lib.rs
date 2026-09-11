//! 原生文件所有权与持久化 outbox；本库不依赖 WebView，可独立执行破坏性故障测试。

pub mod core;
pub mod core_update;
pub mod credentials;
mod payloads;
mod preference_records;
pub mod recent;
pub mod records;
#[cfg(feature = "desktop")]
pub mod request_lifecycle;
mod runtime_compat;
#[cfg(windows)]
pub mod session_lock;
#[cfg(feature = "desktop")]
pub mod sync_auth;
#[cfg(feature = "desktop")]
pub mod sync_client;
pub mod sync_discovery;
pub mod sync_inbox;
pub mod sync_initial;
pub mod sync_resolution;
pub mod sync_state;
pub mod workspace;
pub mod workspace_broker;

pub mod sync_retry;

#[cfg(feature = "desktop")]
pub mod extension_package;

#[cfg(feature = "desktop")]
pub mod extension_manifest;

#[cfg(feature = "desktop")]
pub mod extension_store;

#[cfg(feature = "desktop")]
pub mod extension_dependencies;

#[cfg(feature = "desktop")]
pub mod extension_unpack;

#[cfg(feature = "desktop")]
pub mod extension_permit;

#[cfg(feature = "desktop")]
pub mod extension_transaction;

#[cfg(feature = "desktop")]
pub mod extension_config;

#[cfg(feature = "desktop")]
pub mod extension_trust;

#[cfg(windows)]
pub mod extension_job;
pub mod extension_legacy;

#[cfg(windows)]
pub mod extension_container;

#[cfg(windows)]
pub mod extension_launch_data;

#[cfg(windows)]
pub mod extension_process;

#[cfg(windows)]
pub mod extension_deadline;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_pinned;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_launch_authorization;

#[cfg(all(windows, feature = "desktop"))]
mod extension_revocation;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_file_broker;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_network_broker;

#[cfg(windows)]
pub mod extension_stdio;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_io;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_mcp;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_mcp_tools;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_call_authorization;

#[cfg(all(windows, feature = "desktop"))]
pub mod extension_instance;

#[cfg(windows)]
mod process_creation;

pub mod sync_scope;
