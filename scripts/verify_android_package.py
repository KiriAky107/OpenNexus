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
TLS_COORDINATE = 'org.rustls:rustls-platform-verifier:0.2.0'
TLS_UPSTREAM = '1aa691352a5e0c215210dfc9a3c2f2078639dc91'
TLS_ARTIFACTS = {
    'aar': ('aa021794230fbc2f0be355999e2cf67398dd563de066a2e17001ffbd0b69101b', 10295),
    'pom': ('78dcb69a1a20e91e72017de9a67104bba1874a7ecc8e6dcdfb2fe1a85de6b362', 555),
}
TLS_CLASSES = ['Lorg/rustls/platformverifier/' + name + ';' for name in ('CertificateVerifier', 'StatusCode', 'VerificationResult')]
TLS_SYMBOL = 'Java_cc_kronecker_opennexus_MainActivity_initializeSyncTls'
BROKER_SYMBOL = 'Java_cc_kronecker_opennexus_sync_NativeCredentialProbe_roundTrip'
TEST_PACKAGE = 'cc.kronecker.opennexus.dev.test'
TEST_RUNNER = 'androidx.test.runner.AndroidJUnitRunner'


def verify_test_manifest(badging, xml):
    match = re.search(r"^package: name='([^']+)'", badging, re.M)
    require(match is not None and match[1] == TEST_PACKAGE, 'ANDROID_TEST_PACKAGE_MISMATCH')
    blocks = re.split(r'(?m)^\s*E: ', xml)
    instruments = [block for block in blocks if block.startswith('instrumentation ')]
    require(len(instruments) == 1, 'ANDROID_TEST_INSTRUMENTATION_MISMATCH')
    def attribute(name):
        return re.findall(r'android:' + name + r'\(0x[0-9a-fA-F]+\)="([^"\n]+)"', instruments[0])
    require(attribute('name') == [TEST_RUNNER] and attribute('targetPackage') == ['cc.kronecker.opennexus.dev'],
        'ANDROID_TEST_INSTRUMENTATION_MISMATCH')


def verify_native_tests(apk, tools, signer):
    require(apk.is_file() and not apk.is_symlink(), 'ANDROID_TEST_APK_MISSING')
    badging = subprocess.check_output([str(tools/'aapt2'), 'dump', 'badging', str(apk)], text=True)
    xml = subprocess.check_output([str(tools/'aapt2'), 'dump', 'xmltree', '--file', 'AndroidManifest.xml', str(apk)], text=True)
    verify_test_manifest(badging, xml)
    certificates = subprocess.check_output([str(tools/'apksigner'), 'verify', '--print-certs', str(apk)], text=True)
    fingerprints = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$', certificates, re.M)
    require([value.lower() for value in fingerprints] == [signer.lower()], 'ANDROID_TEST_SIGNER_MISMATCH')
    with zipfile.ZipFile(apk) as archive:
        require(any(b'Lcc/kronecker/opennexus/sync/SyncCredentialStoreTest;' in archive.read(name)
            for name in archive.namelist() if re.fullmatch(r'classes(?:\d+)?\.dex', name)), 'ANDROID_KEYSTORE_TEST_CLASS_MISSING')
    return {'apk':'OpenNexus-android-tests.apk', 'sha256':digest(apk), 'bytes':apk.stat().st_size,
        'package':TEST_PACKAGE, 'runner':TEST_RUNNER, 'target_package':'cc.kronecker.opennexus.dev',
        'signer_certificate_sha256':signer.lower(), 'test_class':'cc.kronecker.opennexus.sync.SyncCredentialStoreTest',
        'expected_tests':5, 'actual_device_tests_passed':False}


def verify_native_broker(apk, native_dir, abis, symbol_tool):
    classes = ['Lcc/kronecker/opennexus/sync/SyncCredentialStore;', 'Lcc/kronecker/opennexus/sync/NativeCredentialProbe;']
    with zipfile.ZipFile(apk) as archive:
        dex = [archive.read(name) for name in archive.namelist() if re.fullmatch(r'classes(?:\d+)?\.dex', name)]
        require(all(any(descriptor.encode() in contents for contents in dex) for descriptor in classes),
            'ANDROID_SECRET_JVM_CLASSES_MISSING')
    symbols = {}
    for abi in abis:
        require(abi in MACHINES, 'ANDROID_ABI_UNSUPPORTED')
        library = native_dir / MACHINES[abi][1] / 'debug/libopennexus_mobile.so'
        table = subprocess.check_output([str(symbol_tool), '--defined-only', '--dynamic', str(library)], text=True)
        require(sum(line.split()[-1] == BROKER_SYMBOL for line in table.splitlines() if line.split()) == 1,
            'ANDROID_SECRET_JNI_SYMBOL_MISSING')
        symbols[abi] = BROKER_SYMBOL
    return {'class_descriptors':classes, 'native_symbols':symbols, 'debug_test_entry_only':True,
        'WebView_read_command':False, 'Rust_worker_verified':False, 'login_connected':False}


