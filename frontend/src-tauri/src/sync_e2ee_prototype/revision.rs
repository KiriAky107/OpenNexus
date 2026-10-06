use super::{
    decrypt, derive, encrypt, json, random, validate_id, ObjectReference, Result, VaultKeys,
};
use serde::{Deserialize, Serialize};
use unicode_normalization::UnicodeNormalization;
use zeroize::Zeroizing;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevisionContext {
    pub operation_id: String,
    pub base_revision: u64,
    pub metadata_epoch: u64,
    pub object: ObjectReference,
}
impl RevisionContext {
    fn validate(&self, keys: &VaultKeys) -> Result<()> {
        validate_id(&self.operation_id)?;
        self.object.validate()?;
        if self.object.vault_id != keys.vault_id {
            return Err("E2EE_SCOPE");
        }
        keys.key(self.metadata_epoch)?;
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevisionMetadata {
    pub file_id: String,
    pub path: String,
}

// Conservative portable path validation before allocating/importing anything.
// Product adoption must also use Workspace's no-link filesystem write guard.
fn validate_metadata(
    keys: &VaultKeys,
    context: &RevisionContext,
    metadata: &RevisionMetadata,
) -> Result<()> {
    validate_id(&metadata.file_id)?;
    if keys.file_token(&metadata.file_id)? != context.object.file_token {
        return Err("E2EE_SCOPE");
    }
    let path = &metadata.path;
    if path.is_empty()
        || path.chars().count() > 768
        || path.nfc().collect::<String>() != *path
        || path.contains('\\')
    {
        return Err("E2EE_PATH");
    }
    for part in path.split('/') {
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.ends_with(['.', ' '])
            || part
                .chars()
                .any(|c| c.is_control() || "<>:\"|?*".contains(c))
        {
            return Err("E2EE_PATH");
        }
        let upper = part.to_ascii_uppercase();
        let stem = upper.split('.').next().ok_or("E2EE_PATH")?;
        if [".GIT", ".AINOTE", "__PYCACHE__"].contains(&upper.as_str())
            || ["CON", "PRN", "AUX", "NUL"].contains(&stem)
            || ((stem.starts_with("COM") || stem.starts_with("LPT"))
                && stem.len() == 4
                && stem.as_bytes()[3].is_ascii_digit())
        {
            return Err("E2EE_PATH");
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevisionEnvelope {
    pub context: RevisionContext,
    pub nonce: [u8; 24],
    pub ciphertext: Vec<u8>,
}

fn aad(context: &RevisionContext, nonce: &[u8; 24]) -> Result<Vec<u8>> {
    json(&("OpenNexus/e2ee-prototype/revision/v1", context, nonce))
}

// A rename references the existing object bytes and encrypts only the new path.
// The public CAS head and identity token remain visible to the server; logical
// path collisions and trusted three-way bases must be resolved by clients.
pub fn seal_revision(
    keys: &VaultKeys,
    context: RevisionContext,
    metadata: &RevisionMetadata,
) -> Result<RevisionEnvelope> {
    context.validate(keys)?;
    if context.metadata_epoch != keys.epoch {
        return Err("E2EE_STALE_EPOCH");
    }
    validate_metadata(keys, &context, metadata)?;
    let nonce = random()?;
    let aad = aad(&context, &nonce)?;
    let key = derive(
        keys.key(context.metadata_epoch)?,
        &aad,
        b"OpenNexus/e2ee-prototype/revision-key/v1",
    )?;
    let plaintext = Zeroizing::new(json(metadata)?);
    let ciphertext = encrypt(&key, &nonce, &aad, &plaintext)?;
    Ok(RevisionEnvelope {
        context,
        nonce,
        ciphertext,
    })
}

pub fn open_revision(
    keys: &VaultKeys,
    expected: &RevisionContext,
    envelope: &RevisionEnvelope,
    minimum_epoch: u64,
) -> Result<RevisionMetadata> {
    expected.validate(keys)?;
    if expected.metadata_epoch < minimum_epoch {
        return Err("E2EE_STALE_EPOCH");
    }
    if &envelope.context != expected || envelope.ciphertext.len() > 4096 {
        return Err("E2EE_SCOPE");
    }
    let aad = aad(expected, &envelope.nonce)?;
    let key = derive(
        keys.key(expected.metadata_epoch)?,
        &aad,
        b"OpenNexus/e2ee-prototype/revision-key/v1",
    )?;
    let plaintext = decrypt(&key, &envelope.nonce, &aad, &envelope.ciphertext)?;
    let metadata: RevisionMetadata =
        serde_json::from_slice(&plaintext).map_err(|_| "E2EE_FORMAT")?;
    validate_metadata(keys, expected, &metadata)?;
    Ok(metadata)
}
