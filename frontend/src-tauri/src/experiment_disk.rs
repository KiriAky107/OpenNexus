//! File-handle based accounting for the complete private container directory.
//! Named streams count too. Inspection errors stop the run; they never mean zero.
use crate::{
    experiment_policy::ValidatedLimits,
    workspace::{HostError, Result},
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions as CapOptions, OpenOptionsExt as CapOptionsExt};
use std::{
    fs::{File, OpenOptions},
    mem::{offset_of, size_of},
    os::windows::{
        fs::{MetadataExt, OpenOptionsExt},
        io::AsRawHandle,
    },
    path::Path,
};
use windows_sys::Win32::{
    Foundation::{GetLastError, ERROR_HANDLE_EOF, ERROR_INSUFFICIENT_BUFFER, ERROR_MORE_DATA},
    Storage::FileSystem::*,
};

fn unavailable() -> HostError {
    HostError::new("EXPERIMENT_DISK_INSPECTION_FAILED")
}
fn exceeded() -> HostError {
    HostError::new("EXPERIMENT_DISK_LIMIT_EXCEEDED")
}

pub(crate) struct DiskBudget {
    root: Dir,
    bytes: u64,
    objects: u32,
}
impl DiskBudget {
    pub(crate) fn open(root: &Path, limits: &ValidatedLimits) -> Result<Self> {
        if !root.is_absolute() {
            return Err(unavailable());
        }
        let file = OpenOptions::new()
            .access_mode(FILE_GENERIC_READ)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(root)
            .map_err(|_| unavailable())?;
        inspect_object(&file)?;
        if !file.metadata().map_err(|_| unavailable())?.is_dir() {
            return Err(unavailable());
        }
        let value = Self {
            root: Dir::from_std_file(file),
            bytes: limits.disk_bytes(),
            objects: limits.objects(),
        };
        value.usage()?;
        Ok(value)
    }
    pub(crate) fn usage(&self) -> Result<u64> {
        let mut total = 0u64;
        let mut objects = 1u32; // Include the pinned root.
        let mut streams = 0u32;
        let mut pending = vec![self.root.try_clone().map_err(|_| unavailable())?];
        let mut options = CapOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
        while let Some(directory) = pending.pop() {
            let file = directory
                .try_clone()
                .map_err(|_| unavailable())?
                .into_std_file();
            inspect_object(&file)?;
            total = total
                .checked_add(stream_bytes(&file, &mut streams, self.objects * 16)?)
                .ok_or_else(exceeded)?;
            if total > self.bytes {
                return Err(exceeded());
            }
            for entry in directory.entries().map_err(|_| unavailable())? {
                let entry = entry.map_err(|_| unavailable())?;
                // Bound the traversal queue as entries are discovered, before
                // opening or enqueueing them. A directory flood is bounded too.
                objects += 1;
                if objects > self.objects {
                    return Err(exceeded());
                }
                // Open relative to the directory capability, never by a raced
                // ambient path. The opened object's attributes decide its type.
                let file = entry
                    .open_with(&options)
                    .map_err(|_| unavailable())?
                    .into_std();
                let metadata = inspect_object(&file)?;
                if metadata.is_dir() {
                    pending.push(Dir::from_std_file(file));
                } else {
                    total = total
                        .checked_add(stream_bytes(&file, &mut streams, self.objects * 16)?)
                        .ok_or_else(exceeded)?;
                    if total > self.bytes {
                        return Err(exceeded());
                    }
                }
            }
        }
        Ok(total)
    }
}

fn inspect_object(file: &File) -> Result<std::fs::Metadata> {
    let metadata = file.metadata().map_err(|_| unavailable())?;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || !(metadata.is_file() || metadata.is_dir())
    {
        return Err(unavailable());
    }
    if metadata.is_file() {
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.nNumberOfLinks != 1
        {
            return Err(unavailable());
        }
    }
    Ok(metadata)
}

