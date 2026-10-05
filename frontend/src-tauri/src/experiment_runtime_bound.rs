//! Per-run runtime ownership. Neither discovery nor these guards authorize a run.
use crate::{
    experiment_runtime::{self, RuntimeInfo, RUNTIME_ID},
    extension_container::Profile,
    extension_package::Inventory,
    extension_pinned::{BoundEntry, PackageAccess, PinnedPackage},
    workspace::{hash, HostError, Result},
};
use cap_std::fs::Dir;
use std::path::Path;

/// All interpreter objects stay pinned without write/delete sharing. Entry
/// binding retains physical volume ancestors; grants borrow this owner and its
/// unique profile, so neither can be released while the grant is still used.
pub struct PinnedRuntime {
    package: PinnedPackage,
    info: RuntimeInfo,
}
impl PinnedRuntime {
    /// The resource location comes from Tauri. Inventory authority is compiled
    /// into this Host, never supplied by renderer, Core, a request or a receipt.
    pub fn open(resource_root: &Path) -> Result<Self> {
        if !cfg!(target_arch = "x86_64") {
            return Err(HostError::new("EXPERIMENT_RUNTIME_UNAVAILABLE"));
        }
        let expected = include_str!(concat!(env!("OUT_DIR"), "/experiment-runtime.json"));
        if expected == "{}" {
            return Err(HostError::new("EXPERIMENT_RUNTIME_UNAVAILABLE"));
        }
        Self::pin(&resource_root.join("runtimes").join(RUNTIME_ID), expected)
    }
    fn pin(root: &Path, expected: &str) -> Result<Self> {
        let bad = || HostError::new("EXPERIMENT_RUNTIME_INTEGRITY_FAILED");
        // Validate the source lock, bounded names/sizes and receipt before
        // constructing the native inventory. Recheck with all objects pinned
        // afterwards; a raced edit cannot inherit this earlier discovery.
        let info = experiment_runtime::verify_distribution(root, expected)?;
        let source: serde_json::Value = serde_json::from_str(expected).map_err(|_| bad())?;
        let mut files = std::collections::BTreeMap::new();
        let mut total = expected.len() as u64;
        for (name, item) in source["files"].as_object().ok_or_else(bad)? {
            total = total
                .checked_add(item["bytes"].as_u64().ok_or_else(bad)?)
                .ok_or_else(bad)?;
            files.insert(
                name.clone(),
                item["sha256"].as_str().ok_or_else(bad)?.to_owned(),
            );
        }
        files.insert("runtime.json".into(), hash(expected.as_bytes()));
        let inventory = Inventory {
            files,
            expanded_size: total,
            manifest: "python.exe".into(),
        };
        let directory =
            Dir::open_ambient_dir(root, cap_std::ambient_authority()).map_err(|_| bad())?;
        let package = PinnedPackage::from_inventory(&directory, &inventory).map_err(|_| bad())?;
        experiment_runtime::verify_distribution(root, expected)?;
        Ok(Self { package, info })
    }
    #[cfg(test)]
    pub(crate) fn pin_for_probe(root: &Path, expected: &str) -> Result<Self> {
        Self::pin(root, expected)
    }
    pub fn info(&self) -> &RuntimeInfo {
        &self.info
    }
    /// Only this fixed verified executable can be selected. The guard pins
    /// physical volume ancestors and native identity. The process factory also
    /// checks the actual suspended image before permitting resume.
    pub fn bind_entry(&self) -> Result<BoundEntry<'_>> {
        self.package
            .bind_entry("python.exe")
            .map_err(|_| HostError::new("EXPERIMENT_RUNTIME_BINDING_FAILED"))
    }
    pub fn access<'a>(&'a self, profile: &'a Profile) -> Result<PackageAccess<'a>> {
        self.package.access(profile)
    }
    pub(crate) fn access_for_execution<'a>(
        &'a self,
        profile: &'a Profile,
        attempt: &crate::experiment_cleanup::Attempt,
    ) -> Result<PackageAccess<'a>> {
        self.package.access_for_experiment(
            profile,
            attempt,
            crate::experiment_cleanup::GrantKind::Runtime,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(root: &Path) -> String {
        std::fs::create_dir_all(root).unwrap();
        let files = ["python.exe", "python313._pth", "LICENSE.txt"]
            .into_iter()
            .map(|name| {
                std::fs::write(root.join(name), b"locked").unwrap();
                (
                    name,
                    serde_json::json!({"bytes": 6, "sha256": hash(b"locked")}),
                )
            })
            .collect::<std::collections::BTreeMap<_, _>>();
        let expected = serde_json::json!({"schema_version": 1,
            "lock": serde_json::from_str::<serde_json::Value>(include_str!("../../../scripts/experiment-runtime-lock.json")).unwrap(),
            "files": files}).to_string();
        std::fs::write(root.join("runtime.json"), &expected).unwrap();
        expected
    }
    #[test]
    fn pins_exact_files_receipt_and_physical_ancestors_for_the_full_borrow() {
        let temp = tempfile::tempdir().unwrap();
        let parent = temp.path().join("parent");
        let root = parent.join("runtime");
        let expected = fixture(&root);
        let pinned = PinnedRuntime::pin(&root, &expected).unwrap();
        assert_eq!(pinned.info().runtime_id, RUNTIME_ID);
        let bound = pinned.bind_entry().unwrap();
        assert!(bound.path().to_string_lossy().starts_with(r"\\?\Volume{"));
        assert_eq!(std::fs::read(bound.path()).unwrap(), b"locked");
        for name in [
            "python.exe",
            "python313._pth",
            "LICENSE.txt",
            "runtime.json",
        ] {
            assert!(std::fs::write(root.join(name), b"edited").is_err());
            assert!(std::fs::remove_file(root.join(name)).is_err());
        }
        assert!(std::fs::rename(&root, parent.join("moved")).is_err());
        assert!(std::fs::rename(&parent, temp.path().join("moved")).is_err());
        let profile = Profile::create().unwrap();
        let access = pinned.access(&profile).unwrap();
        access.finish().unwrap();
        profile.remove().unwrap();
        drop(bound);
        drop(pinned);
        std::fs::write(root.join("python.exe"), b"unlocked").unwrap();
        std::fs::rename(parent, temp.path().join("moved")).unwrap();
    }
    #[test]
    fn rejects_existing_writers_forgery_and_hardlinks_without_retaining_locks() {
        let temp = tempfile::tempdir().unwrap();
        let expected = fixture(temp.path());
        let exe = temp.path().join("python.exe");
        let writer = std::fs::OpenOptions::new().write(true).open(&exe).unwrap();
        assert!(PinnedRuntime::pin(temp.path(), &expected).is_err());
        drop(writer);
        let outside = tempfile::tempdir().unwrap();
        std::fs::hard_link(&exe, outside.path().join("alias.exe")).unwrap();
        assert!(PinnedRuntime::pin(temp.path(), &expected).is_err());
        std::fs::remove_file(outside.path().join("alias.exe")).unwrap();
        std::fs::write(temp.path().join("runtime.json"), b"forged").unwrap();
        assert!(PinnedRuntime::pin(temp.path(), &expected).is_err());
        std::fs::write(&exe, b"unlocked after rejection").unwrap();
    }
    #[test]
    fn rejects_reparse_ancestors_and_extra_native_modules() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("runtime");
        let expected = fixture(&root);
        std::fs::write(root.join("injected.pyd"), b"extra").unwrap();
        assert!(PinnedRuntime::pin(&root, &expected).is_err());
        std::fs::remove_file(root.join("injected.pyd")).unwrap();
        let alias = temp.path().join("alias");
        let command = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&alias)
            .arg(&root)
            .output()
            .unwrap();
        assert!(command.status.success(), "junction fixture failed");
        assert!(PinnedRuntime::pin(&alias, &expected).is_err());
        std::fs::remove_dir(alias).unwrap();
        assert!(PinnedRuntime::pin(&root, &expected).is_ok());
    }
}
