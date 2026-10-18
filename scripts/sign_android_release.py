"""Locally sign one immutable, source-verified split release input without uploading keys.

The result binds signature and unchanged ZIP payload only. Runtime, upgrade, physical
device and publication evidence remain separate, and this receipt cannot publish a release.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from check_android_release_manifest import inspect as inspect_signature
from release_plan import COMMIT, HEX, VERSION, atomic_json, digest, journal_lock, require
from verify_android_release_package import archive_inventory

SIGNATURE = re.compile(r'META-INF/(?:MANIFEST\.MF|[A-Z0-9_-]{1,16}\.(?:SF|RSA|DSA|EC))')


def bind_input(apk, proof):
    require(type(proof) is dict and proof.get('schema') == 1 and proof.get('passed') is True
        and proof.get('kind') == 'android-release-input' and proof.get('source_clean') is True
        and type(proof.get('source_commit')) is str and COMMIT.fullmatch(proof['source_commit']),
        'ANDROID_SIGN_RELEASE_SOURCE_REQUIRED')
    require(apk.is_file() and not apk.is_symlink() and proof.get('apk_sha256') == digest(apk)
        and type(proof.get('apk_bytes')) is int and proof['apk_bytes'] == apk.stat().st_size,
        'ANDROID_SIGN_ORIGINAL_INPUT_CHANGED')
    meta = proof.get('metadata', {})
    require(type(meta) is dict and meta.get('application_id') == 'cc.kronecker.opennexus'
        and type(meta.get('version')) is str and VERSION.fullmatch(meta['version'])
        and type(meta.get('version_code')) is int and meta['version_code'] > 0
        and all(meta.get(k) is False for k in ('debuggable', 'test_only', 'uses_cleartext_traffic', 'allow_backup')),
        'ANDROID_SIGN_STABLE_RELEASE_FLAGS_REQUIRED')
    inventory = archive_inventory(apk, proof.get('abi'))
    require(inventory == proof.get('inventory') and proof.get('zip_alignment_page_size_kib') == 16,
        'ANDROID_SIGN_SOURCE_PAYLOAD_CHANGED')
    native = proof.get('native_library', {})
    require(type(native) is dict and native.get('profile') == 'release'
        and native.get('debug_NativeCredentialProbe_roundTrip_export_absent') is True
        and native.get('equivalence', {}).get('exact_runtime_header_and_loader_segments_and_allocated_code_data_exports') is True,
        'ANDROID_SIGN_COMPILED_RELEASE_RUNTIME_REQUIRED')
    return inventory


def same_signing_payload(before, after):
    expected = {k:v for k,v in before.items() if not SIGNATURE.fullmatch(k)}
    actual = {k:v for k,v in after.items() if not SIGNATURE.fullmatch(k)}
    require(expected == actual and bool(expected), 'ANDROID_SIGN_NON_SIGNATURE_PAYLOAD_CHANGED')
    require({k for k in after if SIGNATURE.fullmatch(k)} == {
        'META-INF/MANIFEST.MF', 'META-INF/ONXREL.SF', 'META-INF/ONXREL.RSA'}, 'ANDROID_SIGN_V1_FILES_UNEXPECTED')
    return dict(non_signature_entries=len(expected), exact_uncompressed_payload_preserved=True,
        native_library_or_DEX_or_resources_or_manifest_rewritten=False)


def sign(apk, proof_path, output, keystore, alias, password_env, certificate, sdk, java):
    require(os.environ.get('GITHUB_ACTIONS') != 'true', 'ANDROID_SIGN_PRIVATE_KEY_LOCAL_ONLY')
    require(type(password_env) is str and re.fullmatch(r'OPENNEXUS_ANDROID_[A-Z0-9_]{1,60}', password_env)
        and bool(os.environ.get(password_env)), 'ANDROID_SIGN_PASSWORD_ENV_REQUIRED')
    require(type(alias) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', alias)
        and type(certificate) is str and HEX.fullmatch(certificate), 'ANDROID_SIGN_EXPLICIT_IDENTITY_REQUIRED')
    require(keystore.is_file() and not keystore.is_symlink(), 'ANDROID_SIGN_KEYSTORE_MISSING')
    require(not output.exists(), 'ANDROID_SIGN_ORIGINAL_OUTPUT_EXISTS_RECONCILE_ONLY')
    proof = json.loads(proof_path.read_text('utf-8'))
    before = bind_input(apk, proof)
    original, key_hash, proof_hash = digest(apk), digest(keystore), digest(proof_path)
    tools = sdk / 'build-tools/36.0.0'; jar = tools / 'lib/apksigner.jar'
    require(jar.is_file() and java.is_file(), 'ANDROID_SIGN_LOCAL_SDK_MISSING')
    output.mkdir(parents=True)
    target = output / ('OpenNexus-android-' + proof['abi'] + '-signed.apk')
    intent = dict(schema=1, source_commit=proof['source_commit'], original_apk_sha256=original,
        original_proof_sha256=proof_hash, keystore_sha256=key_hash, alias=alias,
        expected_certificate_sha256=certificate, signer_role='intended-android-release',
        output_name=target.name, automatic_retry=False, private_key_uploaded=False)
    atomic_json(output / 'signing-intent.json', intent)
    args = [java, '-Xmx256m', '-jar', jar, 'sign', '--ks', keystore, '--ks-type', 'PKCS12',
        '--ks-key-alias', alias, '--ks-pass', 'env:' + password_env, '--key-pass', 'env:' + password_env,
        '--v1-signer-name', 'ONXREL', '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true',
        '--v3-signing-enabled', 'true', '--v4-signing-enabled', 'false', '--min-sdk-version', '24', '--out', target, apk]
    result = subprocess.run([str(v) for v in args], stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    # The password is supplied only through the process environment, never argv or a receipt.
    for suffix, raw in (('stdout', result.stdout), ('stderr', result.stderr)):
        with (output / ('signing-original.' + suffix)).open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    require(result.returncode == 0 and target.is_file(), 'ANDROID_SIGN_ORIGINAL_FAILED_RECONCILE_ONLY')
    require(digest(apk) == original and digest(keystore) == key_hash and digest(proof_path) == proof_hash,
        'ANDROID_SIGN_INPUT_CHANGED_DURING_SIGNING')
    payload = same_signing_payload(before, archive_inventory(target, proof['abi']))
    signed = inspect_signature(target, sdk, java, version=proof['metadata']['version'],
        version_code=proof['metadata']['version_code'], expected_sha256=digest(target), certificate=certificate)
    require(signed['metadata'] == proof['metadata'], 'ANDROID_SIGN_METADATA_CHANGED')
    align = tools / ('zipalign.exe' if os.name == 'nt' else 'zipalign')
    alignment = subprocess.run([str(align), '-c', '-P', '16', '4', str(target)], capture_output=True, timeout=60)
    with (output / 'signed-alignment.stdout').open('xb') as stream: stream.write(alignment.stdout)
    with (output / 'signed-alignment.stderr').open('xb') as stream: stream.write(alignment.stderr)
    require(alignment.returncode == 0, 'ANDROID_SIGN_ALIGNMENT_CHANGED')
    require(digest(apk) == original and digest(proof_path) == proof_hash and digest(keystore) == key_hash,
        'ANDROID_SIGN_IMMUTABLE_INPUT_CHANGED')
    require(digest(target) == signed['apk_sha256'], 'ANDROID_SIGN_VERIFIED_OUTPUT_CHANGED')
    record = dict(schema=1, passed=True, kind='android-release-signed-input', source_commit=proof['source_commit'],
        source_clean=True, abi=proof['abi'], apk_sha256=digest(target), apk_bytes=target.stat().st_size,
        original_input_sha256=original, original_input_proof_sha256=proof_hash,
        certificate_sha256=certificate, signer_role='intended-android-release', intended_signer_verified=True,
        metadata=proof['metadata'], payload=payload, signed_manifest_verification=signed, zip_alignment_page_size_kib=16,
        actual_native_runtime_verified=False, upgrade_verified=False, physical_phone_verified=False, published=False)
    atomic_json(output / 'signature-proof.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('apk', 'input-proof', 'output-directory', 'keystore', 'sdk', 'java'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('alias', 'password-env', 'certificate-sha256'): parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    with journal_lock(args.output_directory):
        record = sign(args.apk, args.input_proof, args.output_directory, args.keystore, args.alias,
            args.password_env, args.certificate_sha256, args.sdk, args.java)
    print(json.dumps({k:record[k] for k in ('kind', 'source_commit', 'abi', 'apk_sha256', 'certificate_sha256', 'published')}))


if __name__ == '__main__': main()
