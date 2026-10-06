use super::{derive, json, random, validate_id, Result, VaultKeys, CHUNK_BYTES, MAX_OBJECT_BYTES};
use chacha20poly1305::{
    aead::{AeadInPlace, KeyInit},
    XChaCha20Poly1305, XNonce,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use tempfile::NamedTempFile;
use zeroize::Zeroizing;

const MAGIC: &[u8; 8] = b"ONE2EE01";
const MAX_HEADER: usize = 4096;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObjectReference {
    pub vault_id: String,
    pub epoch: u64,
    pub file_token: [u8; 32],
    pub cipher_sha256: [u8; 32],
    pub cipher_bytes: u64,
}
impl ObjectReference {
    pub(super) fn validate(&self) -> Result<()> {
        validate_id(&self.vault_id)?;
        if self.epoch == 0 || self.cipher_bytes == 0 || self.cipher_bytes > MAX_OBJECT_BYTES {
            return Err("E2EE_OBJECT_LIMIT");
        }
        Ok(())
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Header {
    format: u8,
    vault_id: String,
    epoch: u64,
    file_token: [u8; 32],
    object_salt: [u8; 32],
    nonce_prefix: [u8; 16],
    plaintext_bytes: u64,
}

pub struct PreparedObject {
    file: NamedTempFile,
    reference: ObjectReference,
}
impl PreparedObject {
    pub fn reference(&self) -> &ObjectReference {
        &self.reference
    }
    // Reopen the same prepared bytes after a confirmed upload offset. Never
    // re-encrypt a retry: fresh encryption changes both nonces and object hash.
    pub fn reader_at(&self, offset: u64) -> Result<impl Read> {
        if offset > self.reference.cipher_bytes {
            return Err("E2EE_OFFSET");
        }
        let mut file = self.file.reopen().map_err(|_| "E2EE_IO")?;
        file.seek(SeekFrom::Start(offset)).map_err(|_| "E2EE_IO")?;
        Ok(file)
    }
}

// The anonymous staging file never becomes available to the caller on failure.
// A product importer must still apply its existing snapshot/identity/write guards.
pub struct VerifiedContent {
    file: File,
    pub plaintext_bytes: u64,
    pub plaintext_sha256: [u8; 32],
}
impl Read for VerifiedContent {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        self.file.read(buffer)
    }
}

fn predicted_size(header_len: usize, size: u64) -> Result<u64> {
    size.checked_add(
        size.div_ceil(CHUNK_BYTES as u64)
            .checked_mul(20)
            .ok_or("E2EE_OBJECT_LIMIT")?,
    )
    .and_then(|n| n.checked_add(12 + header_len as u64 + 60))
    .filter(|n| *n <= MAX_OBJECT_BYTES)
    .ok_or("E2EE_OBJECT_LIMIT")
}

fn object_key(keys: &VaultKeys, header: &Header) -> Result<Zeroizing<[u8; 32]>> {
    let info = json(&(
        "OpenNexus/e2ee-prototype/content-key/v1",
        &header.vault_id,
        header.epoch,
        header.file_token,
    ))?;
    derive(keys.key(header.epoch)?, &header.object_salt, &info)
}
fn nonce(header: &Header, index: u64) -> [u8; 24] {
    let mut result = [0; 24];
    result[..16].copy_from_slice(&header.nonce_prefix);
    result[16..].copy_from_slice(&index.to_be_bytes());
    result
}
fn chunk_aad(header_hash: &[u8; 32], index: u64, footer: bool) -> Vec<u8> {
    let mut result = b"OpenNexus/e2ee-prototype/content-chunk/v1\0".to_vec();
    result.extend_from_slice(header_hash);
    result.extend_from_slice(&index.to_be_bytes());
    result.push(u8::from(footer));
    result
}
fn write_hashed(file: &mut File, digest: &mut Sha256, bytes: &[u8]) -> Result<()> {
    file.write_all(bytes).map_err(|_| "E2EE_IO")?;
    digest.update(bytes);
    Ok(())
}

/// Stage exactly `size` bytes, with bounded memory, under fresh random per-object
/// keys/nonces. The 100 MiB service quota counts ciphertext, including framing.
pub fn seal_content(
    keys: &VaultKeys,
    file_id: &str,
    size: u64,
    mut source: impl Read,
) -> Result<PreparedObject> {
    if size > MAX_OBJECT_BYTES {
        return Err("E2EE_OBJECT_LIMIT");
    }
    let header = Header {
        format: 1,
        vault_id: keys.vault_id.clone(),
        epoch: keys.epoch,
        file_token: keys.file_token(file_id)?,
        object_salt: random()?,
        nonce_prefix: random()?,
        plaintext_bytes: size,
    };
    let encoded_header = json(&header)?;
    if encoded_header.len() > MAX_HEADER {
        return Err("E2EE_FORMAT");
    }
    let cipher_bytes = predicted_size(encoded_header.len(), size)?;
    let header_hash: [u8; 32] = Sha256::digest(&encoded_header).into();
    let key = object_key(keys, &header)?;
    let cipher = XChaCha20Poly1305::new((&*key).into());
    let mut staged = NamedTempFile::new().map_err(|_| "E2EE_IO")?;
    let file = staged.as_file_mut();
    let mut digest = Sha256::new();
    write_hashed(file, &mut digest, MAGIC)?;
    write_hashed(
        file,
        &mut digest,
        &(encoded_header.len() as u32).to_be_bytes(),
    )?;
    write_hashed(file, &mut digest, &encoded_header)?;
    let mut plain_digest = Sha256::new();
    let mut remaining = size;
    let mut index = 1;
    let mut buffer = Zeroizing::new(Vec::with_capacity(CHUNK_BYTES + 16));
    while remaining > 0 {
        let count = remaining.min(CHUNK_BYTES as u64) as usize;
        buffer.resize(count, 0);
        source
            .read_exact(&mut buffer)
            .map_err(|_| "E2EE_SOURCE_CHANGED")?;
        plain_digest.update(&*buffer);
        cipher
            .encrypt_in_place(
                XNonce::from_slice(&nonce(&header, index)),
                &chunk_aad(&header_hash, index, false),
                &mut *buffer,
            )
            .map_err(|_| "E2EE_AUTH")?;
        write_hashed(file, &mut digest, &(buffer.len() as u32).to_be_bytes())?;
        write_hashed(file, &mut digest, &buffer)?;
        remaining -= count as u64;
        index += 1;
    }
    let mut extra = [0; 1];
    if source.read(&mut extra).map_err(|_| "E2EE_IO")? != 0 {
        return Err("E2EE_SOURCE_CHANGED");
    }
    let mut footer = Zeroizing::new(plain_digest.finalize().to_vec());
    footer.extend_from_slice(&size.to_be_bytes());
    cipher
        .encrypt_in_place(
            XNonce::from_slice(&nonce(&header, index)),
            &chunk_aad(&header_hash, index, true),
            &mut *footer,
        )
        .map_err(|_| "E2EE_AUTH")?;
    write_hashed(file, &mut digest, &(footer.len() as u32).to_be_bytes())?;
    write_hashed(file, &mut digest, &footer)?;
    file.sync_all().map_err(|_| "E2EE_IO")?;
    if file.metadata().map_err(|_| "E2EE_IO")?.len() != cipher_bytes {
        return Err("E2EE_FORMAT");
    }
    Ok(PreparedObject {
        file: staged,
        reference: ObjectReference {
            vault_id: header.vault_id,
            epoch: header.epoch,
            file_token: header.file_token,
            cipher_sha256: digest.finalize().into(),
            cipher_bytes,
        },
    })
}

struct DigestReader<R> {
    reader: R,
    digest: Sha256,
    count: u64,
    limit: u64,
}
impl<R: Read> DigestReader<R> {
    fn exact(&mut self, target: &mut [u8]) -> Result<()> {
        self.count = self
            .count
            .checked_add(target.len() as u64)
            .filter(|n| *n <= self.limit)
            .ok_or("E2EE_FORMAT")?;
        self.reader
            .read_exact(target)
            .map_err(|_| "E2EE_TRUNCATED")?;
        self.digest.update(target);
        Ok(())
    }
    fn length(&mut self) -> Result<usize> {
        let mut bytes = [0; 4];
        self.exact(&mut bytes)?;
        Ok(u32::from_be_bytes(bytes) as usize)
    }
}

/// `expected` must come from authenticated revision metadata or a locally
/// prepared object. Neither a server-supplied hash nor an embedded header proves
/// that this is the intended revision. Publish no plaintext until this succeeds.
pub fn open_content(
    keys: &VaultKeys,
    expected: &ObjectReference,
    source: impl Read,
) -> Result<VerifiedContent> {
    expected.validate()?;
    if expected.vault_id != keys.vault_id {
        return Err("E2EE_SCOPE");
    }
    let mut input = DigestReader {
        reader: source,
        digest: Sha256::new(),
        count: 0,
        limit: expected.cipher_bytes,
    };
    let mut magic = [0; 8];
    input.exact(&mut magic)?;
    if &magic != MAGIC {
        return Err("E2EE_FORMAT");
    }
    let header_len = input.length()?;
    if header_len == 0 || header_len > MAX_HEADER {
        return Err("E2EE_FORMAT");
    }
    let mut encoded_header = vec![0; header_len];
    input.exact(&mut encoded_header)?;
    let header: Header = serde_json::from_slice(&encoded_header).map_err(|_| "E2EE_FORMAT")?;
    if header.format != 1
        || header.vault_id != expected.vault_id
        || header.epoch != expected.epoch
        || header.file_token != expected.file_token
    {
        return Err("E2EE_SCOPE");
    }
    if json(&header)? != encoded_header
        || predicted_size(header_len, header.plaintext_bytes)? != expected.cipher_bytes
    {
        return Err("E2EE_FORMAT");
    }
    let header_hash: [u8; 32] = Sha256::digest(&encoded_header).into();
    let key = object_key(keys, &header)?;
    let cipher = XChaCha20Poly1305::new((&*key).into());
    let mut staged = tempfile::tempfile().map_err(|_| "E2EE_IO")?;
    let mut remaining = header.plaintext_bytes;
    let mut plain_digest = Sha256::new();
    let mut index = 1;
    let mut buffer = Zeroizing::new(Vec::with_capacity(CHUNK_BYTES + 16));
    while remaining > 0 {
        let count = remaining.min(CHUNK_BYTES as u64) as usize;
        if input.length()? != count + 16 {
            return Err("E2EE_FORMAT");
        }
        buffer.resize(count + 16, 0);
        input.exact(&mut buffer)?;
        cipher
            .decrypt_in_place(
                XNonce::from_slice(&nonce(&header, index)),
                &chunk_aad(&header_hash, index, false),
                &mut *buffer,
            )
            .map_err(|_| "E2EE_AUTH")?;
        plain_digest.update(&*buffer);
        staged.write_all(&buffer).map_err(|_| "E2EE_IO")?;
        remaining -= count as u64;
        index += 1;
    }
    if input.length()? != 56 {
        return Err("E2EE_FORMAT");
    }
    let mut footer = Zeroizing::new(vec![0; 56]);
    input.exact(&mut footer)?;
    cipher
        .decrypt_in_place(
            XNonce::from_slice(&nonce(&header, index)),
            &chunk_aad(&header_hash, index, true),
            &mut *footer,
        )
        .map_err(|_| "E2EE_AUTH")?;
    let plaintext_sha256: [u8; 32] = plain_digest.finalize().into();
    if footer[..32] != plaintext_sha256 || footer[32..] != header.plaintext_bytes.to_be_bytes() {
        return Err("E2EE_AUTH");
    }
    let mut extra = [0; 1];
    if input.count != expected.cipher_bytes
        || input.reader.read(&mut extra).map_err(|_| "E2EE_IO")? != 0
        || <[u8; 32]>::from(input.digest.finalize()) != expected.cipher_sha256
    {
        return Err("E2EE_AUTH");
    }
    staged.seek(SeekFrom::Start(0)).map_err(|_| "E2EE_IO")?;
    Ok(VerifiedContent {
        file: staged,
        plaintext_bytes: header.plaintext_bytes,
        plaintext_sha256,
    })
}
