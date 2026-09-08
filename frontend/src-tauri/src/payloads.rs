//! Immutable payloads are fsynced before any SQLite reference becomes visible.
use crate::workspace::{hash, HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
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
        let old: Option<(String, i64)> = self
            .db
            .query_row(
                "SELECT hash,size FROM payloads WHERE operation_id=?1",
                [operation],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if old.is_some_and(|v| v != (digest.clone(), content.len() as i64)) {
            return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
        }
        self.db.execute(
            "INSERT OR IGNORE INTO payloads VALUES (?1,?2,?3)",
            params![operation, digest, content.len() as i64],
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
pub(crate) fn verify(path: &Path, digest: &str, size: u64) -> Result<()> {
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
    let mut hasher = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    if format!("{:x}", hasher.finalize()) != digest {
        return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
    }
    Ok(())
}