def verify_platform_tls(apk, native_dir, abis, symbol_tool, origin_file):
    origin = json.loads(origin_file.read_text('utf-8'))
    require(origin.get('coordinate') == TLS_COORDINATE and origin.get('upstream_commit') == TLS_UPSTREAM,
        'ANDROID_TLS_MAVEN_ORIGIN_MISMATCH')
    artifacts = origin.get('artifacts', [])
    require(len(artifacts) == len(TLS_ARTIFACTS) and {item.get('extension') for item in artifacts} == set(TLS_ARTIFACTS),
        'ANDROID_TLS_MAVEN_ARTIFACT_SET_MISMATCH')
    for item in artifacts:
        expected, size = TLS_ARTIFACTS[item['extension']]
        require(item.get('sha256') == expected and item.get('bytes') == size, 'ANDROID_TLS_MAVEN_BYTES_MISMATCH')
    with zipfile.ZipFile(apk) as archive:
        dex = [archive.read(name) for name in archive.namelist() if re.fullmatch(r'classes(?:\d+)?\.dex', name)]
        require(all(any(descriptor.encode() in contents for contents in dex) for descriptor in TLS_CLASSES),
            'ANDROID_TLS_JVM_CLASSES_MISSING')
    symbols = {}
    for abi in abis:
        require(abi in MACHINES, 'ANDROID_ABI_UNSUPPORTED')
        library = native_dir / MACHINES[abi][1] / 'debug/libopennexus_mobile.so'
        table = subprocess.check_output([str(symbol_tool), '--defined-only', '--dynamic', str(library)], text=True)
        names = [line.split()[-1] for line in table.splitlines() if line.split()]
        require(names.count(TLS_SYMBOL) == 1, 'ANDROID_TLS_JNI_SYMBOL_MISSING')
        symbols[abi] = TLS_SYMBOL
    return {'coordinate':TLS_COORDINATE, 'origin':origin, 'class_descriptors':TLS_CLASSES,
        'native_symbols':symbols, 'jvm_initialization_verified':False, 'HTTPS_verified':False}


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


def verify_mobile_files(apk, permissions):
    forbidden = {'android.permission.MANAGE_EXTERNAL_STORAGE', 'android.permission.READ_EXTERNAL_STORAGE',
        'android.permission.WRITE_EXTERNAL_STORAGE', 'android.permission.READ_MEDIA_IMAGES', 'android.permission.CAMERA'}
    actual = set(re.findall(r"uses-permission(?:-sdk-\d+)?: name='([^']+)'", permissions))
    require(not actual.intersection(forbidden), 'ANDROID_BROAD_FILE_PERMISSION')
    descriptor = b'Lcc/kronecker/opennexus/files/MobileFilesPlugin;'
    with zipfile.ZipFile(apk) as archive:
        dex = [name for name in archive.namelist() if re.fullmatch(r'classes(?:\d+)?\.dex', name)]
        require(any(descriptor in archive.read(name) for name in dex), 'ANDROID_FILES_PLUGIN_CLASS_MISSING')
    return {'class_descriptor': descriptor.decode(), 'broad_storage_or_camera_permissions': False,
        'native_class_presence_only': True, 'SAF_destination_execution_verified': False}


def verify_generated_mobile_files(root):
    kotlin = root / 'frontend/mobile/android/MobileFilesPlugin.kt'
    generated = root / 'frontend/mobile/src-tauri/gen/android/app/src/main/java/cc/kronecker/opennexus/files/MobileFilesPlugin.kt'
    require(kotlin.is_file() and not kotlin.is_symlink() and generated.is_file() and not generated.is_symlink()
        and generated.read_bytes() == kotlin.read_bytes(), 'ANDROID_FILES_PLUGIN_SOURCE_CHANGED')
    return digest(kotlin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--commit', required=True)
    parser.add_argument('--source-proof', type=Path, required=True)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--instrumentation-apk', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--abis', nargs='+', default=['arm64-v8a', 'x86_64'])
    args = parser.parse_args()
    root = args.repository_dir.resolve()
    before = json.loads(args.source_proof.read_text('utf-8'))
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True)
    if dirty.strip():
        print(json.dumps({'post_compile_source_changes': dirty.splitlines()}), flush=True)
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
    permissions = subprocess.check_output([str(tools / 'aapt2'), 'dump', 'permissions', str(apk)], text=True)
    files_plugin = verify_mobile_files(apk, permissions)
    tls_origin = root / 'frontend/mobile/src-tauri/gen/android/app/build/reports/platform-verifier/origin.json'
    native_tls = verify_platform_tls(apk, args.native_dir, args.abis,
        args.sdk / 'ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-nm', tls_origin)
    native_tests = verify_native_tests(args.instrumentation_apk.resolve(), tools, fingerprints[0])
    native_secret_broker = verify_native_broker(apk, args.native_dir, args.abis,
        args.sdk / 'ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-nm')
    kotlin_sha256 = verify_generated_mobile_files(root)
    proof = {'schema': 1, 'passed': True, 'kind': 'android-debug', 'source_commit': args.commit,
        'source_clean': True, 'source_proof': args.source_proof.name, 'package': package, 'version': config['version'],
        'version_code': config['bundle']['android']['versionCode'], 'min_sdk': 24, 'target_sdk': 36,
        'apk': apk.name, 'bytes': apk.stat().st_size, 'sha256': digest(apk), 'libraries': libraries,
        'signer_certificate_sha256': fingerprints[0].lower(), 'debug_signing': True,
        'actual_device_execution_verified': False, 'published': False,
        'files_plugin': {**files_plugin, 'source_sha256': kotlin_sha256},
        'native_tls':native_tls,
        'native_tests':native_tests,
        'native_secret_broker':native_secret_broker,
        'rustc': subprocess.check_output(['rustc', '-Vv'], text=True).strip(),
        'java': subprocess.run(['java', '-version'], capture_output=True, text=True, check=True).stderr.strip()}
    require(not args.output.exists(), 'ANDROID_PACKAGE_PROOF_EXISTS')
    atomic_json(args.output, proof)
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