fn stream_bytes(file: &File, count: &mut u32, limit: u32) -> Result<u64> {
    // FILE_STREAM_INFO requires eight-byte alignment. Query by the pinned handle
    // so rename/replacement cannot redirect stream enumeration to another object.
    let mut words = vec![0u64; 512];
    loop {
        if unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle(),
                FileStreamInfo,
                words.as_mut_ptr().cast(),
                (words.len() * 8) as u32,
            )
        } != 0
        {
            break;
        }
        match unsafe { GetLastError() } {
            ERROR_HANDLE_EOF => return Ok(0),
            ERROR_MORE_DATA | ERROR_INSUFFICIENT_BUFFER if words.len() < 131072 => {
                words.resize(words.len() * 2, 0)
            }
            _ => return Err(unavailable()),
        }
    }
    let length = words.len() * 8;
    let mut offset = 0usize;
    let mut total = 0u64;
    loop {
        if offset % 8 != 0 || offset + size_of::<FILE_STREAM_INFO>() > length {
            return Err(unavailable());
        }
        let info = unsafe {
            &*words
                .as_ptr()
                .cast::<u8>()
                .add(offset)
                .cast::<FILE_STREAM_INFO>()
        };
        let name_end = offset
            .checked_add(offset_of!(FILE_STREAM_INFO, StreamName))
            .and_then(|n| n.checked_add(info.StreamNameLength as usize))
            .ok_or_else(unavailable)?;
        if info.StreamNameLength == 0
            || info.StreamNameLength % 2 != 0
            || name_end > length
            || info.StreamSize < 0
            || info.StreamAllocationSize < 0
        {
            return Err(unavailable());
        }
        *count += 1;
        if *count > limit {
            return Err(exceeded());
        }
        // Count preallocated storage as well as logical bytes, including ADS.
        total = total
            .checked_add(info.StreamSize.max(info.StreamAllocationSize) as u64)
            .ok_or_else(exceeded)?;
        if info.NextEntryOffset == 0 {
            break;
        }
        let next = offset
            .checked_add(info.NextEntryOffset as usize)
            .ok_or_else(unavailable)?;
        if next < name_end {
            return Err(unavailable());
        }
        offset = next;
    }
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::experiment_policy::ExecutionLimits;
    fn limits() -> ValidatedLimits {
        ExecutionLimits {
            disk_mib: 8,
            output_mib: 1,
            ..Default::default()
        }
        .validate()
        .unwrap()
    }
    #[test]
    fn counts_file_and_directory_streams_outside_temp() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("AC")).unwrap();
        std::fs::write(root.path().join("AC/file"), b"main").unwrap();
        let disk = DiskBudget::open(root.path(), &limits()).unwrap();
        let baseline = disk.usage().unwrap();
        std::fs::write(
            root.path().join("AC/file:hidden"),
            vec![0u8; 5 * 1024 * 1024],
        )
        .unwrap();
        assert!(disk.usage().unwrap() >= baseline + 5 * 1024 * 1024);
        std::fs::write(
            root.path().join("AC:directory-stream"),
            vec![0u8; 5 * 1024 * 1024],
        )
        .unwrap();
        assert_eq!(
            disk.usage().unwrap_err().code,
            "EXPERIMENT_DISK_LIMIT_EXCEEDED"
        );
    }
    #[test]
    fn rejects_hardlinks_and_entry_floods() {
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::NamedTempFile::new().unwrap();
        std::fs::hard_link(outside.path(), root.path().join("linked")).unwrap();
        assert_eq!(
            DiskBudget::open(root.path(), &limits()).err().unwrap().code,
            "EXPERIMENT_DISK_INSPECTION_FAILED"
        );
        std::fs::remove_file(root.path().join("linked")).unwrap();
        let small = ExecutionLimits {
            objects: 32,
            ..Default::default()
        }
        .validate()
        .unwrap();
        let disk = DiskBudget::open(root.path(), &small).unwrap();
        for index in 0..32 {
            std::fs::write(root.path().join(format!("file-{index}")), []).unwrap();
        }
        assert_eq!(
            disk.usage().unwrap_err().code,
            "EXPERIMENT_DISK_LIMIT_EXCEEDED"
        );
        drop(disk);
        for index in 0..32 {
            std::fs::remove_file(root.path().join(format!("file-{index}"))).unwrap();
        }
        let disk = DiskBudget::open(root.path(), &small).unwrap();
        for index in 0..32 {
            std::fs::create_dir(root.path().join(format!("directory-{index}"))).unwrap();
        }
        assert_eq!(
            disk.usage().unwrap_err().code,
            "EXPERIMENT_DISK_LIMIT_EXCEEDED"
        );
    }
}
