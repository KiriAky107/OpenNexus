"""Bounded encrypted handoff for an owned Android/Windows interoperability run.

Only an ephemeral test account and vault may cross jobs. The receiving job keeps
its private key locally; artifact upload/download and original-run provenance
checks belong to the driver. This module performs no network, file or app IO.
"""
import base64
import hashlib
import json
import os
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class HandoffError(ValueError):
    """A static error code, never a credential or decoded payload."""


def require(condition, code):
    if not condition:
        raise HandoffError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def context_bytes(context):
    require(type(context) is dict and set(context) == {
        'run_id', 'tools_commit', 'android_commit', 'desktop_commit'}, 'HANDOFF_CONTEXT_INVALID')
    require(type(context['run_id']) is int and 0 < context['run_id'] <= 9007199254740991,
            'HANDOFF_CONTEXT_INVALID')
    require(all(type(context[k]) is str and re.fullmatch(r'[0-9a-f]{40}', context[k])
                for k in ('tools_commit', 'android_commit', 'desktop_commit')), 'HANDOFF_CONTEXT_INVALID')
    return canonical(context)


def validate_payload(payload):
    require(type(payload) is dict and set(payload) == {
        'endpoint', 'username', 'password', 'vault_id', 'vault_name'}, 'HANDOFF_PAYLOAD_INVALID')
    require(all(type(v) is str for v in payload.values()), 'HANDOFF_PAYLOAD_INVALID')
    require(re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com/', payload['endpoint'])
            and len(payload['endpoint']) <= 253, 'HANDOFF_PAYLOAD_INVALID')
    require(re.fullmatch(r'android-owned-a-[0-9a-f]{12}', payload['username'])
            and re.fullmatch(r'[A-Za-z0-9_-]{40,100}', payload['password'])
            and re.fullmatch(r'[0-9a-f]{32}', payload['vault_id'])
            and re.fullmatch(r'Owned Android Sync [0-9a-f]{32}', payload['vault_name']), 'HANDOFF_PAYLOAD_INVALID')
    return payload


def encode(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def decode(value, size=None):
    require(type(value) is str and 0 < len(value) <= 8192
            and re.fullmatch(r'[A-Za-z0-9_-]+', value), 'HANDOFF_ENVELOPE_INVALID')
    try:
        result = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except Exception:
        raise HandoffError('HANDOFF_ENVELOPE_INVALID') from None
    require(encode(result) == value and (size is None or len(result) == size), 'HANDOFF_ENVELOPE_INVALID')
    return result


def public_bytes(private):
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def new_recipient():
    """Return private/public raw keys; the private value must never be uploaded."""
    private = X25519PrivateKey.generate()
    return (private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption()), public_bytes(private))


def derive(shared, context, recipient, ephemeral):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(context).digest(),
                info=b'OpenNexus owned Android Windows handoff v1\0' + recipient + ephemeral).derive(shared)


def seal(recipient, payload, context):
    associated = context_bytes(context)
    plain = canonical(validate_payload(payload))
    require(type(recipient) is bytes and len(recipient) == 32, 'HANDOFF_RECIPIENT_INVALID')
    private = X25519PrivateKey.generate()
    ephemeral = public_bytes(private)
    try:
        shared = private.exchange(X25519PublicKey.from_public_bytes(recipient))
    except ValueError:
        raise HandoffError('HANDOFF_RECIPIENT_INVALID') from None
    nonce = os.urandom(12)
    encrypted = AESGCM(derive(shared, associated, recipient, ephemeral)).encrypt(nonce, plain, associated)
    return {'schema': 1, 'context': dict(context), 'recipient_sha256': hashlib.sha256(recipient).hexdigest(),
            'ephemeral_public_key': encode(ephemeral), 'nonce': encode(nonce), 'ciphertext': encode(encrypted)}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'HANDOFF_PAYLOAD_INVALID')
        result[key] = value
    return result


def open_handoff(private_key, envelope, expected_context):
    associated = context_bytes(expected_context)
    require(type(envelope) is dict and set(envelope) == {
        'schema', 'context', 'recipient_sha256', 'ephemeral_public_key', 'nonce', 'ciphertext'},
        'HANDOFF_ENVELOPE_INVALID')
    require(type(envelope['schema']) is int and envelope['schema'] == 1
            and context_bytes(envelope['context']) == associated, 'HANDOFF_CONTEXT_MISMATCH')
    require(type(private_key) is bytes and len(private_key) == 32, 'HANDOFF_RECIPIENT_INVALID')
    private = X25519PrivateKey.from_private_bytes(private_key)
    recipient = public_bytes(private)
    require(envelope['recipient_sha256'] == hashlib.sha256(recipient).hexdigest(), 'HANDOFF_RECIPIENT_MISMATCH')
    ephemeral, nonce, encrypted = decode(envelope['ephemeral_public_key'], 32), decode(envelope['nonce'], 12), decode(envelope['ciphertext'])
    require(16 < len(encrypted) <= 4096, 'HANDOFF_ENVELOPE_INVALID')
    try:
        shared = private.exchange(X25519PublicKey.from_public_bytes(ephemeral))
        plain = AESGCM(derive(shared, associated, recipient, ephemeral)).decrypt(nonce, encrypted, associated)
        payload = json.loads(plain.decode('utf-8'), object_pairs_hook=unique_pairs)
        return dict(validate_payload(payload))
    except Exception:
        # Neither provider exceptions nor malformed decrypted input may expose
        # a credential in a job log. Callers receive only this fixed code.
        raise HandoffError('HANDOFF_AUTHENTICATION_FAILED') from None
