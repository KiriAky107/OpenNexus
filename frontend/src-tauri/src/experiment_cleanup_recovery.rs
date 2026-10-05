//! Main-window recovery preflight. All scope comes from durable Host receipts;
//! no caller supplies filesystem paths, profile names or native process IDs.
use crate::{
    experiment_cleanup::{CleanupObjects, CleanupPhase, CleanupStatus, MAX_GRANTS},
    experiment_cleanup_lease::Lease,
    experiment_cleanup_objects::{HeldObject, ObjectKind, ObjectReceipt},
    extension_container::RecoverySid,
    workspace::{HostError, Result},
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions, OpenOptionsExt};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{fs::File, os::windows::io::AsRawHandle};
use windows_sys::Win32::Storage::FileSystem::*;

fn unverified() -> HostError {
    HostError::new("EXPERIMENT_CLEANUP_UNVERIFIED")
}
#[derive(Debug, Clone, Serialize)]
pub struct CleanupReview {
    pub fingerprint: String,
    pub operation_id: String,
    pub phase: CleanupPhase,
    pub profile_present: bool,
    pub temporary_objects: usize,
    pub borrowed_objects: usize,
}
struct TemporaryObject {
    receipt: ObjectReceipt,
    file: File,
}
pub(crate) struct Preflight {
    review: CleanupReview,
    sid: Option<RecoverySid>,
    profile: Option<HeldObject>,
    source: Vec<TemporaryObject>,
    _source_ancestors: Vec<File>,
    runtime: Vec<HeldObject>,
}

