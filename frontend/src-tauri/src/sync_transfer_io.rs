//! Private immutable transfer spools. Call only on the owned blocking worker.
use crate::{sync_client::SyncError, workspace::Result as CheckResult};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Seek, SeekFrom, Write},
    path::PathBuf,
};

type Result<T> = std::result::Result<T, SyncError>;
const CHUNK: usize = 64 * 1024;
const UPLOAD_PART: u64 = 1024 * 1024;
const MAX_BYTES: u64 = 100 * 1024 * 1024;

/// Nonserializable handles never cross IPC or publish a queue/cursor/receipt.
pub(crate) struct Upload {
    file: fs::File,
    size: u64,
}
impl Upload {
    pub(crate) fn open(
        path: PathBuf,
        digest: &str,
        size: u64,
        check: &dyn Fn() -> CheckResult<()>,
    ) -> Result<Self> {
        check()?;
        if size > MAX_BYTES {
            return Err(SyncError::new("SYNC_SPOOL_CORRUPT"));
        }
        let file = crate::payloads::open_verified_checked(&path, digest, size, check)?;
        check()?;
        Ok(Self { file, size })
    }
    pub(crate) fn read_part(
        mut self,
        offset: u64,
        check: &dyn Fn() -> CheckResult<()>,
    ) -> Result<(Self, Vec<u8>)> {
        check()?;
        if offset >= self.size || self.file.metadata()?.len() != self.size {
            return Err(SyncError::new("SYNC_SPOOL_CORRUPT"));
        }
        self.file.seek(SeekFrom::Start(offset))?;
        let limit = (self.size - offset).min(UPLOAD_PART) as usize;
        let mut bytes = vec![0; limit];
        let mut filled = 0;
        while filled < limit {
            check()?;
            let end = (filled + CHUNK).min(limit);
            let count = self.file.read(&mut bytes[filled..end])?;
            check()?;
            if count == 0 {
                return Err(SyncError::new("SYNC_SPOOL_CORRUPT"));
            }
            filled += count;
        }
        Ok((self, bytes))
    }
}

