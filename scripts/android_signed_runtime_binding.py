"""Recheck each downloaded signed binary against its original build/signing receipts."""
import json
from pathlib import Path
import zipfile

from android_release_native import elf
from android_signed_runtime_inputs import manifest, strict_json
from check_android_release_manifest import inspect
from release_plan import atomic_json, digest, require
from sign_android_release import same_signing_payload
from verify_android_release_package import archive_inventory


def bind_pair(root, sdk, java, output):
    root, output = Path(root), Path(output)
    require(not output.exists(), 'ANDROID_RUNTIME_BINDING_OUTPUT_EXISTS')
    value = strict_json((root / 'runtime-inputs.json').read_bytes())
    manifest(value, value.get('draft_id'))
    downloaded = strict_json((root / 'download-proof.json').read_bytes())
    require(downloaded.get('kind') == 'android-signed-runtime-input-download'
            and downloaded.get('downloaded_original_bytes_verified') is True
            and downloaded.get('draft_id') == value['draft_id']
            and downloaded.get('manifest_sha256') == digest(root / 'runtime-inputs.json'),
            'ANDROID_RUNTIME_DOWNLOADED_MANIFEST_REQUIRED')
    output.mkdir(parents=True)
    result = {}
    for role in ('predecessor', 'successor'):
        item = value[role]
        for kind in ('apk', 'signature', 'input'):
            record = item[kind]
            path = root / record['name']
            require(path.is_file() and not path.is_symlink() and path.stat().st_size == record['bytes']
                    and digest(path) == record['sha256'] and downloaded['files'].get(path.name) == record,
                    'ANDROID_RUNTIME_DOWNLOADED_PAIR_CHANGED')
        apk = root / item['apk']['name']
        signed = strict_json((root / item['signature']['name']).read_bytes())
        original = strict_json((root / item['input']['name']).read_bytes())
        require(signed.get('schema') == original.get('schema') == 1
                and signed.get('passed') is True and original.get('passed') is True
                and signed.get('kind') == 'android-release-signed-input'
                and original.get('kind') == 'android-release-input'
                and signed.get('source_clean') is True and original.get('source_clean') is True
                and signed.get('source_commit') == original.get('source_commit') == item['source_commit']
                and signed.get('abi') == original.get('abi') == item['abi']
                and signed.get('metadata') == original.get('metadata'), 'ANDROID_RUNTIME_ORIGINAL_SOURCE_RECEIPTS')
        require(signed.get('original_input_sha256') == original.get('apk_sha256')
                and signed.get('original_input_proof_sha256') == item['input']['sha256']
                and signed.get('apk_sha256') == item['apk']['sha256'] and signed.get('apk_bytes') == item['apk']['bytes']
                and signed.get('intended_signer_verified') is True
                and signed.get('signer_role') == 'intended-android-release'
                and signed.get('certificate_sha256') == value['certificate_sha256']
                and signed.get('zip_alignment_page_size_kib') == original.get('zip_alignment_page_size_kib') == 16,
                'ANDROID_RUNTIME_INTENDED_SIGNER_RECEIPT')
        inventory = archive_inventory(apk, item['abi'])
        payload = same_signing_payload(original.get('inventory', {}), inventory)
        native = original.get('native_library', {})
        require(native.get('profile') == 'release'
                and native.get('debug_NativeCredentialProbe_roundTrip_export_absent') is True
                and native.get('equivalence', {}).get('exact_runtime_header_and_loader_segments_and_allocated_code_data_exports') is True,
                'ANDROID_RUNTIME_ORIGINAL_RELEASE_NATIVE_REQUIRED')
        actual = inspect(apk, sdk, java, version=item['version'], version_code=item['version_code'],
                         expected_sha256=item['apk']['sha256'], certificate=value['certificate_sha256'])
        require(actual['metadata'] == original['metadata'], 'ANDROID_RUNTIME_ACTUAL_METADATA_CHANGED')
        library = output / (role + '-native.so')
        with zipfile.ZipFile(apk) as bundle, bundle.open('lib/x86_64/libopennexus_mobile.so') as source, library.open('xb') as destination:
            while block := source.read(1024**2): destination.write(block)
        require(digest(library) == native.get('packaged_sha256'), 'ANDROID_RUNTIME_NATIVE_ORIGINAL_CHANGED')
        actual_elf = elf(library, 'x86_64')
        require(all(actual_elf[k] == native['equivalence'].get(k) for k in ('allocated_sections', 'program_headers')),
                'ANDROID_RUNTIME_COMPILED_CODE_CHANGED')
        require(digest(apk) == item['apk']['sha256'], 'ANDROID_RUNTIME_APK_CHANGED_DURING_BINDING')
        atomic_json(output / (role + '-actual-signature.json'), actual)
        result[role] = dict(source_commit=item['source_commit'], abi=item['abi'], apk_sha256=item['apk']['sha256'],
            apk_bytes=item['apk']['bytes'], certificate_sha256=value['certificate_sha256'], metadata=actual['metadata'],
            payload=payload, original_input_proof_sha256=item['input']['sha256'])
    receipt = dict(schema=1, kind='android-release-runtime-binary-binding', passed=True,
        manifest_sha256=digest(root / 'runtime-inputs.json'), binaries=result,
        actual_GUI_or_upgrade_or_physical_verified=False, published=False)
    atomic_json(output / 'binding-proof.json', receipt)
    return receipt
