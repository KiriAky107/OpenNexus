//! 原生文件所有权与持久化 outbox；本库不依赖 WebView，可独立执行破坏性故障测试。

mod canvas_contract;
pub mod core;
pub mod core_update;
#[cfg(windows)]
mod credential_autounlock;
pub mod credentials;
pub mod experiment_agent;
#[cfg(windows)]
pub mod experiment_cleanup;
#[cfg(windows)]
mod experiment_cleanup_job;
#[cfg(any(windows, test))]
mod experiment_cleanup_lease;
#[cfg(windows)]
mod experiment_cleanup_objects;
#[cfg(windows)]
mod experiment_cleanup_recovery;
mod experiment_contract;
#[cfg(windows)]
pub mod experiment_execution;
mod experiment_file_broker;
pub mod experiment_import;
pub mod experiment_input;
pub mod experiment_log;
pub mod experiment_outputs;
pub mod experiment_owner;
pub mod experiment_preview;
pub mod experiment_store;
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
pub mod sync_account;
#[cfg(feature = "desktop")]
pub mod sync_auth;
pub mod sync_capabilities;
#[cfg(feature = "desktop")]
pub mod sync_client;
pub mod sync_discovery;
#[cfg(feature = "sync-e2ee-prototype")]
pub mod sync_e2ee_prototype;
pub mod sync_inbox;
pub mod sync_initial;
pub mod sync_progress;
pub mod sync_resolution;
pub mod sync_review;
#[cfg(test)]
mod sync_review_tests;
pub mod sync_state;
pub mod workspace;
pub mod workspace_broker;

pub mod sync_retry;

pub mod extension_package;

pub mod extension_manifest;

#[cfg(feature = "desktop")]
pub mod extension_store;

#[cfg(feature = "desktop")]
pub mod extension_dependencies;

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

#[cfg(windows)]
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

#[cfg(windows)]
mod experiment_disk;
#[cfg(windows)]
mod experiment_filesystem;
pub mod experiment_policy;
#[cfg(windows)]
mod experiment_registry;
#[cfg(windows)]
pub mod experiment_runner;
pub mod experiment_runtime;
#[cfg(windows)]
pub mod experiment_runtime_bound;
#[cfg(windows)]
pub mod experiment_sources;

#[cfg(all(test, windows))]
mod experiment_runtime_probe;

pub mod sync_scope;