pub(crate) struct Download {
    file: tempfile::NamedTempFile,
    target: PathBuf,
    digest: String,
    size: u64,
    length: u64,
    hasher: Sha256,
}
impl Download {
    /// None means the existing original cache was independently verified.
    pub(crate) fn prepare(
        target: PathBuf,
        digest: String,
        size: u64,
        check: &dyn Fn() -> CheckResult<()>,
    ) -> Result<Option<Self>> {
        check()?;
        if size > MAX_BYTES {
            return Err(SyncError::new("SYNC_OBJECT_CORRUPT"));
        }
        if target.try_exists()? {
            crate::payloads::verify_checked(&target, &digest, size, check)?;
            check()?;
            return Ok(None);
        }
        let parent = target
            .parent()
            .ok_or_else(|| SyncError::new("SYNC_SPOOL_FAILED"))?;
        let file = tempfile::NamedTempFile::new_in(parent)?;
        check()?;
        Ok(Some(Self {
            file,
            target,
            digest,
            size,
            length: 0,
            hasher: Sha256::new(),
        }))
    }
    pub(crate) fn append(
        mut self,
        bytes: &[u8],
        check: &dyn Fn() -> CheckResult<()>,
    ) -> Result<Self> {
        check()?;
        if bytes.len() as u64 > self.size - self.length {
            return Err(SyncError::new("SYNC_OBJECT_CORRUPT"));
        }
        for chunk in bytes.chunks(CHUNK) {
            check()?;
            self.file.write_all(chunk)?;
            self.hasher.update(chunk);
            self.length += chunk.len() as u64;
            check()?;
        }
        Ok(self)
    }
    pub(crate) fn length(&self) -> u64 {
        self.length
    }
    pub(crate) fn finish(self, check: &dyn Fn() -> CheckResult<()>) -> Result<()> {
        check()?;
        if self.length != self.size || format!("{:x}", self.hasher.finalize()) != self.digest {
            return Err(SyncError::new("SYNC_OBJECT_CORRUPT"));
        }
        self.file.as_file().sync_all()?;
        check()?;
        self.file
            .persist_noclobber(&self.target)
            .map_err(|_| SyncError::new("SYNC_SPOOL_FAILED"))?;
        #[cfg(unix)]
        fs::File::open(
            self.target
                .parent()
                .ok_or_else(|| crate::workspace::HostError::new("UNSAFE_PATH"))?,
        )?
        .sync_all()?;
        check()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::{hash, HostError};
    use std::cell::Cell;

    #[test]
    fn sync_transfer_private_upload_verification_can_stop_without_returning_a_handle() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("original");
        let bytes = vec![7; 256 * 1024];
        fs::write(&path, &bytes).unwrap();
        let checks = Cell::new(0);
        let check = || {
            checks.set(checks.get() + 1);
            if checks.get() == 5 {
                Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
            } else {
                Ok(())
            }
        };
        assert_eq!(
            Upload::open(path.clone(), &hash(&bytes), bytes.len() as u64, &check)
                .err()
                .unwrap()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
        assert_eq!(checks.get(), 5);
        assert_eq!(fs::read(path).unwrap(), bytes);
    }

    #[test]
    fn sync_transfer_upload_reads_exact_resumed_original_parts_and_refuses_stop_or_truncation() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("original");
        let bytes = (0..(1024 * 1024 + 37))
            .map(|i| (i % 251) as u8)
            .collect::<Vec<_>>();
        fs::write(&path, &bytes).unwrap();
        let upload =
            Upload::open(path.clone(), &hash(&bytes), bytes.len() as u64, &|| Ok(())).unwrap();
        let (upload, part) = upload.read_part(13, &|| Ok(())).unwrap();
        assert_eq!(part, bytes[13..13 + 1024 * 1024]);
        let (_, part) = upload.read_part(13 + 1024 * 1024, &|| Ok(())).unwrap();
        assert_eq!(part, bytes[13 + 1024 * 1024..]);
        let upload =
            Upload::open(path.clone(), &hash(&bytes), bytes.len() as u64, &|| Ok(())).unwrap();
        assert_eq!(
            upload
                .read_part(0, &|| Err(HostError::new("SYNC_FOREGROUND_CHANGED")))
                .err()
                .unwrap()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
        let upload =
            Upload::open(path.clone(), &hash(&bytes), bytes.len() as u64, &|| Ok(())).unwrap();
        fs::write(&path, b"later truncated bytes").unwrap();
        assert_eq!(
            upload.read_part(0, &|| Ok(())).err().unwrap().code,
            "SYNC_SPOOL_CORRUPT"
        );
        assert_eq!(fs::read(path).unwrap(), b"later truncated bytes");
    }

    #[test]
    fn sync_transfer_canceled_download_drops_private_bytes_and_never_publishes_cache() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("original");
        let bytes = vec![9; 256 * 1024];
        let download =
            Download::prepare(target.clone(), hash(&bytes), bytes.len() as u64, &|| Ok(()))
                .unwrap()
                .unwrap();
        let checks = Cell::new(0);
        let check = || {
            checks.set(checks.get() + 1);
            if checks.get() == 5 {
                Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
            } else {
                Ok(())
            }
        };
        assert_eq!(
            download.append(&bytes, &check).err().unwrap().code,
            "SYNC_FOREGROUND_CHANGED"
        );
        assert!(!target.exists());
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn sync_transfer_verified_cache_is_reused_but_corrupt_or_unfinished_bytes_never_replace_it() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("original");
        let bytes = b"original bytes\r\n";
        let download =
            Download::prepare(target.clone(), hash(bytes), bytes.len() as u64, &|| Ok(()))
                .unwrap()
                .unwrap();
        let download = download.append(bytes, &|| Ok(())).unwrap();
        download.finish(&|| Ok(())).unwrap();
        assert!(
            Download::prepare(target.clone(), hash(bytes), bytes.len() as u64, &|| Ok(()))
                .unwrap()
                .is_none()
        );
        assert!(Download::prepare(
            target.clone(),
            hash(b"different"),
            bytes.len() as u64,
            &|| Ok(())
        )
        .is_err());
        let other = root.path().join("unfinished");
        let download =
            Download::prepare(other.clone(), hash(bytes), bytes.len() as u64, &|| Ok(()))
                .unwrap()
                .unwrap();
        let download = download.append(b"short", &|| Ok(())).unwrap();
        assert_eq!(
            download.finish(&|| Ok(())).unwrap_err().code,
            "SYNC_OBJECT_CORRUPT"
        );
        assert!(!other.exists());
        assert_eq!(fs::read(target).unwrap(), bytes);
    }
}
