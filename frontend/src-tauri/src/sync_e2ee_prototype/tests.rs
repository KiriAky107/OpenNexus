use super::*;
use sha2::{Digest, Sha256};
use std::io::{Cursor, Read};

const VAULT: &str = "vault-prototype-0001";
const FILE: &str = "file-prototype-00001";
const DEVICE: &str = "device-prototype-001";
const OP: &str = "operation-prototype1";

fn error<T>(result: Result<T>, expected: &str) {
    match result {
        Err(actual) => assert_eq!(actual, expected),
        Ok(_) => panic!("Unexpected successful operation"),
    }
}
fn pair(owner: &VaultOwner, history: HistoryAccess) -> (DeviceSecret, VaultKeys) {
    let device = DeviceSecret::new().unwrap();
    let grant = owner.grant(DEVICE, device.public_key(), history).unwrap();
    let keys = device
        .accept(
            &grant,
            owner.public_key(),
            VAULT,
            DEVICE,
            owner.keys().epoch(),
        )
        .unwrap();
    (device, keys)
}
fn bytes(prepared: &PreparedObject) -> Vec<u8> {
    let mut value = Vec::new();
    prepared
        .reader_at(0)
        .unwrap()
        .read_to_end(&mut value)
        .unwrap();
    value
}
fn context(keys: &VaultKeys, prepared: &PreparedObject) -> RevisionContext {
    RevisionContext {
        operation_id: OP.into(),
        base_revision: 4,
        metadata_epoch: keys.epoch(),
        object: prepared.reference().clone(),
    }
}
fn metadata(path: &str) -> RevisionMetadata {
    RevisionMetadata {
        file_id: FILE.into(),
        path: path.into(),
    }
}
fn read_all(mut verified: VerifiedContent) -> Vec<u8> {
    let mut output = Vec::new();
    verified.read_to_end(&mut output).unwrap();
    output
}

#[test]
fn grant_requires_separately_trusted_owner_recipient_and_epoch() {
    let owner = VaultOwner::new(VAULT).unwrap();
    let device = DeviceSecret::new().unwrap();
    let other_device = DeviceSecret::new().unwrap();
    let grant = owner
        .grant(DEVICE, device.public_key(), HistoryAccess::AllRetained)
        .unwrap();
    let accepted = device
        .accept(&grant, owner.public_key(), VAULT, DEVICE, 1)
        .unwrap();
    assert_eq!(
        accepted.file_token(FILE).unwrap(),
        owner.keys().file_token(FILE).unwrap()
    );
    let impostor = VaultOwner::new(VAULT).unwrap();
    error(
        device.accept(&grant, impostor.public_key(), VAULT, DEVICE, 1),
        "E2EE_AUTH",
    );
    error(
        other_device.accept(&grant, owner.public_key(), VAULT, DEVICE, 1),
        "E2EE_SCOPE",
    );
    error(
        device.accept(
            &grant,
            owner.public_key(),
            "vault-prototype-0002",
            DEVICE,
            1,
        ),
        "E2EE_SCOPE",
    );
    error(
        device.accept(&grant, owner.public_key(), VAULT, "device-prototype-002", 1),
        "E2EE_SCOPE",
    );
    error(
        device.accept(&grant, owner.public_key(), VAULT, DEVICE, 2),
        "E2EE_SCOPE",
    );
    let mut changed = grant.clone();
    changed.ciphertext[0] ^= 1;
    error(
        device.accept(&changed, owner.public_key(), VAULT, DEVICE, 1),
        "E2EE_AUTH",
    );
    let mut changed = grant.clone();
    changed.scope.history = HistoryAccess::CurrentOnly;
    error(
        device.accept(&changed, owner.public_key(), VAULT, DEVICE, 1),
        "E2EE_AUTH",
    );
    error(
        owner.grant(DEVICE, [0; 32], HistoryAccess::AllRetained),
        "E2EE_INVALID_PEER",
    );
    let mut oversized = grant;
    oversized.ciphertext = vec![0; 2049];
    error(
        device.accept(&oversized, owner.public_key(), VAULT, DEVICE, 1),
        "E2EE_SCOPE",
    );
}

