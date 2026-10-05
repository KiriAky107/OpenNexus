//! Physical, bounded receipts captured from Host-held objects before ACL grants.
//! A stored path is a locator only; reopening must match its original native ID.
use crate::workspace::{HostError, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    os::windows::io::{AsRawHandle, FromRawHandle},
    path::{Component, Path, PathBuf, Prefix},
};
use windows_sys::Win32::Storage::FileSystem::*;

fn failed() -> HostError {
    HostError::new("EXPERIMENT_CLEANUP_OBJECT_CHANGED")
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObjectKind {
    Directory,
    File,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObjectReceipt {
    pub path: PathBuf,
    pub kind: ObjectKind,
    pub volume: u64,
    pub file_id: [u8; 16],
}
pub(crate) struct HeldObject {
    _ancestors: Vec<File>,
    pub(crate) target: File,
}
impl HeldObject {
    pub(crate) fn into_parts(self) -> (Vec<File>, File) {
        (self._ancestors, self.target)
    }
}

fn native_id(file: &File) -> Result<(ObjectKind, u64, [u8; 16])> {
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    let mut id = FILE_ID_INFO::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0 && info.nNumberOfLinks != 1)
        || unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle(),
                FileIdInfo,
                (&mut id as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
        || id.FileId.Identifier.iter().all(|byte| *byte == 0)
    {
        return Err(failed());
    }
    let kind = if info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0 {
        ObjectKind::Directory
    } else {
        ObjectKind::File
    };
    Ok((kind, id.VolumeSerialNumber, id.FileId.Identifier))
}
fn volume_path(file: &File) -> Result<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    let mut buffer = vec![0u16; 32768];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            VOLUME_NAME_GUID,
        )
    } as usize;
    if length == 0 || length >= buffer.len() {
        return Err(failed());
    }
    Ok(PathBuf::from(std::ffi::OsString::from_wide(
        &buffer[..length],
    )))
}
fn open(path: &Path) -> Result<File> {
    open_shared(path, false)
}
fn open_shared(path: &Path, deleting: bool) -> Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .share_mode(
            FILE_SHARE_READ | FILE_SHARE_WRITE | if deleting { FILE_SHARE_DELETE } else { 0 },
        )
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| failed())
}
impl ObjectReceipt {
    /// The volume GUID and native ID prove absence. Path lookup by itself
    /// cannot distinguish deletion from a rename or a replaced directory.
    pub(crate) fn lookup(&self, access: u32) -> Result<Option<HeldObject>> {
        use windows_sys::Win32::Foundation::{
            GetLastError, ERROR_FILE_NOT_FOUND, ERROR_INVALID_PARAMETER, INVALID_HANDLE_VALUE,
        };
        self.validate()?;
        let root = self.path.ancestors().last().ok_or_else(failed)?;
        let hint = open(root)?;
        if native_id(&hint)?.1 != self.volume {
            return Err(failed());
        }
        let hint_id = native_id(&hint)?;
        let mut filesystem = [0u16; 64];
        if unsafe {
            GetVolumeInformationByHandleW(
                hint.as_raw_handle(),
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                filesystem.as_mut_ptr(),
                filesystem.len() as u32,
            )
        } == 0
        {
            return Err(failed());
        }
        let ntfs = filesystem.starts_with(&[b'N' as u16, b'T' as u16, b'F' as u16, b'S' as u16, 0]);
        // NTFS stale file references produce ERROR_INVALID_PARAMETER. Use a
        // lossless 64-bit descriptor only on NTFS, and first prove that the
        // very same descriptor format/options open a known live volume object.
        // Unsupported formats, nonzero high bits and access errors stay unknown.
        if ntfs && (self.file_id[8..] != [0; 8] || hint_id.2[8..] != [0; 8]) {
            return Err(failed());
        }
        let descriptor = |id: [u8; 16]| {
            let mut low = [0u8; 8];
            low.copy_from_slice(&id[..8]);
            FILE_ID_DESCRIPTOR {
                dwSize: std::mem::size_of::<FILE_ID_DESCRIPTOR>() as u32,
                Type: if ntfs { FileIdType } else { ExtendedFileIdType },
                Anonymous: if ntfs {
                    FILE_ID_DESCRIPTOR_0 {
                        FileId: i64::from_le_bytes(low),
                    }
                } else {
                    FILE_ID_DESCRIPTOR_0 {
                        ExtendedFileId: FILE_ID_128 { Identifier: id },
                    }
                },
            }
        };
        let by_id = |descriptor: &FILE_ID_DESCRIPTOR, access| unsafe {
            OpenFileById(
                hint.as_raw_handle(),
                descriptor,
                FILE_READ_ATTRIBUTES | access,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                std::ptr::null(),
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            )
        };
        let control = by_id(&descriptor(hint_id.2), 0);
        if control == INVALID_HANDLE_VALUE {
            return Err(failed());
        }
        let control = unsafe { File::from_raw_handle(control) };
        if native_id(&control)? != hint_id {
            return Err(failed());
        }
        let target_descriptor = descriptor(self.file_id);
        let raw = by_id(&target_descriptor, 0);
        if raw == INVALID_HANDLE_VALUE {
            let error = unsafe { GetLastError() };
            if error != ERROR_FILE_NOT_FOUND && !(ntfs && error == ERROR_INVALID_PARAMETER) {
                return Err(failed());
            }
            // A replacement at the old locator is also a conflict; it must
            // never be touched or used as an identity for the missing object.
            if !matches!(std::fs::symlink_metadata(&self.path), Err(error) if error.kind() == std::io::ErrorKind::NotFound)
            {
                return Err(failed());
            }
            return Ok(None);
        }
        let target = unsafe { File::from_raw_handle(raw) };
        if native_id(&target)? != (self.kind, self.volume, self.file_id)
            || volume_path(&target)? != self.path
        {
            return Err(failed());
        }
        // Validate and hold every physical ancestor too. A reparse point at
        // the old name cannot redirect any subsequent capability operation.
        let mut ancestors = Vec::new();
        for parent in self.path.ancestors().skip(1) {
            let file = open(parent)?;
            if native_id(&file)?.0 != ObjectKind::Directory {
                return Err(failed());
            }
            ancestors.push(file);
        }
        // A FILE_OPEN_BY_FILE_ID handle has no directory-entry context and
        // NTFS refuses disposition on it. Keep the discovery handle live,
        // then open the same physical name with requested rights and verify
        // the complete native ID again. A replaced name is never adopted.
        use std::os::windows::fs::OpenOptionsExt;
        let named = std::fs::OpenOptions::new()
            .access_mode(FILE_READ_ATTRIBUTES | access)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&self.path)
            .map_err(|_| failed())?;
        if native_id(&named)? != (self.kind, self.volume, self.file_id)
            || volume_path(&named)? != self.path
        {
            return Err(failed());
        }
        drop(target);
        let target = named;
        let at_path = open_shared(&self.path, access & DELETE != 0)?;
        if native_id(&at_path)? != (self.kind, self.volume, self.file_id) {
            return Err(failed());
        }
        Ok(Some(HeldObject {
            _ancestors: ancestors,
            target,
        }))
    }
    pub(crate) fn validate(&self) -> Result<()> {
        use std::os::windows::ffi::OsStrExt;
        if self.path.as_os_str().encode_wide().count() >= 32767
            || self.file_id.iter().all(|byte| *byte == 0)
        {
            return Err(failed());
        }
        let mut components = self.path.components();
        let Some(Component::Prefix(prefix)) = components.next() else {
            return Err(failed());
        };
        let Prefix::Verbatim(volume) = prefix.kind() else {
            return Err(failed());
        };
        let volume = volume
            .to_str()
            .and_then(|value| value.strip_prefix("Volume{"))
            .and_then(|value| value.strip_suffix('}'))
            .ok_or_else(failed)?;
        uuid::Uuid::parse_str(volume).map_err(|_| failed())?;
        if components.next() != Some(Component::RootDir) || components.any(|part| !matches!(part, Component::Normal(name) if !name.is_empty() && !name.to_string_lossy().contains([':', '\0']))) { return Err(failed()); }
        Ok(())
    }
    pub(crate) fn capture(file: &File) -> Result<Self> {
        let receipt = Self::from_held(file)?;
        // Confirm the locator through physical ancestors while the original
        // object remains held, rather than trusting string canonicalization.
        let _held = receipt.pin()?;
        Ok(receipt)
    }
    /// Identity for a child opened without following links through an already
    /// held original directory capability. Does not open a second delete pin.
    pub(crate) fn from_held(file: &File) -> Result<Self> {
        let (kind, volume, file_id) = native_id(file)?;
        let receipt = Self {
            path: volume_path(file)?,
            kind,
            volume,
            file_id,
        };
        receipt.validate()?;
        Ok(receipt)
    }
    pub(crate) fn directory(path: &Path) -> Result<(Self, HeldObject)> {
        if !path.is_absolute() {
            return Err(failed());
        }
        let mut ancestors = Vec::new();
        for parent in path.ancestors().skip(1) {
            let file = open(parent)?;
            if native_id(&file)?.0 != ObjectKind::Directory {
                return Err(failed());
            }
            ancestors.push(file);
        }
        let target = open(path)?;
        let receipt = Self::capture(&target)?;
        if receipt.kind != ObjectKind::Directory {
            return Err(failed());
        }
        Ok((
            receipt,
            HeldObject {
                _ancestors: ancestors,
                target,
            },
        ))
    }
    pub(crate) fn pin(&self) -> Result<HeldObject> {
        self.validate()?;
        let mut ancestors = Vec::new();
        for parent in self.path.ancestors().skip(1) {
            let file = open(parent)?;
            if native_id(&file)?.0 != ObjectKind::Directory {
                return Err(failed());
            }
            ancestors.push(file);
        }
        let held = HeldObject {
            _ancestors: ancestors,
            target: open(&self.path)?,
        };
        if native_id(&held.target)? != (self.kind, self.volume, self.file_id) {
            return Err(failed());
        }
        Ok(held)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_lookup_distinguishes_deletion_from_renames_and_replacements() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("owned.txt");
        std::fs::write(&path, b"owned bytes").unwrap();
        let receipt = ObjectReceipt::capture(&open(&path).unwrap()).unwrap();
        let held = receipt.lookup(READ_CONTROL | WRITE_DAC).unwrap().unwrap();
        assert_eq!(native_id(&held.target).unwrap().2, receipt.file_id);
        drop(held);
        std::fs::rename(&path, root.path().join("moved.txt")).unwrap();
        assert!(
            receipt.lookup(0).is_err(),
            "missing locator must not mean deleted object"
        );
        std::fs::remove_file(root.path().join("moved.txt")).unwrap();
        assert!(receipt.lookup(0).unwrap().is_none());
        std::fs::write(&path, b"unrelated replacement").unwrap();
        assert!(receipt.lookup(0).is_err());
        assert_eq!(std::fs::read(path).unwrap(), b"unrelated replacement");
    }
    #[test]
    fn receipts_roundtrip_hold_native_ancestors_and_reject_replacements() {
        let root = tempfile::tempdir().unwrap();
        let parent = root.path().join("parent");
        std::fs::create_dir(&parent).unwrap();
        let path = parent.join("source.py");
        std::fs::write(&path, b"original").unwrap();
        let file = open(&path).unwrap();
        let receipt = ObjectReceipt::capture(&file).unwrap();
        let decoded: ObjectReceipt =
            serde_json::from_str(&serde_json::to_string(&receipt).unwrap()).unwrap();
        assert_eq!(decoded, receipt);
        let held = decoded.pin().unwrap();
        assert!(std::fs::rename(&parent, root.path().join("moved")).is_err());
        drop(file);
        drop(held);
        std::fs::rename(&path, parent.join("old.py")).unwrap();
        std::fs::write(&path, b"unrelated replacement").unwrap();
        assert_eq!(
            decoded.pin().err().unwrap().code,
            "EXPERIMENT_CLEANUP_OBJECT_CHANGED"
        );
        assert_eq!(std::fs::read(&path).unwrap(), b"unrelated replacement");
        assert_eq!(std::fs::read(parent.join("old.py")).unwrap(), b"original");
    }
    #[test]
    fn refuses_hardlinks_reparse_ancestors_and_forged_locators() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("entry.py");
        std::fs::write(&path, b"preserve").unwrap();
        std::fs::hard_link(&path, root.path().join("alias.py")).unwrap();
        assert!(ObjectReceipt::capture(&open(&path).unwrap()).is_err());
        std::fs::remove_file(root.path().join("alias.py")).unwrap();
        let mut receipt = ObjectReceipt::capture(&open(&path).unwrap()).unwrap();
        receipt.path = PathBuf::from(r"C:\entry.py");
        assert!(receipt.pin().is_err());
        receipt.path = PathBuf::from(r"\\?\Volume{invalid}\entry.py");
        assert!(receipt.validate().is_err());
        let directory = root.path().join("directory");
        std::fs::create_dir(&directory).unwrap();
        let alias = root.path().join("link");
        if std::os::windows::fs::symlink_dir(&directory, &alias).is_ok() {
            let mut value = ObjectReceipt::directory(&directory).unwrap().0;
            value.path = ObjectReceipt::directory(root.path())
                .unwrap()
                .0
                .path
                .join("link");
            assert!(value.pin().is_err());
            assert!(ObjectReceipt::directory(&alias).is_err());
            std::fs::remove_dir(alias).unwrap();
        }
        assert_eq!(std::fs::read(path).unwrap(), b"preserve");
    }
}
