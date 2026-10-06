//! The Host owns grouped health, replacement and rollback. No renderer health claims.
use crate::{
    extension_commands::{runtime_backend, store},
    Host,
};
use notesagent_host::{
    extension_instance::{Endpoint, LaunchSpec, Status},
    extension_permit::{Claims, ExecutionKind},
    extension_store::RuntimePackage,
    extension_transaction::Receipt,
    workspace::HostError,
};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn current_vault(host: &Host, vault: &str) -> Result<(), String> {
    if host
        .workspace
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .as_ref()
        .ok_or("VAULT_NOT_OPEN")?
        .vault_id
        != vault
    {
        return Err("VAULT_CHANGED".into());
    }
    Ok(())
}
fn spec(
    host: &Host,
    runtime: RuntimePackage,
    vault: &str,
    operation: Option<&str>,
) -> Result<LaunchSpec, String> {
    let (entry, arguments, environment) = runtime_backend(&runtime.manifest)?;
    if !runtime.inventory.files.contains_key(&entry) {
        return Err("EXTENSION_ENTRY_INVALID".into());
    }
    let now = u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "EXTENSION_CLOCK_INVALID")?
            .as_millis(),
    )
    .map_err(|_| "EXTENSION_CLOCK_INVALID")?;
    let claims = Claims {
        kind: if runtime.release.kind == "plugin" {
            ExecutionKind::Plugin
        } else {
            ExecutionKind::Mcp
        },
        source: runtime.source,
        namespace: runtime.release.namespace,
        package_id: runtime.release.package_id,
        version: runtime.release.version,
        archive_sha256: runtime.release.sha256,
        tree_sha256: runtime.active.target.tree_sha256.clone(),
        signer_sha256: runtime.signer_sha256,
        entry,
        arguments,
        environment,
        permissions: runtime.release.permissions.into_iter().collect(),
        vault_id: vault.into(),
        platform: std::env::consts::OS.into(),
        policy_version: "1".into(),
        expires_at_ms: now
            .checked_add(8 * 60 * 60 * 1000)
            .ok_or("EXTENSION_CLOCK_INVALID")?,
    };
    let permit = host
        .extension_authority
        .issue(&claims, now)
        .map_err(|error| error.code)?;
    let extensions = Arc::clone(&host.extensions);
    let slot = runtime.active.target.slot.clone();
    let revision = runtime.active.revision.clone();
    let package_key = runtime.active.target.package_key.clone();
    let vault_id = vault.to_owned();
    let pending = operation.map(str::to_owned);
    Ok(LaunchSpec {
        installation_revision: revision.clone(),
        package: runtime.package,
        inventory: runtime.inventory,
        claims,
        permit,
        authority: Arc::clone(&host.extension_authority),
        credentials: Arc::clone(&host.credentials),
        vault_id: vault.into(),
        policy_version: "1".into(),
        system_root: PathBuf::from(std::env::var_os("SystemRoot").ok_or("SYSTEM_ROOT_MISSING")?),
        before_resume: Box::new(move |_| {
            let guard = extensions.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
            let current = guard
                .as_ref()
                .ok_or_else(|| HostError::new("EXTENSIONS_NOT_READY"))?
                .runtime_package(&slot, &vault_id, pending.as_deref())?;
            if current.active.revision != revision
                || current.active.target.package_key != package_key
            {
                return Err(HostError::new("EXTENSION_INSTALL_CONFLICT"));
            }
            Ok(())
        }),
    })
}
fn start(
    host: &Host,
    spec: LaunchSpec,
    checkpoint: &dyn Fn() -> Result<(), String>,
) -> Result<Endpoint, String> {
    checkpoint()?;
    let endpoint = unsafe {
        host.extension_instances
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .start(spec)
    }
    .map_err(|error| error.code)?;
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        let state = endpoint.snapshot();
        let failed = checkpoint().err().or_else(|| {
            if matches!(
                state.status,
                Status::Failed | Status::Stopped | Status::Stopping
            ) {
                Some(
                    state
                        .error
                        .unwrap_or_else(|| "EXTENSION_INSTANCE_NOT_READY".into()),
                )
            } else if Instant::now() >= deadline {
                Some("EXTENSION_START_TIMEOUT".into())
            } else {
                None
            }
        });
        if let Some(error) = failed {
            stop(host, &endpoint)?;
            return Err(error);
        }
        if state.status == Status::Ready {
            return Ok(endpoint);
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}
fn stop(host: &Host, endpoint: &Endpoint) -> Result<(), String> {
    host.extension_instances
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .stop_and_join(endpoint)
        .map_err(|error| error.code)
}

/// Explicitly enable a committed package. A pending group is activated as a group.
pub(super) fn enable_committed(host: &Host, slot: &str, vault: &str) -> Result<Endpoint, String> {
    current_vault(host, vault)?;
    let runtime = store(host, |store| store.runtime_package(slot, vault, None))?;
    let revision = runtime.active.revision.clone();
    let old = host
        .extension_endpoints
        .lock()
        .map_err(|_| "HOST_BUSY")?
        .get(slot)
        .cloned();
    if let Some(old) = old {
        if old.ready_for_revision(&revision) {
            return Ok(old);
        }
        stop(host, &old)?;
        host.extension_endpoints
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .remove(slot);
    }
    let endpoint = start(host, spec(host, runtime, vault, None)?, &|| {
        current_vault(host, vault)
    })?;
    let checked = (|| {
        current_vault(host, vault)?;
        let current = store(host, |store| store.runtime_package(slot, vault, None))?;
        if current.active.revision != revision || !endpoint.ready_for_revision(&revision) {
            return Err("EXTENSION_INSTALL_CONFLICT".into());
        }
        host.extension_endpoints
            .lock()
            .map_err(|_| "HOST_BUSY")?
            .insert(slot.into(), endpoint.clone());
        Ok(())
    })();
    if let Err(error) = checked {
        stop(host, &endpoint)?;
        return Err(error);
    }
    Ok(endpoint)
}

/// Finish the exact durable group, preserving the receipt after a commit/cancel race.
pub(super) fn finish_reviewed(
    host: &Host,
    vault: &str,
    operation: &str,
    outcome: Result<Receipt, String>,
    checkpoint: &dyn Fn() -> Result<(), String>,
) -> Result<Receipt, String> {
    let receipt = match outcome {
        Ok(receipt) => receipt,
        Err(error) => {
            // Online cancellation may race a committed pointer switch. This path
            // has not started any runtime, so only the exact pending transaction
            // can be restored. Completed/unknown operations are never reverted.
            if store(host, |store| store.installation_status(operation, vault))?
                .is_some_and(|receipt| receipt.state == "checking")
            {
                store(host, |store| store.finish_installation(operation, false))?;
            }
            return Err(error);
        }
    };
    if receipt.operation_id != operation {
        return Err("EXTENSION_INSTALL_CONFLICT".into());
    }
    if receipt.state != "checking" {
        return Ok(receipt);
    }
    activate_pending(host, vault, operation, checkpoint)
}
fn activate_pending(
    host: &Host,
    vault: &str,
    operation: &str,
    checkpoint: &dyn Fn() -> Result<(), String>,
) -> Result<Receipt, String> {
    let mut started = BTreeMap::<String, Endpoint>::new();
    let mut stopped = Vec::<String>::new();
    let attempt = (|| {
        checkpoint()?;
        current_vault(host, vault)?;
        // Verify the whole group before stopping any predecessor. This includes
        // declarative packages; they require valid signatures/configurations,
        // but have no fictitious runtime process or health status.
        let targets = store(host, |store| store.installation_targets(operation, vault))?;
        let materials = store(host, |store| {
            targets
                .iter()
                .map(|target| store.runtime_package(&target.target.slot, vault, Some(operation)))
                .collect::<notesagent_host::workspace::Result<Vec<_>>>()
        })?;
        let mut plans = Vec::new();
        for runtime in materials {
            if !matches!(runtime.release.kind.as_str(), "plugin" | "mcp") {
                continue;
            }
            let slot = runtime.active.target.slot.clone();
            let revision = runtime.active.revision.clone();
            plans.push((slot, revision, spec(host, runtime, vault, Some(operation))?));
        }
        if plans.len() > 16 {
            return Err("EXTENSION_INSTANCE_LIMIT".into());
        }
        let mut required = BTreeMap::<String, Endpoint>::new();
        for (slot, revision, plan) in plans {
            checkpoint()?;
            current_vault(host, vault)?;
            let old = host
                .extension_endpoints
                .lock()
                .map_err(|_| "HOST_BUSY")?
                .get(&slot)
                .cloned();
            if let Some(old) = old {
                if old.ready_for_revision(&revision) {
                    required.insert(slot, old);
                    continue;
                }
                stop(host, &old)?;
                host.extension_endpoints
                    .lock()
                    .map_err(|_| "HOST_BUSY")?
                    .remove(&slot);
                stopped.push(slot.clone());
            }
            let endpoint = start(host, plan, checkpoint)?;
            started.insert(slot.clone(), endpoint.clone());
            required.insert(slot, endpoint);
        }
        checkpoint()?;
        // Workspace and endpoint locks prevent a switch or a UI disable from
        // crossing the final revalidation/atomic commit/publication boundary.
        let workspace = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
        if workspace.as_ref().ok_or("VAULT_NOT_OPEN")?.vault_id != vault {
            return Err("VAULT_CHANGED".into());
        }
        let mut endpoints = host.extension_endpoints.lock().map_err(|_| "HOST_BUSY")?;
        let receipt = store(host, |store| {
            let current = store.installation_targets(operation, vault)?;
            if current != targets {
                return Err(HostError::new("EXTENSION_INSTALL_CONFLICT"));
            }
            for target in &current {
                let material =
                    store.runtime_package(&target.target.slot, vault, Some(operation))?;
                if matches!(material.release.kind.as_str(), "plugin" | "mcp")
                    && !required
                        .get(&target.target.slot)
                        .is_some_and(|endpoint| endpoint.ready_for_revision(&target.revision))
                {
                    return Err(HostError::new("EXTENSION_GROUP_NOT_HEALTHY"));
                }
            }
            checkpoint().map_err(|code| HostError::new(&code))?;
            store.finish_installation(operation, true)
        })?;
        for (slot, endpoint) in &started {
            endpoints.insert(slot.clone(), endpoint.clone());
        }
        Ok(receipt)
    })();
    match attempt {
        Ok(receipt) => Ok(receipt),
        Err(error) => {
            // Do not restore a pointer while cleanup of a newly owned generation
            // remains unproven; the durable pending state stays visible.
            for endpoint in started.values() {
                endpoint.stop();
            }
            let mut cleanup_pending = error == "EXTENSION_INSTALL_CLEANUP_REQUIRED";
            for endpoint in started.values() {
                // Attempt every owned cleanup even if a previous one failed.
                // Never leave another unpublished generation executing merely
                // because one sandbox requires operator recovery.
                cleanup_pending |= stop(host, endpoint).is_err();
            }
            if cleanup_pending {
                return Err("EXTENSION_INSTALL_CLEANUP_REQUIRED".into());
            }
            let receipt = store(host, |store| store.installation_status(operation, vault))?;
            if !receipt.is_some_and(|receipt| receipt.state == "checking") {
                return Err(error);
            }
            store(host, |store| store.finish_installation(operation, false))?;
            if current_vault(host, vault).is_ok() {
                for slot in stopped {
                    // Restoration uses old signed material and current trust;
                    // it cannot resurrect an untrusted/revoked package.
                    if enable_committed(host, &slot, vault).is_err() {
                        return Err("EXTENSION_UPDATE_ROLLED_BACK_RUNTIME_STOPPED".into());
                    }
                }
            }
            Err(error)
        }
    }
}

#[cfg(test)]
mod tests;
