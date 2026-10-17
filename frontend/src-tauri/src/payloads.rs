//! 在任何 SQLite 引用变得可见之前，不可变的有效负载会被 fsync。
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
        self.store_payload_file_checked(operation, source, expected, &|| Ok(()))
    }
    pub(crate) fn store_payload_file_checked(
        &self,
        operation: &str,
        source: &Path,
        expected: &str,
        check: &dyn Fn() -> Result<()>,
    ) -> Result<()> {
        let size = self.snapshot_file_checked(source, expected, check)?;
        check()?;
        self.register_payload(operation, expected, size)
    }
    pub(crate) fn sync_store_file(&self, source: &Path) -> Result<String> {
        let digest = hash_file(source)?;
        self.snapshot_file(source, &digest)?;
        Ok(digest)
    }
    fn snapshot_file(&self, source: &Path, expected: &str) -> Result<u64> {
        self.snapshot_file_checked(source, expected, &|| Ok(()))
    }
    pub(crate) fn snapshot_file_checked(
        &self,
        source: &Path,
        expected: &str,
        check: &dyn Fn() -> Result<()>,
    ) -> Result<u64> {
        check()?;
        let size = fs::metadata(source)?.len();
        if size > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        let mut input = open_verified_checked(source, expected, size, check).map_err(|error| {
            if error.code == "SYNC_SPOOL_CORRUPT" {
                HostError::new("REVISION_CONFLICT")
            } else {
                error
            }
        })?;
        let target = self.sync_spool(expected)?;
        if target.exists() {
            open_verified_checked(&target, expected, size, check)?;
        } else {
            let parent = target
                .parent()
                .ok_or_else(|| HostError::new("UNSAFE_PATH"))?;
            let mut temp = tempfile::NamedTempFile::new_in(parent)?;
            copy_verified_checked(&mut input, &mut temp, expected, size, check)?;
            temp.as_file().sync_all()?;
            check()?;
            temp.persist_noclobber(&target)
                .map_err(|_| HostError::new("SYNC_SPOOL_FAILED"))?;
            #[cfg(unix)]
            fs::File::open(parent)?.sync_all()?;
        }
        check()?;
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
/// 新写入要么直接提供字节，要么引用现有的不可变暂存文件。
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
        if crate::canvas_contract::is_canvas(path)
            && self.size() > crate::canvas_contract::MAX_CANVAS_BYTES as u64
        {
            return Err(HostError::new("CANVAS_TOO_LARGE"));
        }
        if crate::records::is_record(path) && self.size() > 1024 * 1024 {
            return Err(HostError::new("RECORD_TOO_LARGE"));
        }
        if self.size() > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        match self {
            Self::Inline(bytes) => {
                if crate::canvas_contract::is_canvas(path) {
                    crate::canvas_contract::validate(bytes)?;
                }
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
    if crate::canvas_contract::is_canvas(path) {
        if size > crate::canvas_contract::MAX_CANVAS_BYTES as u64 {
            return Err(HostError::new("CANVAS_TOO_LARGE"));
        }
        let mut bytes = Vec::new();
        file.take(crate::canvas_contract::MAX_CANVAS_BYTES as u64 + 1)
            .read_to_end(&mut bytes)?;
        crate::canvas_contract::validate(&bytes)?;
        file.seek(SeekFrom::Start(0))?;
    }
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
    hash_file_info(path).map(|(digest, _)| digest)
}
pub(crate) fn hash_file_checked(path: &Path, check: &dyn Fn() -> Result<()>) -> Result<String> {
    hash_file_info_checked(path, check).map(|(digest, _)| digest)
}
pub(crate) fn sync_file_info(source: &Path, path: &str) -> Result<(String, u64)> {
    sync_file_info_checked(source, path, &|| Ok(()))
}
pub(crate) fn sync_file_info_checked(
    source: &Path,
    path: &str,
    check: &dyn Fn() -> Result<()>,
) -> Result<(String, u64)> {
    check()?;
    if crate::records::is_record(path) || crate::canvas_contract::is_canvas(path) {
        let mut bytes = Vec::new();
        let limit = if crate::canvas_contract::is_canvas(path) {
            crate::canvas_contract::MAX_CANVAS_BYTES
        } else {
            1024 * 1024
        };
        let mut reader = fs::File::open(source)?.take(limit as u64 + 1);
        let mut buffer = vec![0; VERIFY_BUFFER_BYTES];
        loop {
            check()?;
            let count = reader.read(&mut buffer)?;
            check()?;
            if count == 0 {
                break;
            }
            bytes.extend_from_slice(&buffer[..count]);
        }
        if crate::canvas_contract::is_canvas(path) {
            crate::canvas_contract::validate(&bytes)?;
        } else {
            crate::records::validate(path, &bytes)?;
        }
        Ok((hash(&bytes), bytes.len() as u64))
    } else {
        hash_file_info_checked(source, check)
    }
}
fn hash_file_info(path: &Path) -> Result<(String, u64)> {
    hash_file_info_checked(path, &|| Ok(()))
}
fn hash_file_info_checked(path: &Path, check: &dyn Fn() -> Result<()>) -> Result<(String, u64)> {
    check()?;
    let mut file = fs::File::open(path)?;
    let mut buffer = vec![0u8; VERIFY_BUFFER_BYTES];
    let mut hasher = Sha256::new();
    let mut total = 0u64;
    loop {
        check()?;
        let count = file.read(&mut buffer)?;
        check()?;
        if count == 0 {
            break;
        }
        total += count as u64;
        if total > 100 * 1024 * 1024 {
            return Err(HostError::new("FILE_TOO_LARGE"));
        }
        hasher.update(&buffer[..count]);
    }
    Ok((format!("{:x}", hasher.finalize()), total))
}
pub(crate) fn verify(path: &Path, digest: &str, size: u64) -> Result<()> {
    open_verified(path, digest, size).map(drop)
}
/// 返回已经验证并回绕到起始位置的句柄，供流式调用方使用。
/// 路径约束由调用方负责；此函数并不负责建立沙箱。
pub(crate) fn open_verified(path: &Path, digest: &str, size: u64) -> Result<fs::File> {
    open_verified_checked(path, digest, size, &|| Ok(()))
}
fn open_verified_checked(
    path: &Path,
    digest: &str,
    size: u64,
    check: &dyn Fn() -> Result<()>,
) -> Result<fs::File> {
    check()?;
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
    copy_verified_checked(&mut stream, &mut std::io::sink(), digest, size, check)?;
    stream.seek(SeekFrom::Start(0))?;
    check()?;
    Ok(stream)
}
const VERIFY_BUFFER_BYTES: usize = 64 * 1024;
#[cfg(test)]
fn verify_reader(stream: &mut impl Read, digest: &str, size: u64) -> Result<()> {
    copy_verified(stream, &mut std::io::sink(), digest, size)
}
pub(crate) fn copy_verified(
    stream: &mut impl Read,
    target: &mut impl Write,
    digest: &str,
    size: u64,
) -> Result<()> {
    copy_verified_checked(stream, target, digest, size, &|| Ok(()))
}
fn copy_verified_checked(
    stream: &mut impl Read,
    target: &mut impl Write,
    digest: &str,
    size: u64,
    check: &dyn Fn() -> Result<()>,
) -> Result<()> {
    let mut hasher = Sha256::new();
    let mut buffer = vec![0; VERIFY_BUFFER_BYTES];
    let mut length = 0u64;
    loop {
        check()?;
        // 即使文件在元数据检查后增长，最多消耗声明的有效负载加上一个字节，而不是无限变化的流。
        let limit = size
            .saturating_sub(length)
            .saturating_add(1)
            .min(buffer.len() as u64) as usize;
        let count = stream.read(&mut buffer[..limit])?;
        check()?;
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
    check()?;
    if length != size || format!("{:x}", hasher.finalize()) != digest {
        return Err(HostError::new("SYNC_SPOOL_CORRUPT"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sync_canceled_payload_copy_stops_at_a_chunk_without_publishing_unverified_bytes() {
        struct Observed<'a> {
            bytes: Vec<u8>,
            written: &'a std::cell::Cell<usize>,
        }
        impl Write for Observed<'_> {
            fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
                self.bytes.extend_from_slice(bytes);
                self.written.set(self.bytes.len());
                Ok(bytes.len())
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let original = vec![7u8; VERIFY_BUFFER_BYTES * 4];
        let mut input = &original[..];
        let written = std::cell::Cell::new(0);
        let mut target = Observed {
            bytes: vec![],
            written: &written,
        };
        let error = copy_verified_checked(
            &mut input,
            &mut target,
            &hash(&original),
            original.len() as u64,
            &|| {
                if written.get() >= VERIFY_BUFFER_BYTES {
                    Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
                } else {
                    Ok(())
                }
            },
        )
        .unwrap_err();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(target.bytes, &original[..VERIFY_BUFFER_BYTES]);
        assert_eq!(input.len(), original.len() - VERIFY_BUFFER_BYTES);
    }
    #[test]
    fn sync_file_hash_checks_cancellation_between_chunks_and_keeps_original_bytes() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("original.md");
        let bytes = vec![b'x'; VERIFY_BUFFER_BYTES * 4];
        fs::write(&path, &bytes).unwrap();
        let calls = std::cell::Cell::new(0);
        let error = sync_file_info_checked(&path, "original.md", &|| {
            calls.set(calls.get() + 1);
            if calls.get() == 6 {
                Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
            } else {
                Ok(())
            }
        })
        .unwrap_err();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(calls.get(), 6);
        assert_eq!(fs::read(&path).unwrap(), bytes);
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    }
    #[test]
    fn sync_checked_canvas_keeps_the_same_validated_original_digest_and_size() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("original.canvas");
        let bytes = br#"{"nodes":[],"edges":[]}"#;
        fs::write(&path, bytes).unwrap();
        let original = sync_file_info(&path, "original.canvas").unwrap();
        assert_eq!(
            sync_file_info_checked(&path, "original.canvas", &|| Ok(())).unwrap(),
            original
        );
        assert_eq!(original, (hash(bytes), bytes.len() as u64));
        assert_eq!(fs::read(&path).unwrap(), bytes);
    }
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