/// Enumerate only the original Host-created source-copy tree. Open every child
/// without following links and hold each native object through disposition.
/// cap-std's Windows remove_dir_all closes the directory then deletes by path;
/// recovery deliberately uses native handle disposition instead.
fn source_tree(root: &ObjectReceipt) -> Result<(Vec<File>, Vec<TemporaryObject>)> {
    let Some(held) = root.lookup(DELETE | FILE_LIST_DIRECTORY | READ_CONTROL | WRITE_DAC)? else {
        return Ok((Vec::new(), Vec::new()));
    };
    let (ancestors, target) = held.into_parts();
    let mut source = vec![TemporaryObject {
        receipt: root.clone(),
        file: target,
    }];
    let mut index = 0;
    while index < source.len() {
        if source[index].receipt.kind == ObjectKind::Directory {
            let dir = Dir::from_std_file(source[index].file.try_clone().map_err(|_| unverified())?);
            for entry in dir.entries().map_err(|_| unverified())? {
                if source.len() >= MAX_GRANTS {
                    return Err(unverified());
                }
                let entry = entry.map_err(|_| unverified())?;
                let mut options = OpenOptions::new();
                options
                    .access_mode(
                        FILE_READ_ATTRIBUTES
                            | FILE_LIST_DIRECTORY
                            | DELETE
                            | READ_CONTROL
                            | WRITE_DAC,
                    )
                    .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                    .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                    .follow(FollowSymlinks::No);
                let file = dir
                    .open_with(entry.file_name(), &options)
                    .map_err(|_| unverified())?
                    .into_std();
                let receipt = ObjectReceipt::from_held(&file)?;
                if !receipt.path.starts_with(&root.path) || receipt.volume != root.volume {
                    return Err(unverified());
                }
                source.push(TemporaryObject { receipt, file });
            }
        }
        index += 1;
    }
    Ok((ancestors, source))
}
impl Preflight {
    pub(crate) fn inspect(token: &str, record: &CleanupStatus, lease: &Lease) -> Result<Self> {
        let objects: &CleanupObjects = record.objects.as_ref().ok_or_else(unverified)?;
        let job = record.job.as_ref().ok_or_else(unverified)?;
        job.validate(token)?;
        job.stopped_under_lease(lease)?;
        if record.phase == CleanupPhase::Creating {
            return Err(unverified());
        }
        let mut value = Self {
            review: CleanupReview {
                fingerprint: String::new(),
                operation_id: record.operation_id.clone(),
                phase: record.phase,
                profile_present: false,
                temporary_objects: 0,
                borrowed_objects: 0,
            },
            sid: None,
            profile: None,
            source: Vec::new(),
            _source_ancestors: Vec::new(),
            runtime: Vec::new(),
        };
        let mut runtime_presence = Vec::new();
        if record.phase == CleanupPhase::Created {
            let profile = objects.profile.as_ref().ok_or_else(unverified)?;
            let sid = RecoverySid::derive(
                &record.profile_name,
                objects.profile_sid.as_deref().ok_or_else(unverified)?,
            )?;
            value.profile = profile.lookup(0)?;
            if let Some(_held) = &value.profile {
                let folder = sid.folder()?;
                let root = folder.parent().ok_or_else(unverified)?;
                let (sdk, _pin) = ObjectReceipt::directory(root)?;
                if sdk != *profile {
                    return Err(unverified());
                }
            } else if !crate::experiment_registry::profile_is_absent(&record.profile_name)? {
                return Err(unverified());
            }
            value.review.profile_present = value.profile.is_some();
            if let Some(source) = &objects.source {
                let (ancestors, source) = source_tree(source)?;
                value._source_ancestors = ancestors;
                value.source = source;
            }
            if let Some(grants) = &objects.source_grants {
                for grant in grants {
                    if !value.source.iter().any(|item| item.receipt == *grant)
                        && grant.lookup(0)?.is_some()
                    {
                        return Err(unverified());
                    }
                }
            }
            if let Some(runtime) = &objects.runtime {
                // Even a pre-grant runtime binding must not silently adopt a
                // replacement. No runtime bytes are ever deleted by recovery.
                let _pin = runtime.lookup(0)?;
            }
            if let Some(grants) = &objects.runtime_grants {
                for grant in grants {
                    let held = grant.lookup(READ_CONTROL | WRITE_DAC)?;
                    runtime_presence.push(held.is_some());
                    if let Some(held) = held {
                        value.runtime.push(held);
                    }
                }
            }
            value.sid = Some(sid);
        }
        value.review.temporary_objects = value.source.len();
        value.review.borrowed_objects = value.runtime.len();
        value
            .source
            .sort_by(|left, right| left.receipt.path.cmp(&right.receipt.path));
        // Bind the approval to both the immutable journal and the native scope
        // displayed now. New files, deletion or a partially completed retry
        // require a new review, rather than broadening an old confirmation.
        let scope = serde_json::to_vec(&(
            token,
            record,
            value.review.profile_present,
            value
                .source
                .iter()
                .map(|item| &item.receipt)
                .collect::<Vec<_>>(),
            runtime_presence,
        ))
        .map_err(|_| unverified())?;
        let mut hash = Sha256::new();
        hash.update(b"opennexus-cleanup-review-v1\0");
        hash.update(scope);
        value.review.fingerprint = format!("{:x}", hash.finalize());
        Ok(value)
    }
    pub(crate) fn review(&self) -> CleanupReview {
        self.review.clone()
    }
    pub(crate) fn apply(mut self, record: &CleanupStatus, lease: &Lease) -> Result<RecoveryProof> {
        record
            .job
            .as_ref()
            .ok_or_else(unverified)?
            .stopped_under_lease(lease)?;
        if let Some(sid) = &self.sid {
            for item in &self.source {
                if ObjectReceipt::from_held(&item.file)? != item.receipt {
                    return Err(unverified());
                }
            }
            for held in &self.runtime {
                sid.revoke(&held.target)?;
            }
            // Every temporary descendant is pinned. Handle disposition cannot
            // follow a reparse point or delete a replacement at the old name.
            self.source
                .sort_by_key(|item| std::cmp::Reverse(item.receipt.path.components().count()));
            for item in self.source.drain(..) {
                if ObjectReceipt::from_held(&item.file)? != item.receipt {
                    return Err(unverified());
                }
                sid.revoke(&item.file)?;
                let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
                if unsafe {
                    SetFileInformationByHandle(
                        item.file.as_raw_handle(),
                        FileDispositionInfo,
                        (&disposition as *const FILE_DISPOSITION_INFO).cast(),
                        std::mem::size_of::<FILE_DISPOSITION_INFO>() as u32,
                    )
                } == 0
                {
                    #[cfg(test)]
                    eprintln!(
                        "owned cleanup disposition {:?}: {}",
                        item.receipt.path,
                        unsafe { windows_sys::Win32::Foundation::GetLastError() }
                    );
                    return Err(HostError::new("EXPERIMENT_SOURCE_CLEANUP_FAILED"));
                }
                drop(item);
            }
            if self.profile.is_some() {
                sid.remove_verified_profile()?;
            }
        }
        // Close all original directory pins before verifying completed native
        // deletion. Missing by pathname alone is never accepted as proof.
        self.profile.take();
        let objects = record.objects.as_ref().ok_or_else(unverified)?;
        for receipt in [&objects.source, &objects.profile].into_iter().flatten() {
            if receipt.lookup(0)?.is_some() {
                return Err(unverified());
            }
        }
        if !crate::experiment_registry::profile_is_absent(&record.profile_name)? {
            return Err(unverified());
        }
        record
            .job
            .as_ref()
            .ok_or_else(unverified)?
            .stopped_under_lease(lease)?;
        Ok(RecoveryProof { _closed: () })
    }
}
/// Only this native protocol can construct the acknowledgement. Journal
/// deletion must consume it while retaining the exact lease and row snapshot.
pub(crate) struct RecoveryProof {
    _closed: (),
}
