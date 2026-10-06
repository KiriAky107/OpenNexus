//! Vault-bound installed metadata and reviewable rollback plans. No activation.
use super::*;
use crate::{
    extension_dependencies::{LockedPackage, Plan},
    extension_transaction::Target,
};

#[derive(Debug, Serialize)]
pub struct InstalledPackage {
    pub slot: String,
    pub package_key: String,
    pub source: String,
    pub release: Release,
    pub revision: String,
    pub pending_operation: Option<String>,
    pub configuration: serde_json::Value,
    pub rollback_operation_id: Option<String>,
}
#[derive(Debug, Serialize)]
pub struct InstalledPage {
    pub items: Vec<InstalledPackage>,
    pub total: usize,
    pub offset: u32,
    pub limit: u32,
    pub app_version: String,
    pub platform: String,
    pub architecture: String,
}
fn vault_id(value: &str) -> Result<String> {
    let canonical = Uuid::parse_str(value)
        .map_err(|_| HostError::new("VAULT_INVALID"))?
        .to_string();
    if value != canonical {
        return Err(HostError::new("VAULT_INVALID"));
    }
    Ok(canonical)
}
fn slot(vault: &str, origin: &str, release: &Release) -> String {
    hash(&serde_json::to_vec(&(vault, origin, &release.namespace, &release.package_id)).unwrap())
}
impl ExtensionStore {
    /// Stream metadata only and retain just the requested page. Old installations
    /// need no migration: their slot digest already binds the vault and source.
    pub fn installed(&self, vault: &str, offset: u32, limit: u32) -> Result<InstalledPage> {
        let vault = vault_id(vault)?;
        if limit == 0 || limit > 100 {
            return Err(HostError::new("EXTENSION_PAGE_INVALID"));
        }
        let mut query=self.db.prepare("SELECT a.slot,a.target,a.revision,a.pending_operation,v.source,v.release,v.manifest FROM extension_active a LEFT JOIN versions v ON v.package_key=json_extract(a.target,'$.package_key') ORDER BY v.source,v.namespace,v.package_id,a.slot")?;
        let mut rows = query.query([])?;
        let mut items = Vec::new();
        let mut total = 0usize;
        while let Some(row) = rows.next()? {
            let current_slot: String = row.get(0)?;
            let raw_target: String = row.get(1)?;
            let origin: String = row.get(4)?;
            let raw_release: String = row.get(5)?;
            let raw_manifest: String = row.get(6)?;
            if raw_target.len() > 128 * 1024
                || raw_release.len() > 512 * 1024
                || raw_manifest.len() > 1024 * 1024
            {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            let release: Release = serde_json::from_str(&raw_release)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            if slot(&vault, &origin, &release) != current_slot {
                continue;
            }
            let target: Target = serde_json::from_str(&raw_target)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            let revision: String = row.get(2)?;
            if target.slot != current_slot || hash(raw_target.as_bytes()) != revision {
                return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
            }
            let manifest: serde_json::Value = serde_json::from_str(&raw_manifest)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            crate::extension_config::validate(&manifest, &target.configuration)?;
            let pending_operation: Option<String> = row.get(3)?;
            let index = total;
            total = total
                .checked_add(1)
                .ok_or_else(|| HostError::new("EXTENSION_PAGE_INVALID"))?;
            if index < (offset as usize) || items.len() >= limit as usize {
                continue;
            }
            let operation: Option<String> = if pending_operation.is_none() {
                self.db.query_row("SELECT t.id FROM extension_transactions t,json_each(t.after_state) c WHERE t.state='complete' AND json_extract(c.value,'$.target.slot')=?1 AND json_extract(c.value,'$.target.package_key')=?2 ORDER BY t.rowid DESC LIMIT 1",params![current_slot,target.package_key],|r|r.get(0)).optional()?
            } else {
                None
            };
            let rollback_operation_id = operation.filter(|id| self.rollback_changes(id).is_ok());
            items.push(InstalledPackage {
                slot: current_slot,
                package_key: target.package_key,
                source: origin,
                release,
                revision,
                pending_operation,
                configuration: target.configuration,
                rollback_operation_id,
            });
        }
        Ok(InstalledPage {
            items,
            total,
            offset,
            limit,
            app_version: env!("CARGO_PKG_VERSION").into(),
            platform: std::env::consts::OS.into(),
            architecture: std::env::consts::ARCH.into(),
        })
    }

    /// Freeze all old targets, configurations, dependency identities and current
    /// trust revisions before any online check or pointer change.
    pub fn rollback_preview(
        &mut self,
        installed_operation: &str,
        vault: &str,
    ) -> Result<InstallPreview> {
        let vault = vault_id(vault)?;
        Uuid::parse_str(installed_operation).map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        let changes = self.rollback_changes(installed_operation)?;
        let mut packages = Vec::new();
        let mut trust_revisions = Vec::new();
        for change in &changes {
            let (origin, json, key): (String, String, Vec<u8>) = self.db.query_row(
                "SELECT source,release,signer FROM versions WHERE package_key=?1",
                [&change.target.package_key],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )?;
            let release: Release = serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            if change.target.slot != slot(&vault, &origin, &release) {
                return Err(HostError::new("VAULT_CHANGED"));
            }
            let public: [u8; 32] = key
                .try_into()
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            self.check_not_revoked(&origin, &release, &public)?;
            let trust = self
                .trust_setting(&origin, &release.namespace, &release.key_id)?
                .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED"))?;
            if !trust.enabled || trust.public_key != public {
                return Err(HostError::new("EXTENSION_SOURCE_UNTRUSTED"));
            }
            let archive = self.archive(&change.target.package_key)?;
            let (_, manifest) = release.verify_package(
                &public,
                &release.key_id,
                &release.namespace,
                false,
                false,
                &archive,
            )?;
            crate::extension_config::validate(&manifest, &change.target.configuration)?;
            let prepared = self.prepare(
                &change.target.package_key,
                Signer {
                    public_key: &public,
                    key_id: &release.key_id,
                    namespace: &release.namespace,
                    revoked: false,
                },
                false,
            )?;
            if prepared.directory != change.target.directory
                || prepared.tree_sha256 != change.target.tree_sha256
            {
                return Err(HostError::new("EXTENSION_INSTALL_CONFLICT"));
            }
            packages.push(LockedPackage {
                package_key: change.target.package_key.clone(),
                source: origin,
                namespace: release.namespace.clone(),
                package_id: release.package_id.clone(),
                kind: release.kind.clone(),
                version: release.version.clone(),
                archive_sha256: release.sha256.clone(),
                signer_sha256: hash(&public),
                release_sha256: hash(&serde_json::to_vec(&release).unwrap()),
                permissions: release.permissions.clone(),
            });
            trust_revisions.push(trust.fingerprint()?);
        }
        let fingerprint = hash(
            &serde_json::to_vec(&(
                "rollback",
                installed_operation,
                vault,
                &changes,
                &packages,
                trust_revisions,
            ))
            .unwrap(),
        );
        let dependencies = Plan {
            fingerprint: hash(&serde_json::to_vec(&packages).unwrap()),
            packages,
        };
        Ok(InstallPreview {
            fingerprint,
            dependencies,
            changes,
        })
    }

    pub async fn rollback_reviewed(
        &mut self,
        operation: &str,
        installed_operation: &str,
        vault: &str,
        fingerprint: &str,
    ) -> Result<crate::extension_transaction::Receipt> {
        let preview = self.rollback_preview(installed_operation, vault)?;
        if preview.fingerprint != fingerprint {
            return Err(HostError::new("EXTENSION_INSTALL_REVIEW_CHANGED"));
        }
        self.switch_online(operation, vault, &preview.changes).await
    }

    pub fn installation_status(
        &self,
        operation: &str,
        vault: &str,
    ) -> Result<Option<crate::extension_transaction::Receipt>> {
        let vault = vault_id(vault)?;
        Uuid::parse_str(operation).map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        let row: Option<(String, String)> = self
            .db
            .query_row(
                "SELECT state,after_state FROM extension_transactions WHERE id=?1",
                [operation],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        let Some((state, json)) = row else {
            return Ok(None);
        };
        if json.len() > 4 * 1024 * 1024 {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        let changes: Vec<crate::extension_transaction::Change> =
            serde_json::from_str(&json).map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
        if changes.is_empty() || changes.len() > 200 {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        for change in changes {
            let (origin, json): (String, String) = self.db.query_row(
                "SELECT source,release FROM versions WHERE package_key=?1",
                [change.target.package_key],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            let release: Release = serde_json::from_str(&json)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
            if change.target.slot != slot(&vault, &origin, &release) {
                return Err(HostError::new("VAULT_CHANGED"));
            }
        }
        Ok(Some(crate::extension_transaction::Receipt {
            operation_id: operation.into(),
            state,
        }))
    }

    /// Bind the complete pending group to its durable reviewed targets. The Host
    /// still verifies each archive/tree/configuration and every runtime health.
    pub fn installation_targets(
        &self,
        operation: &str,
        vault: &str,
    ) -> Result<Vec<crate::extension_transaction::Active>> {
        let receipt = self
            .installation_status(operation, vault)?
            .ok_or_else(|| HostError::new("EXTENSION_INSTALL_UNKNOWN"))?;
        if receipt.state != "checking" {
            return Err(HostError::new("EXTENSION_INSTALL_NOT_PENDING"));
        }
        let json: String = self.db.query_row(
            "SELECT after_state FROM extension_transactions WHERE id=?1",
            [operation],
            |row| row.get(0),
        )?;
        let changes: Vec<crate::extension_transaction::Change> =
            serde_json::from_str(&json).map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
        changes
            .into_iter()
            .map(|change| {
                let active = self
                    .active_installation(&change.target.slot)?
                    .ok_or_else(|| HostError::new("EXTENSION_INSTALL_CONFLICT"))?;
                if active.pending_operation.as_deref() != Some(operation)
                    || active.target != change.target
                    || active.revision != hash(&serde_json::to_vec(&active.target).unwrap())
                {
                    return Err(HostError::new("EXTENSION_INSTALL_CONFLICT"));
                }
                Ok(active)
            })
            .collect()
    }
}

#[cfg(test)]
mod tests;
