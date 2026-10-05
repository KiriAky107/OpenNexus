//! Host-owned, immutable source copies outside the container's writable profile.
use crate::{
    experiment_store::ClaimedRun,
    extension_container::Profile,
    extension_package::Inventory,
    extension_pinned::{BoundEntry, PackageAccess, PinnedPackage},
    workspace::{HostError, Result},
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};
use std::{io::Write, path::Path};

/// Objects and ancestors stay pinned through the borrowed run. The package is
/// dropped before TempDir, so normal cleanup never races our retained handles.
/// These files are not stored under Profile::folder(): that folder's critical
/// package ACE can grant write access despite an added read-only grant.
pub struct RunSources<'a> {
    package: PinnedPackage,
    _temporary: tempfile::TempDir,
    run: &'a ClaimedRun,
}
pub struct SourceEntry<'a>(BoundEntry<'a>);
impl SourceEntry<'_> {
    pub fn path(&self) -> &Path {
        self.0.launch_path()
    }
}
impl<'a> RunSources<'a> {
    pub(crate) fn is_bound_to(&self, run: &ClaimedRun) -> bool {
        std::ptr::eq(self.run, run)
    }
    /// Uses only already claimed, revalidated Host bytes and Host's fresh temp
    /// directory. No renderer-provided absolute path or existing tree is used.
    pub fn create(run: &'a ClaimedRun) -> Result<Self> {
        Self::create_inner(run, None)
    }
    pub(crate) fn create_for_execution(
        run: &'a ClaimedRun,
        attempt: &crate::experiment_cleanup::Attempt,
    ) -> Result<Self> {
        Self::create_inner(run, Some(attempt))
    }
    fn create_inner(
        run: &'a ClaimedRun,
        attempt: Option<&crate::experiment_cleanup::Attempt>,
    ) -> Result<Self> {
        let bad = || HostError::new("EXPERIMENT_SOURCE_BINDING_FAILED");
        let temporary = tempfile::Builder::new()
            .prefix("opennexus-experiment-input-")
            .tempdir()
            .map_err(|_| bad())?;
        // Record the actual Host-created root before copying or granting bytes.
        if let Some(attempt) = attempt {
            attempt.source_created(temporary.path())?;
        }
        let root = Dir::open_ambient_dir(temporary.path(), cap_std::ambient_authority())
            .map_err(|_| bad())?;
        let summary = run.inputs().summary();
        summary.validate()?;
        for (path, bytes) in run.inputs().files() {
            let relative = Path::new(path);
            let parent = relative.parent().ok_or_else(bad)?;
            root.create_dir_all(parent).map_err(|_| bad())?;
            let mut options = OpenOptions::new();
            options
                .write(true)
                .create_new(true)
                .follow(FollowSymlinks::No);
            let mut file = root.open_with(relative, &options).map_err(|_| bad())?;
            file.write_all(bytes).map_err(|_| bad())?;
            file.sync_all().map_err(|_| bad())?;
        }
        let inventory = Inventory {
            files: std::iter::once(&summary.request.entry)
                .chain(&summary.request.inputs)
                .map(|file| (file.path.clone(), file.hash.clone()))
                .collect(),
            expanded_size: summary.total_bytes as u64,
            manifest: summary.request.entry.path.clone(),
        };
        // Locks every object before rehashing the exact tree. Earlier copies or
        // discovery do not authorize different bytes, links or extra modules.
        let package = PinnedPackage::from_inventory(&root, &inventory).map_err(|_| bad())?;
        Ok(Self {
            package,
            _temporary: temporary,
            run,
        })
    }
    pub fn entry(&self) -> Result<SourceEntry<'_>> {
        self.package
            .bind_entry(&self.run.record().summary.request.entry.path)
            .map(SourceEntry)
            .map_err(|_| HostError::new("EXPERIMENT_SOURCE_BINDING_FAILED"))
    }
    pub fn access<'b>(&'b self, profile: &'b Profile) -> Result<PackageAccess<'b>> {
        self.package.access(profile)
    }
    pub fn finish(self) -> Result<()> {
        let Self {
            package,
            _temporary: temporary,
            run: _,
        } = self;
        drop(package);
        let root = temporary.path().to_owned();
        temporary
            .close()
            .map_err(|_| HostError::new("EXPERIMENT_SOURCE_CLEANUP_FAILED"))?;
        if !matches!(std::fs::symlink_metadata(root),Err(e) if e.kind()==std::io::ErrorKind::NotFound)
        {
            return Err(HostError::new("EXPERIMENT_SOURCE_CLEANUP_FAILED"));
        }
        Ok(())
    }
    #[cfg(test)]
    pub(crate) fn test_root(&self) -> &Path {
        self._temporary.path()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_input::{PreparedInputs, RunRequest, SelectedFile},
        experiment_owner::RunOwner,
        experiment_policy::ExecutionLimits,
        experiment_runtime::RUNTIME_ID,
        workspace::Workspace,
    };
    #[test]
    fn claimed_copies_keep_exact_bytes_and_block_edits_until_cleanup() {
        let _serial = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
        let vault = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(vault.path().join("experiments/中文 #%")).unwrap();
        let path = "experiments/中文 #%/main.py";
        let data = "print('中文')\r\n";
        std::fs::write(vault.path().join(path), data).unwrap();
        let mut ws = Workspace::open(vault.path()).unwrap();
        let file = ws.read(path).unwrap().entry;
        let request = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry: SelectedFile {
                file_id: file.file_id,
                path: file.path,
                hash: file.hash,
                revision: file.revision,
            },
            inputs: vec![],
            limits: ExecutionLimits::default(),
        };
        let inputs = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        let record = ws.experiment_prepare(inputs).unwrap();
        ws.experiment_approve(&request.operation_id, &record.summary.fingerprint)
            .unwrap();
        let owner = RunOwner::claim(&mut ws, &request.operation_id).unwrap();
        let sources = RunSources::create(owner.run()).unwrap();
        let copied_root = sources.test_root().to_owned();
        let entry = sources.entry().unwrap();
        assert_eq!(std::fs::read(entry.path()).unwrap(), data.as_bytes());
        assert!(!std::fs::canonicalize(entry.path())
            .unwrap()
            .starts_with(std::fs::canonicalize(vault.path()).unwrap()));
        std::fs::write(vault.path().join(path), b"print('later edit')").unwrap();
        assert_eq!(std::fs::read(entry.path()).unwrap(), data.as_bytes());
        assert!(std::fs::write(entry.path(), b"changed").is_err());
        assert!(std::fs::remove_file(entry.path()).is_err());
        assert!(std::fs::rename(&copied_root, copied_root.with_extension("moved")).is_err());
        let profile = Profile::create().unwrap();
        assert!(!std::fs::canonicalize(entry.path())
            .unwrap()
            .starts_with(std::fs::canonicalize(profile.folder().unwrap()).unwrap()));
        sources.access(&profile).unwrap().finish().unwrap();
        profile.remove().unwrap();
        drop(entry);
        drop(sources);
        assert!(!copied_root.exists());
        drop(owner);
    }
}
