"""Bind a real debug APK to a clean source checkout and its compiled JNI libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import zipfile

from release_plan import atomic_json, build_source, digest, require

MACHINES = {'arm64-v8a': (183, 'aarch64-linux-android'), 'x86_64': (62, 'x86_64-linux-android')}


def package_identity(badging, *, package, version, version_code):
    match = re.search(r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']*)'", badging, re.M)
    require(match is not None, 'ANDROID_PACKAGE_IDENTITY_MISSING')
    require(match.groups() == (package, str(version_code), version), 'ANDROID_PACKAGE_IDENTITY_MISMATCH')
    minimum = re.findall(r"^(?:minSdkVersion|sdkVersion):'([0-9]+)'$", badging, re.M)
    target = re.findall(r"^targetSdkVersion:'([0-9]+)'$", badging, re.M)
    require(minimum == ['24'], 'ANDROID_MIN_SDK_MISMATCH')
    require(target == ['36'], 'ANDROID_TARGET_SDK_MISMATCH')


def verify_libraries(apk, native_dir, abis):
    require(set(abis) <= set(MACHINES) and len(set(abis)) == len(abis), 'ANDROID_ABI_UNSUPPORTED')
    checked = {}
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'ANDROID_DUPLICATE_ZIP_ENTRY')
        actual = {name.split('/')[1] for name in names if re.fullmatch(r'lib/[^/]+/libopennexus_mobile\.so', name)}
        require(actual == set(abis), 'ANDROID_ABI_SET_MISMATCH')
        for abi in abis:
            machine, target = MACHINES[abi]
            name = f'lib/{abi}/libopennexus_mobile.so'
            with archive.open(name) as stream:
                header = stream.read(64)
            require(len(header) == 64 and header[:6] == b'\x7fELF\x02\x01', 'ANDROID_ELF_INVALID')
            require(struct.unpack_from('<H', header, 16)[0] == 3 and struct.unpack_from('<H', header, 18)[0] == machine, 'ANDROID_ELF_ABI_MISMATCH')
            library = native_dir / target / 'debug/libopennexus_mobile.so'
            require(library.is_file() and not library.is_symlink(), 'ANDROID_NATIVE_INPUT_MISSING')
            with archive.open(name) as stream:
                actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(actual_hash == digest(library), 'ANDROID_COMPILED_LIBRARY_MISMATCH')
            checked[abi] = {'sha256': actual_hash, 'bytes': archive.getinfo(name).file_size, 'target': target}
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--commit', required=True)
    parser.add_argument('--source-proof', type=Path, required=True)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--abis', nargs='+', default=['arm64-v8a', 'x86_64'])
    args = parser.parse_args()
    root = args.repository_dir.resolve()
    before = json.loads(args.source_proof.read_text('utf-8'))
    require(before == build_source(root, args.commit), 'ANDROID_BUILD_SOURCE_CHANGED')
    config = json.loads((root / 'frontend/mobile/src-tauri/tauri.conf.json').read_text('utf-8'))
    apk = args.apk.resolve()
    require(apk.is_file() and not apk.is_symlink(), 'ANDROID_APK_MISSING')
    tools = args.sdk / 'build-tools/36.0.0'
    badging = subprocess.check_output([str(tools / 'aapt2'), 'dump', 'badging', str(apk)], text=True)
    print(json.dumps({'apk_metadata': [line for line in badging.splitlines()
        if line.startswith(('package:', 'sdkVersion:', 'minSdkVersion:', 'targetSdkVersion:', 'native-code:'))]}), flush=True)
    package = config['identifier'] + config['bundle']['android']['debugApplicationIdSuffix']
    package_identity(badging, package=package, version=config['version'], version_code=config['bundle']['android']['versionCode'])
    certificates = subprocess.check_output([str(tools / 'apksigner'), 'verify', '--verbose', '--print-certs', str(apk)], text=True)
    fingerprints = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$', certificates, re.M)
    require(len(fingerprints) == 1, 'ANDROID_SIGNING_IDENTITY_MISSING')
    libraries = verify_libraries(apk, args.native_dir, args.abis)
    proof = {'schema': 1, 'passed': True, 'kind': 'android-debug-bootstrap', 'source_commit': args.commit,
        'source_clean': True, 'source_proof': args.source_proof.name, 'package': package, 'version': config['version'],
        'version_code': config['bundle']['android']['versionCode'], 'min_sdk': 24, 'target_sdk': 36,
        'apk': apk.name, 'bytes': apk.stat().st_size, 'sha256': digest(apk), 'libraries': libraries,
        'signer_certificate_sha256': fingerprints[0].lower(), 'debug_signing': True,
        'actual_device_execution_verified': False, 'published': False,
        'rustc': subprocess.check_output(['rustc', '-Vv'], text=True).strip(),
        'java': subprocess.run(['java', '-version'], capture_output=True, text=True, check=True).stderr.strip()}
    require(not args.output.exists(), 'ANDROID_PACKAGE_PROOF_EXISTS')
    atomic_json(args.output, proof)
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
