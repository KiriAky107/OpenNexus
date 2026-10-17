"""An ephemeral receiving key owned by the future exclusive Windows pair job.

This key encrypts only the new synthetic fixture handoff. It is unrelated to
APK/release signing. Plaintext payload and private key never become artifacts.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from android_desktop_exchange import exchange_bytes
from android_desktop_exchange_api import wait_original_exchange
from android_desktop_handoff import canonical, encode, new_recipient, open_handoff, public_bytes, require
from android_desktop_product import claim_root, original_marker, owned_root


def write_new(path, value):
    with path.open('xb') as stream:
        stream.write(value); stream.flush(); os.fsync(stream.fileno())


def prepare_receiving_key(context):
    # claim_root rejects local/self-hosted/user profiles before key generation.
    root = claim_root(os.environ, context)
    write_new(root/'receiving-intent.json',canonical({'context':context,'purpose':'new owned fixture handoff only',
              'private_key_not_uploaded':True,'APK_or_release_signing':False}))
    private, public = new_recipient()
    require(len(private) == len(public) == 32, 'PAIR_RECEIVING_KEY_INVALID')
    write_new(root/'receiving-key.bin',private)
    recipient = root/'recipient'; recipient.mkdir(exist_ok=False)
    body = exchange_bytes(context,'recipient',{'public_key':encode(public)})
    write_new(recipient/'exchange.json',body)
    proof={'context':context,'recipient_public_key_sha256':hashlib.sha256(public).hexdigest(),
           'public_exchange_sha256':hashlib.sha256(body).hexdigest(),'private_key_bytes':32,
           'private_key_or_plaintext_credentials_uploaded':False,'APK_or_release_signing':False}
    write_new(root/'receiving-proof.json',canonical(proof))
    return recipient/'exchange.json', proof


def read_receiving_key(context):
    root = owned_root(os.environ, context); original_marker(root, context)
    key = root/'receiving-key.bin'
    value = key.stat(follow_symlinks=False)
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_size == 32
            and not getattr(value,'st_file_attributes',0) & 0x400, 'PAIR_RECEIVING_KEY_INVALID')
    with key.open('rb') as stream:
        private = stream.read(33)
    require(len(private) == 32, 'PAIR_RECEIVING_KEY_INVALID')
    public = public_bytes(X25519PrivateKey.from_private_bytes(private))
    # Refuse a replacement key even if its length is valid.
    require((root/'recipient/exchange.json').read_bytes() == exchange_bytes(context,'recipient',{'public_key':encode(public)}),
            'PAIR_RECEIVING_KEY_CHANGED')
    return private


def receive_original_fixture(context, api, cache):
    private = read_receiving_key(context)
    sealed, provenance = wait_original_exchange(context,'sealed',api,cache)
    # Caller uses this only in private memory for the actual own Desktop login.
    # Never serialize this returned payload into an artifact or raw exception.
    return open_handoff(private,sealed,context), provenance
