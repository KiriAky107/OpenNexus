use super::{decrypt, derive, encrypt, json, random, validate_id, Result};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::BTreeMap;
use x25519_dalek::{PublicKey, StaticSecret};
use zeroize::Zeroizing;

const MAX_EPOCHS: usize = 32;
const MAX_KEY_ENVELOPE: usize = 2048;

// Secret-bearing types have no Debug or Serialize implementation.
pub struct VaultKeys {
    pub(super) vault_id: String,
    pub(super) epoch: u64,
    identity_key: Zeroizing<[u8; 32]>,
    content_keys: BTreeMap<u64, Zeroizing<[u8; 32]>>,
}

impl VaultKeys {
    pub fn vault_id(&self) -> &str {
        &self.vault_id
    }
    pub fn epoch(&self) -> u64 {
        self.epoch
    }
    pub fn file_token(&self, file_id: &str) -> Result<[u8; 32]> {
        validate_id(file_id)?;
        let mut mac =
            Hmac::<Sha256>::new_from_slice(&*self.identity_key).map_err(|_| "E2EE_KDF")?;
        mac.update(b"OpenNexus/e2ee-prototype/file-id/v1\0");
        mac.update(self.vault_id.as_bytes());
        mac.update(b"\0");
        mac.update(file_id.as_bytes());
        Ok(mac.finalize().into_bytes().into())
    }
    pub(super) fn key(&self, epoch: u64) -> Result<&[u8; 32]> {
        self.content_keys
            .get(&epoch)
            .map(|k| &**k)
            .ok_or("E2EE_EPOCH_UNAVAILABLE")
    }
    fn encode(&self, history: HistoryAccess) -> Zeroizing<Vec<u8>> {
        let selected: Vec<_> = self
            .content_keys
            .iter()
            .filter(|(epoch, _)| history == HistoryAccess::AllRetained || **epoch == self.epoch)
            .collect();
        let mut result = Zeroizing::new(Vec::with_capacity(42 + 40 * selected.len()));
        result.extend_from_slice(&*self.identity_key);
        result.extend_from_slice(&self.epoch.to_be_bytes());
        result.extend_from_slice(&(selected.len() as u16).to_be_bytes());
        for (epoch, key) in selected {
            result.extend_from_slice(&epoch.to_be_bytes());
            result.extend_from_slice(&**key);
        }
        result
    }
    fn decode(vault_id: String, expected_epoch: u64, bytes: &[u8]) -> Result<Self> {
        if bytes.len() < 42 {
            return Err("E2EE_FORMAT");
        }
        let epoch = u64::from_be_bytes(bytes[32..40].try_into().map_err(|_| "E2EE_FORMAT")?);
        let count =
            u16::from_be_bytes(bytes[40..42].try_into().map_err(|_| "E2EE_FORMAT")?) as usize;
        if epoch == 0
            || epoch != expected_epoch
            || !(1..=MAX_EPOCHS).contains(&count)
            || bytes.len() != 42 + 40 * count
        {
            return Err("E2EE_FORMAT");
        }
        let mut content_keys = BTreeMap::new();
        let mut previous = 0;
        for record in bytes[42..].as_chunks::<40>().0 {
            let item_epoch = u64::from_be_bytes(record[..8].try_into().map_err(|_| "E2EE_FORMAT")?);
            if item_epoch <= previous || item_epoch > epoch {
                return Err("E2EE_FORMAT");
            }
            previous = item_epoch;
            content_keys.insert(
                item_epoch,
                Zeroizing::new(record[8..].try_into().map_err(|_| "E2EE_FORMAT")?),
            );
        }
        if !content_keys.contains_key(&epoch) {
            return Err("E2EE_FORMAT");
        }
        Ok(Self {
            vault_id,
            epoch,
            identity_key: Zeroizing::new(bytes[..32].try_into().map_err(|_| "E2EE_FORMAT")?),
            content_keys,
        })
    }
}