#[test]
fn rotation_without_regrant_blocks_future_content_and_history_is_explicit() {
    let mut owner = VaultOwner::new(VAULT).unwrap();
    let (_old_device, old_keys) = pair(&owner, HistoryAccess::AllRetained);
    let old = seal_content(owner.keys(), FILE, 3, &b"old"[..]).unwrap();
    let old_token = old.reference().file_token;
    assert_eq!(owner.rotate().unwrap(), 2);
    let new = seal_content(owner.keys(), FILE, 3, &b"new"[..]).unwrap();
    assert_eq!(new.reference().file_token, old_token);
    error(
        open_content(&old_keys, new.reference(), new.reader_at(0).unwrap()),
        "E2EE_EPOCH_UNAVAILABLE",
    );
    // Revocation cannot unlearn previously delivered keys or erase old backups.
    assert_eq!(
        read_all(open_content(&old_keys, old.reference(), old.reader_at(0).unwrap()).unwrap()),
        b"old"
    );
    let (_, current_only) = pair(&owner, HistoryAccess::CurrentOnly);
    error(
        open_content(&current_only, old.reference(), old.reader_at(0).unwrap()),
        "E2EE_EPOCH_UNAVAILABLE",
    );
    assert_eq!(
        read_all(open_content(&current_only, new.reference(), new.reader_at(0).unwrap()).unwrap()),
        b"new"
    );
    let (_, with_history) = pair(&owner, HistoryAccess::AllRetained);
    assert_eq!(
        read_all(open_content(&with_history, old.reference(), old.reader_at(0).unwrap()).unwrap()),
        b"old"
    );
}

#[test]
fn recovery_restores_authority_history_and_rejects_rollback_wrong_secret() {
    let mut owner = VaultOwner::new(VAULT).unwrap();
    let old = seal_content(owner.keys(), FILE, 5, &b"early"[..]).unwrap();
    let secret = RecoverySecret::new().unwrap();
    let stale_backup = owner.recovery(&secret).unwrap();
    owner.rotate().unwrap();
    let backup = owner.recovery(&secret).unwrap();
    let restored = VaultOwner::recover(&backup, &secret, VAULT, owner.public_key(), 2).unwrap();
    assert_eq!(restored.public_key(), owner.public_key());
    assert_eq!(
        restored.keys().file_token(FILE).unwrap(),
        owner.keys().file_token(FILE).unwrap()
    );
    let (_, restored_device) = pair(&restored, HistoryAccess::AllRetained);
    assert_eq!(
        read_all(
            open_content(&restored_device, old.reference(), old.reader_at(0).unwrap()).unwrap()
        ),
        b"early"
    );
    error(
        VaultOwner::recover(
            &backup,
            &RecoverySecret::new().unwrap(),
            VAULT,
            owner.public_key(),
            2,
        ),
        "E2EE_AUTH",
    );
    error(
        VaultOwner::recover(&stale_backup, &secret, VAULT, owner.public_key(), 2),
        "E2EE_SCOPE",
    );
    error(
        VaultOwner::recover(
            &backup,
            &secret,
            "vault-prototype-0002",
            owner.public_key(),
            2,
        ),
        "E2EE_SCOPE",
    );
    let impostor = VaultOwner::new(VAULT).unwrap();
    error(
        VaultOwner::recover(&backup, &secret, VAULT, impostor.public_key(), 2),
        "E2EE_AUTH",
    );
    let mut changed = backup;
    changed.ciphertext[10] ^= 1;
    error(
        VaultOwner::recover(&changed, &secret, VAULT, owner.public_key(), 2),
        "E2EE_AUTH",
    );
}

#[test]
fn rename_reuses_object_but_protects_path_with_current_metadata_epoch() {
    let mut owner = VaultOwner::new(VAULT).unwrap();
    let (_, old_keys) = pair(&owner, HistoryAccess::AllRetained);
    let body = "print('中文😀')\r\n".as_bytes();
    let object = seal_content(owner.keys(), FILE, body.len() as u64, body).unwrap();
    let before = context(owner.keys(), &object);
    let original = seal_revision(
        owner.keys(),
        before.clone(),
        &metadata("experiments/课程 #%.py"),
    )
    .unwrap();
    owner.rotate().unwrap();
    let mut after = context(owner.keys(), &object);
    after.operation_id = "operation-prototype2".into();
    after.base_revision = 5;
    let renamed = metadata("experiments/改名 #%.py");
    let envelope = seal_revision(owner.keys(), after.clone(), &renamed).unwrap();
    assert_eq!(envelope.context.object, original.context.object);
    assert_ne!(envelope.ciphertext, original.ciphertext);
    assert_eq!(after.object.epoch, 1);
    assert_eq!(after.metadata_epoch, 2);
    let (_, fresh) = pair(&owner, HistoryAccess::CurrentOnly);
    assert_eq!(
        open_revision(&fresh, &after, &envelope, 2).unwrap(),
        renamed
    );
    error(
        open_content(&fresh, object.reference(), object.reader_at(0).unwrap()),
        "E2EE_EPOCH_UNAVAILABLE",
    );
    error(
        open_revision(&old_keys, &after, &envelope, 2),
        "E2EE_EPOCH_UNAVAILABLE",
    );
    error(
        open_revision(owner.keys(), &before, &original, 2),
        "E2EE_STALE_EPOCH",
    );
    error(
        seal_revision(owner.keys(), before, &renamed),
        "E2EE_STALE_EPOCH",
    );
    let visible = serde_json::to_string(&envelope).unwrap();
    assert!(!visible.contains(&renamed.path));
    assert!(!visible.contains(FILE));
    assert_eq!(
        read_all(
            open_content(
                owner.keys(),
                object.reference(),
                object.reader_at(0).unwrap()
            )
            .unwrap()
        ),
        body
    );
}

