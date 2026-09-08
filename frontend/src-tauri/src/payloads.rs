//! Immutable payloads are fsynced before any SQLite reference becomes visible.
use crate::workspace::{hash, HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};
impl Workspace {
    pub(crate) fn store_payload(&self, operation: &str, content: &[u8]) -> Result<()> {
        let digest = hash(content);
        let target = self.sync_spool(&digest)?;
        if target.exists() {
            verify(&target, &digest, content.len() as u64)?;
        } else {
            let mut temp = tempfile::NamedTempFile::new_in(target.parent().unwrap())?;
            temp.write_all(content)?;
            temp.as_file().sync_all()?;
            temp.persist_noclobber(&target)
                .map_err(|_| HostError::new("SYNC_SPOOL_FAILED"))?;
            #[cfg(unix)]
            fs::File::open(target.parent().unwrap())?.sync_all()?;
        }
        self.register_payload(operation, &digest, content.len() as u64)
    }
    pub(crate) fn store_payload_file(
        &self,
        operation: &str,
        source: &Path,
        expected: &str,
    ) -> Result<()> {
        let size = self.snapshot_file(source, expected)?;
        self.register_payload(operation, expected, size)
    }
    pub(crate) fn sync_store_file(&self, source: &Path) -> Result<String> {
        let digest = hash_file(source)?;
        self.snapshot_file(source, &digest)?;
        Ok(digest)
    }
    fn snapshot_file(&self, source: &Path, expected: &str) -> Result<u64> {
        let size = fs::metadata(source)?.len();
        if size > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        let mut input = open_verified(source, expected, size).map_err(|error| {
            if error.code == "SYNC_SPOOL_CORRUPT" {
                HostError::new("REVISION_CONFLICT")
            } else {
                error
            }
        })?;
        let target = self.sync_spool(expected)?;
        if target.exists() {
            verify(&target, expected, size)?;
        } else {
            let parent = target
                .parent()
                .ok_or_else(|| HostError::new("UNSAFE_PATH"))?;
            let mut temp = tempfile::NamedTempFile::new_in(parent)?;
            copy_verified(&mut input, &mut temp, expected, size)?;
            temp.as_file().sync_all()?;
            temp.persist_noclobber(&target)
                .map_err(|_| HostError::new("SYNC_SPOOL_FAILED"))?;
            #[cfg(unix)]
            fs::File::open(parent)?.sync_all()?;
        }
        Ok(size)
    }
    fn register_payload(&self, operation: &str, digest: &str, size: u64) -> Result<()> {
        let old: Option<(String, i64)> = self
            .db
            .query_row(
                "SELECT hash,size FROM payloads WHERE operation_id=?1",
                [operation],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if old.is_some_and(|v| v != (digest.to_owned(), size as i64)) {
            return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
        }
        self.db.execute(
            "INSERT OR IGNORE INTO payloads VALUES (?1,?2,?3)",
            params![operation, digest, size as i64],
        )?;
        Ok(())
    }
    pub(crate) fn payload(&self, operation: &str, legacy: &[u8]) -> Result<Vec<u8>> {
        let Some((digest, size)) = self.payload_ref(operation)? else {
            return Ok(legacy.to_vec());
        };
        let target = self.sync_spool(&digest)?;
        verify(&target, &digest, size as u64)?;
        Ok(fs::read(target)?)
    }
    pub(crate) fn payload_ref(&self, operation: &str) -> Result<Option<(String, i64)>> {
        Ok(self
            .db
            .query_row(
                "SELECT hash,size FROM payloads WHERE operation_id=?1",
                [operation],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?)
    }
}
/// A new write either supplies bytes or references an existing immutable spool.
pub(crate) enum WritePayload<'a> {
    Inline(&'a [u8]),
    Stored { digest: &'a str, size: u64 },
}
impl WritePayload<'_> {
    pub(crate) fn size(&self) -> u64 {
        match self {
            Self::Inline(bytes) => bytes.len() as u64,
            Self::Stored { size, .. } => *size,
        }
    }
    pub(crate) fn digest(&self) -> String {
        match self {
            Self::Inline(bytes) => hash(bytes),
            Self::Stored { digest, .. } => (*digest).to_owned(),
        }
    }
    pub(crate) fn validate(&self, ws: &Workspace, path: &str) -> Result<()> {
        if crate::records::is_record(path) && self.size() > 1024 * 1024 {
            return Err(HostError::new("RECORD_TOO_LARGE"));
        }
        if self.size() > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        match self {
            Self::Inline(bytes) => {
                if crate::records::is_record(path) {
                    crate::records::validate(path, bytes)?;
                }
            }
            Self::Stored { digest, size } => {
                let mut file = open_verified(&ws.sync_spool(digest)?, digest, *size)?;
                validate_record(&mut file, path, *size)?;
            }
        }
        Ok(())
    }
    pub(crate) fn store(&self, ws: &Workspace, operation: &str) -> Result<()> {
        match self {
            Self::Inline(bytes) => ws.store_payload(operation, bytes),
            Self::Stored { digest, size } => {
                verify(&ws.sync_spool(digest)?, digest, *size)?;
                ws.register_payload(operation, digest, *size)
            }
        }
    }
}
pub(crate) fn validate_record(file: &mut fs::File, path: &str, size: u64) -> Result<()> {
    if crate::records::is_record(path) {
        if size > 1024 * 1024 {
            return Err(HostError::new("RECORD_TOO_LARGE"));
        }
        let mut bytes = Vec::new();
        file.take(1024 * 1024 + 1).read_to_end(&mut bytes)?;
        crate::records::validate(path, &bytes)?;
        file.seek(SeekFrom::Start(0))?;
    }
    Ok(())
}
pub(crate) fn hash_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut buffer = vec![0u8; VERIFY_BUFFER_BYTES];
    let mut hasher = Sha256::new();
    let mut total = 0u64;
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        total += count as u64;
        if total > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}
pub(crate) fn verify(path: &Path, digest: &str, size: u64) -> Result<()> {
    open_verified(path, digest, size).map(drop)
}
/// Return the verified handle, rewound for use by a streaming caller.
/// Path containment is the caller's responsibility; this is not a sandbox opener.
pub(crate) fn open_verified(path: &Path, digest: &str, size: u64) -> Result<fs::File> {
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() || !meta.is_file() || meta.len() != size {
        return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return Err(HostError::new("UNSAFE_PATH"));
        }
    }
    let mut stream = fs::File::open(path)?;
    if stream.metadata()?.len() != size {
        return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
    }
    verify_reader(&mut stream, digest, size)?;
    stream.seek(SeekFrom::Start(0))?;
    Ok(stream)
}
const VERIFY_BUFFER_BYTES: usize = 64 * 1024;
fn verify_reader(stream: &mut impl Read, digest: &str, size: u64) -> Result<()> {
    copy_verified(stream, &mut std::io::sink(), digest, size)
}
pub(crate) fn copy_verified(
    stream: &mut impl Read,
    target: &mut impl Write,
    digest: &str,
    size: u64,
) -> Result<()> {
    let mut hasher = Sha256::new();
    let mut buffer = vec![0; VERIFY_BUFFER_BYTES];
    let mut length = 0u64;
    loop {
        // Even if a file grows after metadata inspection, consume at most the
        // declared payload plus one byte, never an unbounded changing stream.
        let limit = size
            .saturating_sub(length)
            .saturating_add(1)
            .min(buffer.len() as u64) as usize;
        let count = stream.read(&mut buffer[..limit])?;
        if count == 0 {
            break;
        }
        length += count as u64;
        if length > size {
            return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
        }
        hasher.update(&buffer[..count]);
        target.write_all(&buffer[..count])?;
    }
    if length != size || format!("{:x}", hasher.finalize()) != digest {
        return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Generated {
        remaining: u64,
        consumed: u64,
        max_request: usize,
    }
    impl Read for Generated {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            self.max_request = self.max_request.max(buffer.len());
            let count = self.remaining.min(buffer.len() as u64) as usize;
            buffer[..count].fill(7);
            self.remaining -= count as u64;
            self.consumed += count as u64;
            Ok(count)
        }
    }
    #[test]
    fn hundred_mib_verification_is_bounded_and_rejects_growth_or_truncation() {
        let size = 100 * 1024 * 1024u64;
        let block = vec![7u8; VERIFY_BUFFER_BYTES];
        let mut hasher = Sha256::new();
        for _ in 0..size / block.len() as u64 {
            hasher.update(&block);
        }
        let digest = format!("{:x}", hasher.finalize());
        let mut stream = Generated {
            remaining: size,
            consumed: 0,
            max_request: 0,
        };
        verify_reader(&mut stream, &digest, size).unwrap();
        assert_eq!(stream.consumed, size);
        assert_eq!(stream.max_request, VERIFY_BUFFER_BYTES);
        let mut growing = Generated {
            remaining: u64::MAX,
            consumed: 0,
            max_request: 0,
        };
        assert_eq!(
            verify_reader(&mut growing, &hash(&[7; 8]), 8)
                .unwrap_err()
                .code,
            "SYNC_SPOOL_CORRUPT"
        );
        assert_eq!(growing.consumed, 9);
        assert_eq!(
            verify_reader(&mut &[7; 7][..], &hash(&[7; 8]), 8)
                .unwrap_err()
                .code,
            "SYNC_SPOOL_CORRUPT"
        );
        assert_eq!(
            verify_reader(&mut &[8; 8][..], &hash(&[7; 8]), 8)
                .unwrap_err()
                .code,
            "SYNC_SPOOL_CORRUPT"
        );
        verify_reader(&mut &[][..], &hash(&[]), 0).unwrap();
    }
    #[test]
    fn copying_rechecks_source_changed_after_initial_verification() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("source");
        fs::write(&path, b"original").unwrap();
        let mut source = open_verified(&path, &hash(b"original"), 8).unwrap();
        fs::write(&path, b"modified").unwrap();
        let mut destination = tempfile::NamedTempFile::new_in(temp.path()).unwrap();
        assert_eq!(
            copy_verified(&mut source, &mut destination, &hash(b"original"), 8)
                .unwrap_err()
                .code,
            "SYNC_SPOOL_CORRUPT"
        );
    }
    #[test]
    fn verified_file_is_rewound_and_corruption_is_rejected() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("payload");
        fs::write(&path, b"verified payload").unwrap();
        let mut file = open_verified(&path, &hash(b"verified payload"), 16).unwrap();
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).unwrap();
        assert_eq!(bytes, b"verified payload");
        drop(file);
        fs::write(&path, b"modified payload").unwrap();
        assert_eq!(
            verify(&path, &hash(b"verified payload"), 16)
                .unwrap_err()
                .code,
            "SYNC_SPOOL_CORRUPT"
        );
    }
}
