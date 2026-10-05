//! Physical, bounded receipts captured from Host-held objects before ACL grants.
//! A stored path is a locator only; reopening must match its original native ID.
use crate::workspace::{HostError, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    os::windows::io::AsRawHandle,
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
    target: File,
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
    use std::os::windows::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|_| failed())
}
impl ObjectReceipt {
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
        let (kind, volume, file_id) = native_id(file)?;
        let receipt = Self {
            path: volume_path(file)?,
            kind,
            volume,
            file_id,
        };
        // Confirm the locator through physical ancestors while the original
        // object remains held, rather than trusting string canonicalization.
        let _held = receipt.pin()?;
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
