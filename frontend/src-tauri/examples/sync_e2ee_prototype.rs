//! Generated-data-only measurement, with no access to user files or profiles.
use notesagent_host::sync_e2ee_prototype::*;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::time::Instant;

fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let mut owner = VaultOwner::new("vault-benchmark-0001")?;
    let device = DeviceSecret::new()?;
    let grant = owner.grant(
        "device-benchmark-001",
        device.public_key(),
        HistoryAccess::AllRetained,
    )?;
    let peer = device.accept(
        &grant,
        owner.public_key(),
        owner.keys().vault_id(),
        "device-benchmark-001",
        1,
    )?;
    let mut measurements = Vec::new();
    for size in [1000, CHUNK_BYTES as u64, MAX_OBJECT_BYTES - 16_384] {
        let start = Instant::now();
        let prepared = seal_content(
            owner.keys(),
            "file-benchmark-00001",
            size,
            std::io::repeat(b'p').take(size),
        )?;
        let seal_ms = start.elapsed().as_secs_f64() * 1000.0;
        let reference = prepared.reference().clone();
        let start = Instant::now();
        let mut verified = open_content(&peer, &reference, prepared.reader_at(0)?)?;
        let mut digest = Sha256::new();
        let mut buffer = [0; 65_536];
        let mut read_bytes = 0;
        loop {
            let count = verified.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            if !buffer[..count].iter().all(|b| *b == b'p') {
                return Err("Generated source mismatch".into());
            }
            read_bytes += count as u64;
            digest.update(&buffer[..count]);
        }
        if read_bytes != size || <[u8; 32]>::from(digest.finalize()) != verified.plaintext_sha256 {
            return Err("Verified content mismatch".into());
        }
        let open_ms = start.elapsed().as_secs_f64() * 1000.0;
        // Hash the actual retry bytes from the exact confirmed service offset.
        let offset = reference.cipher_bytes.min(CHUNK_BYTES as u64);
        let mut reader = prepared.reader_at(0)?;
        let mut digest = Sha256::new();
        std::io::copy(
            &mut reader.by_ref().take(offset),
            &mut HashWriter(&mut digest),
        )?;
        std::io::copy(
            &mut prepared.reader_at(offset)?,
            &mut HashWriter(&mut digest),
        )?;
        let retry_exact = <[u8; 32]>::from(digest.finalize()) == reference.cipher_sha256;
        if !retry_exact {
            return Err("Retry bytes changed".into());
        }
        measurements.push(json!({ "plaintext_bytes":size, "ciphertext_bytes":reference.cipher_bytes, "overhead_bytes":reference.cipher_bytes-size, "seal_ms":seal_ms, "open_and_check_ms":open_ms, "retry_offset":offset, "retry_exact":retry_exact }));
    }
    owner.rotate()?;
    let secret = RecoverySecret::new()?;
    let backup = owner.recovery(&secret)?;
    let recovered = VaultOwner::recover(
        &backup,
        &secret,
        owner.keys().vault_id(),
        owner.public_key(),
        2,
    )?;
    if recovered.public_key() != owner.public_key() {
        return Err("Recovery changed authority".into());
    }
    let quota_rejected = matches!(
        seal_content(
            owner.keys(),
            "file-benchmark-00001",
            MAX_OBJECT_BYTES,
            std::io::repeat(0).take(MAX_OBJECT_BYTES)
        ),
        Err("E2EE_OBJECT_LIMIT")
    );
    if !quota_rejected {
        return Err("Ciphertext quota not enforced".into());
    }
    println!(
        "{}",
        serde_json::to_string_pretty(
            &json!({ "prototype_only":true, "production_encryption":"transport-only", "chunk_bytes":CHUNK_BYTES, "max_ciphertext_bytes":MAX_OBJECT_BYTES, "measurements":measurements, "full_plaintext_limit_rejected":quota_rejected, "recovery_verified":true })
        )?
    );
    Ok(())
}
struct HashWriter<'a>(&'a mut Sha256);
impl std::io::Write for HashWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.update(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