#[test]
fn metadata_binds_cas_operation_object_and_portable_identity() {
    let owner = VaultOwner::new(VAULT).unwrap();
    let object = seal_content(owner.keys(), FILE, 0, &b""[..]).unwrap();
    let ctx = context(owner.keys(), &object);
    let envelope = seal_revision(
        owner.keys(),
        ctx.clone(),
        &metadata("experiments/课程 #%.py"),
    )
    .unwrap();
    for mutation in 0..5 {
        let mut changed = ctx.clone();
        match mutation {
            0 => changed.base_revision += 1,
            1 => changed.operation_id = "operation-prototype2".into(),
            2 => changed.object.cipher_sha256[0] ^= 1,
            3 => changed.object.file_token[0] ^= 1,
            _ => changed.object.cipher_bytes += 1,
        }
        error(
            open_revision(owner.keys(), &changed, &envelope, 1),
            "E2EE_SCOPE",
        );
        let mut altered = envelope.clone();
        altered.context = changed.clone();
        error(
            open_revision(owner.keys(), &changed, &altered, 1),
            "E2EE_AUTH",
        );
    }
    for path in [
        "../escape.py",
        "/absolute.py",
        "a\\b.py",
        "a//b.py",
        ".git/config",
        "x/.ainote/key",
        "__pycache__/x.py",
        "a/CON.py",
        "a/COM1.py",
        "a/x.py ",
        "a/a\n.py",
        "a/e\u{301}.py",
    ] {
        error(
            seal_revision(owner.keys(), ctx.clone(), &metadata(path)),
            "E2EE_PATH",
        );
    }
    let mut wrong_file = metadata("experiments/课程.py");
    wrong_file.file_id = "file-prototype-00002".into();
    error(seal_revision(owner.keys(), ctx, &wrong_file), "E2EE_SCOPE");
}

#[test]
fn content_authenticates_empty_short_and_multi_chunk_with_exact_bytes() {
    let owner = VaultOwner::new(VAULT).unwrap();
    let (_, peer) = pair(&owner, HistoryAccess::AllRetained);
    for size in [0, 1, CHUNK_BYTES - 1, CHUNK_BYTES, CHUNK_BYTES + 23] {
        let body: Vec<_> = (0..size).map(|index| (index % 251) as u8).collect();
        let prepared = seal_content(owner.keys(), FILE, size as u64, Cursor::new(&body)).unwrap();
        let verified =
            open_content(&peer, prepared.reference(), prepared.reader_at(0).unwrap()).unwrap();
        assert_eq!(verified.plaintext_bytes, size as u64);
        assert_eq!(
            verified.plaintext_sha256,
            <[u8; 32]>::from(Sha256::digest(&body))
        );
        assert_eq!(read_all(verified), body);
        assert_eq!(
            bytes(&prepared).len() as u64,
            prepared.reference().cipher_bytes
        );
    }
}

#[test]
fn fresh_encryptions_are_distinct_but_confirmed_offset_retry_is_exact() {
    let owner = VaultOwner::new(VAULT).unwrap();
    let body = vec![b'z'; CHUNK_BYTES + 100];
    let first = seal_content(owner.keys(), FILE, body.len() as u64, Cursor::new(&body)).unwrap();
    let second = seal_content(owner.keys(), FILE, body.len() as u64, Cursor::new(&body)).unwrap();
    assert_ne!(
        first.reference().cipher_sha256,
        second.reference().cipher_sha256
    );
    let full = bytes(&first);
    let mut confirmed = full[..CHUNK_BYTES].to_vec();
    first
        .reader_at(CHUNK_BYTES as u64)
        .unwrap()
        .read_to_end(&mut confirmed)
        .unwrap();
    assert_eq!(confirmed, full);
    assert_eq!(
        <[u8; 32]>::from(Sha256::digest(&confirmed)),
        first.reference().cipher_sha256
    );
    let mut at_eof = first.reader_at(first.reference().cipher_bytes).unwrap();
    assert_eq!(at_eof.read(&mut [0; 1]).unwrap(), 0);
    error(
        first.reader_at(first.reference().cipher_bytes + 1),
        "E2EE_OFFSET",
    );
}