pub struct VaultOwner {
    keys: VaultKeys,
    signer: SigningKey,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum HistoryAccess {
    CurrentOnly,
    AllRetained,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrantScope {
    pub vault_id: String,
    pub device_id: String,
    pub recipient_key: [u8; 32],
    pub ephemeral_key: [u8; 32],
    pub epoch: u64,
    pub history: HistoryAccess,
    pub nonce: [u8; 24],
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    pub scope: GrantScope,
    pub ciphertext: Vec<u8>,
    pub signature: Vec<u8>,
}

fn grant_aad(scope: &GrantScope) -> Result<Vec<u8>> {
    json(&("OpenNexus/e2ee-prototype/device-grant/v1", scope))
}

impl VaultOwner {
    pub fn new(vault_id: &str) -> Result<Self> {
        validate_id(vault_id)?;
        let seed = Zeroizing::new(random::<32>()?);
        Ok(Self {
            keys: VaultKeys {
                vault_id: vault_id.into(),
                epoch: 1,
                identity_key: Zeroizing::new(random()?),
                content_keys: BTreeMap::from([(1, Zeroizing::new(random()?))]),
            },
            signer: SigningKey::from_bytes(&seed),
        })
    }
    pub fn keys(&self) -> &VaultKeys {
        &self.keys
    }
    pub fn public_key(&self) -> [u8; 32] {
        self.signer.verifying_key().to_bytes()
    }
    // Previous keys remain for explicitly authorized history. A real implementation
    // must persist both rotation and the removed device's grant policy atomically.
    pub fn rotate(&mut self) -> Result<u64> {
        if self.keys.content_keys.len() >= MAX_EPOCHS {
            return Err("E2EE_HISTORY_LIMIT");
        }
        let epoch = self.keys.epoch.checked_add(1).ok_or("E2EE_HISTORY_LIMIT")?;
        self.keys
            .content_keys
            .insert(epoch, Zeroizing::new(random()?));
        self.keys.epoch = epoch;
        Ok(epoch)
    }
    pub fn grant(
        &self,
        device_id: &str,
        recipient_key: [u8; 32],
        history: HistoryAccess,
    ) -> Result<Grant> {
        validate_id(device_id)?;
        let seed = Zeroizing::new(random::<32>()?);
        let ephemeral = StaticSecret::from(*seed);
        let shared = ephemeral.diffie_hellman(&PublicKey::from(recipient_key));
        if !shared.was_contributory() {
            return Err("E2EE_INVALID_PEER");
        }
        let scope = GrantScope {
            vault_id: self.keys.vault_id.clone(),
            device_id: device_id.into(),
            recipient_key,
            ephemeral_key: PublicKey::from(&ephemeral).to_bytes(),
            epoch: self.keys.epoch,
            history,
            nonce: random()?,
        };
        let aad = grant_aad(&scope)?;
        let key = derive(
            shared.as_bytes(),
            &aad,
            b"OpenNexus/e2ee-prototype/grant-key/v1",
        )?;
        let ciphertext = encrypt(&key, &scope.nonce, &aad, &self.keys.encode(history))?;
        let signature = self
            .signer
            .sign(&json(&(&aad, &ciphertext))?)
            .to_bytes()
            .to_vec();
        Ok(Grant {
            scope,
            ciphertext,
            signature,
        })
    }
    pub fn recovery(&self, secret: &RecoverySecret) -> Result<RecoveryEnvelope> {
        let seed = Zeroizing::new(self.signer.to_bytes());
        let mut payload = Zeroizing::new(seed.to_vec());
        payload.extend_from_slice(&self.keys.encode(HistoryAccess::AllRetained));
        let mut envelope = RecoveryEnvelope {
            vault_id: self.keys.vault_id.clone(),
            epoch: self.keys.epoch,
            nonce: random()?,
            ciphertext: Vec::new(),
        };
        let aad = recovery_aad(&envelope, self.public_key())?;
        let key = derive(
            &*secret.0,
            &aad,
            b"OpenNexus/e2ee-prototype/recovery-key/v1",
        )?;
        envelope.ciphertext = encrypt(&key, &envelope.nonce, &aad, &payload)?;
        Ok(envelope)
    }
    pub fn recover(
        envelope: &RecoveryEnvelope,
        secret: &RecoverySecret,
        vault_id: &str,
        owner_key: [u8; 32],
        minimum_epoch: u64,
    ) -> Result<Self> {
        validate_id(vault_id)?;
        if envelope.vault_id != vault_id
            || envelope.epoch < minimum_epoch
            || envelope.epoch == 0
            || envelope.ciphertext.len() > MAX_KEY_ENVELOPE
        {
            return Err("E2EE_SCOPE");
        }
        let aad = recovery_aad(envelope, owner_key)?;
        let key = derive(
            &*secret.0,
            &aad,
            b"OpenNexus/e2ee-prototype/recovery-key/v1",
        )?;
        let bytes = decrypt(&key, &envelope.nonce, &aad, &envelope.ciphertext)?;
        if bytes.len() < 32 {
            return Err("E2EE_FORMAT");
        }
        let seed = Zeroizing::new(bytes[..32].try_into().map_err(|_| "E2EE_FORMAT")?);
        let signer = SigningKey::from_bytes(&seed);
        if signer.verifying_key().to_bytes() != owner_key {
            return Err("E2EE_SCOPE");
        }
        let keys = VaultKeys::decode(vault_id.into(), envelope.epoch, &bytes[32..])?;
        Ok(Self { keys, signer })
    }
}

pub struct DeviceSecret(StaticSecret);
impl DeviceSecret {
    pub fn new() -> Result<Self> {
        let seed = Zeroizing::new(random::<32>()?);
        Ok(Self(StaticSecret::from(*seed)))
    }
    pub fn public_key(&self) -> [u8; 32] {
        PublicKey::from(&self.0).to_bytes()
    }
    // Owner fingerprint and minimum epoch are trusted local enrollment state,
    // never values learned from the same server carrying this envelope.
    pub fn accept(
        &self,
        grant: &Grant,
        owner_key: [u8; 32],
        vault_id: &str,
        device_id: &str,
        minimum_epoch: u64,
    ) -> Result<VaultKeys> {
        validate_id(vault_id)?;
        validate_id(device_id)?;
        if grant.scope.vault_id != vault_id
            || grant.scope.device_id != device_id
            || grant.scope.recipient_key != self.public_key()
            || grant.scope.epoch < minimum_epoch
            || grant.scope.epoch == 0
            || grant.ciphertext.len() > MAX_KEY_ENVELOPE
        {
            return Err("E2EE_SCOPE");
        }
        let aad = grant_aad(&grant.scope)?;
        let signature = Signature::from_slice(&grant.signature).map_err(|_| "E2EE_AUTH")?;
        VerifyingKey::from_bytes(&owner_key)
            .map_err(|_| "E2EE_AUTH")?
            .verify_strict(&json(&(&aad, &grant.ciphertext))?, &signature)
            .map_err(|_| "E2EE_AUTH")?;
        let shared = self
            .0
            .diffie_hellman(&PublicKey::from(grant.scope.ephemeral_key));
        if !shared.was_contributory() {
            return Err("E2EE_INVALID_PEER");
        }
        let key = derive(
            shared.as_bytes(),
            &aad,
            b"OpenNexus/e2ee-prototype/grant-key/v1",
        )?;
        let bytes = decrypt(&key, &grant.scope.nonce, &aad, &grant.ciphertext)?;
        let keys = VaultKeys::decode(vault_id.into(), grant.scope.epoch, &bytes)?;
        if grant.scope.history == HistoryAccess::CurrentOnly && keys.content_keys.len() != 1 {
            return Err("E2EE_FORMAT");
        }
        Ok(keys)
    }
}

// Generated random 256-bit secret, kept separate from the server backup. This is
// not a password format and must not be replaced with a low-entropy user string.
pub struct RecoverySecret(Zeroizing<[u8; 32]>);
impl RecoverySecret {
    pub fn new() -> Result<Self> {
        Ok(Self(Zeroizing::new(random()?)))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryEnvelope {
    pub vault_id: String,
    pub epoch: u64,
    pub nonce: [u8; 24],
    pub ciphertext: Vec<u8>,
}
fn recovery_aad(value: &RecoveryEnvelope, owner: [u8; 32]) -> Result<Vec<u8>> {
    json(&(
        "OpenNexus/e2ee-prototype/recovery/v1",
        &value.vault_id,
        value.epoch,
        owner,
        value.nonce,
    ))
}
