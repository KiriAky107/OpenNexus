//! Offline protocol experiment, deliberately absent from the desktop feature.
//! No network, credentials, vault writes, enrollment UI, or migration is provided.
//! Production v1 still sends readable content and paths; these envelopes require
//! a separately negotiated encrypted-vault protocol before any product adoption.

mod content;
mod keys;
mod revision;
#[cfg(test)]
mod tests;

pub use content::{open_content, seal_content, ObjectReference, PreparedObject, VerifiedContent};
pub use keys::{
    DeviceSecret, Grant, HistoryAccess, RecoveryEnvelope, RecoverySecret, VaultKeys, VaultOwner,
};
pub use revision::{
    open_revision, seal_revision, RevisionContext, RevisionEnvelope, RevisionMetadata,
};

use chacha20poly1305::{
    aead::{Aead, KeyInit, Payload},
    XChaCha20Poly1305, XNonce,
};
use hkdf::Hkdf;
use rand::{rngs::OsRng, RngCore};
use serde::Serialize;
use sha2::Sha256;
use zeroize::Zeroizing;

pub type Result<T> = std::result::Result<T, &'static str>;
pub const CHUNK_BYTES: usize = 1_048_576;
pub const MAX_OBJECT_BYTES: u64 = 104_857_600;

fn random<const N: usize>() -> Result<[u8; N]> {
    let mut bytes = [0; N];
    OsRng
        .try_fill_bytes(&mut bytes)
        .map_err(|_| "E2EE_RANDOM_FAILED")?;
    Ok(bytes)
}

fn validate_id(value: &str) -> Result<()> {
    if !(16..=80).contains(&value.len())
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err("E2EE_INVALID_ID");
    }
    Ok(())
}

fn json(value: &impl Serialize) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|_| "E2EE_FORMAT")
}

fn derive(secret: &[u8], salt: &[u8], domain: &[u8]) -> Result<Zeroizing<[u8; 32]>> {
    let mut key = Zeroizing::new([0; 32]);
    Hkdf::<Sha256>::new(Some(salt), secret)
        .expand(domain, &mut *key)
        .map_err(|_| "E2EE_KDF")?;
    Ok(key)
}

fn encrypt(key: &[u8; 32], nonce: &[u8; 24], aad: &[u8], plaintext: &[u8]) -> Result<Vec<u8>> {
    XChaCha20Poly1305::new(key.into())
        .encrypt(
            XNonce::from_slice(nonce),
            Payload {
                msg: plaintext,
                aad,
            },
        )
        .map_err(|_| "E2EE_AUTH")
}

fn decrypt(
    key: &[u8; 32],
    nonce: &[u8; 24],
    aad: &[u8],
    ciphertext: &[u8],
) -> Result<Zeroizing<Vec<u8>>> {
    XChaCha20Poly1305::new(key.into())
        .decrypt(
            XNonce::from_slice(nonce),
            Payload {
                msg: ciphertext,
                aad,
            },
        )
        .map(Zeroizing::new)
        .map_err(|_| "E2EE_AUTH")
}