#[test]
fn corruption_reordering_truncation_and_wrong_receipt_never_return_plaintext() {
    let owner = VaultOwner::new(VAULT).unwrap();
    let body = vec![b'a'; CHUNK_BYTES * 2];
    let object = seal_content(owner.keys(), FILE, body.len() as u64, Cursor::new(body)).unwrap();
    let original = bytes(&object);
    let header_len = u32::from_be_bytes(original[8..12].try_into().unwrap()) as usize;
    let first = 12 + header_len;
    let second = first + 4 + CHUNK_BYTES + 16;
    for kind in 0..5 {
        let mut changed = original.clone();
        match kind {
            0 => changed[first + 8] ^= 1,
            1 => {
                let last = changed.len() - 1;
                changed[last] ^= 1;
            }
            2 => {
                let chunk = changed[first..second].to_vec();
                changed.copy_within(second..second + chunk.len(), first);
                changed[second..second + chunk.len()].copy_from_slice(&chunk);
            }
            3 => {
                changed.truncate(second);
            }
            _ => {
                changed.push(0);
            }
        }
        let mut receipt = object.reference().clone();
        // Even an attacker replacing the outer hash cannot forge the AEAD tags.
        receipt.cipher_sha256 = Sha256::digest(&changed).into();
        receipt.cipher_bytes = changed.len() as u64;
        assert!(open_content(owner.keys(), &receipt, Cursor::new(changed)).is_err());
    }
    let mut wrong = object.reference().clone();
    wrong.cipher_sha256[0] ^= 1;
    error(
        open_content(owner.keys(), &wrong, Cursor::new(&original)),
        "E2EE_AUTH",
    );
    wrong = object.reference().clone();
    wrong.file_token[0] ^= 1;
    error(
        open_content(owner.keys(), &wrong, Cursor::new(&original)),
        "E2EE_SCOPE",
    );
    wrong = object.reference().clone();
    wrong.vault_id = "vault-prototype-0002".into();
    error(
        open_content(owner.keys(), &wrong, Cursor::new(&original)),
        "E2EE_SCOPE",
    );
    let impostor = VaultOwner::new(VAULT).unwrap();
    error(
        open_content(impostor.keys(), object.reference(), Cursor::new(&original)),
        "E2EE_AUTH",
    );
}

#[test]
fn source_size_and_ciphertext_quota_are_checked_before_publishing() {
    let owner = VaultOwner::new(VAULT).unwrap();
    error(
        seal_content(owner.keys(), FILE, 3, &b"ab"[..]),
        "E2EE_SOURCE_CHANGED",
    );
    error(
        seal_content(owner.keys(), FILE, 2, &b"abc"[..]),
        "E2EE_SOURCE_CHANGED",
    );
    // 100 MiB plaintext exceeds the same service limit after tags and framing.
    error(
        seal_content(
            owner.keys(),
            FILE,
            MAX_OBJECT_BYTES,
            std::io::repeat(0).take(MAX_OBJECT_BYTES),
        ),
        "E2EE_OBJECT_LIMIT",
    );
    error(
        seal_content(owner.keys(), FILE, u64::MAX, std::io::empty()),
        "E2EE_OBJECT_LIMIT",
    );
}

#[test]
fn bounded_history_limit_keeps_existing_keys_and_epoch_unchanged() {
    let mut owner = VaultOwner::new(VAULT).unwrap();
    for expected in 2..=32 {
        assert_eq!(owner.rotate().unwrap(), expected);
    }
    error(owner.rotate(), "E2EE_HISTORY_LIMIT");
    assert_eq!(owner.keys().epoch(), 32);
    let (_, peer) = pair(&owner, HistoryAccess::AllRetained);
    assert_eq!(peer.epoch(), 32);
    let secret = RecoverySecret::new().unwrap();
    assert!(VaultOwner::recover(
        &owner.recovery(&secret).unwrap(),
        &secret,
        VAULT,
        owner.public_key(),
        32
    )
    .is_ok());
}
