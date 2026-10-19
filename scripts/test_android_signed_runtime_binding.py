import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import android_signed_runtime_binding as subject
from release_plan import ReleaseError, digest
from verify_android_release_package import archive_inventory
from test_android_signed_runtime_inputs import fixture


class SignedBinaryBinding(unittest.TestCase):
    def fixtures(self, root):
        value, _, _, _ = fixture()
        for role in ('predecessor', 'successor'):
            item = value[role]
            apk = root / item['apk']['name']
            with zipfile.ZipFile(apk, 'w') as bundle:
                bundle.writestr('AndroidManifest.xml', b'synthetic manifest, not executable')
                bundle.writestr('classes.dex', role.encode())
                bundle.writestr('lib/x86_64/libopennexus_mobile.so', b'synthetic native')
            before = archive_inventory(apk, 'x86_64')
            original = dict(schema=1, passed=True, kind='android-release-input', source_clean=True,
                source_commit=item['source_commit'], abi='x86_64', apk_sha256='e'*64, zip_alignment_page_size_kib=16,
                metadata=dict(application_id='cc.kronecker.opennexus', version=item['version'], version_code=item['version_code'],
                    min_sdk=24, target_sdk=36, debuggable=False, test_only=False, uses_cleartext_traffic=False, allow_backup=False),
                inventory=before, native_library=dict(profile='release', packaged_sha256=hashlib.sha256(b'synthetic native').hexdigest(),
                    debug_NativeCredentialProbe_roundTrip_export_absent=True,
                    equivalence=dict(exact_runtime_header_and_loader_segments_and_allocated_code_data_exports=True,
                                     allocated_sections={'synthetic': True}, program_headers=['synthetic'])))
            with zipfile.ZipFile(apk, 'a') as bundle:
                for name in ('META-INF/MANIFEST.MF', 'META-INF/ONXREL.SF', 'META-INF/ONXREL.RSA'):
                    bundle.writestr(name, b'synthetic, not an actual signature')
            input_path = root / item['input']['name']
            input_path.write_text(json.dumps(original), 'utf-8')
            item['input'].update(bytes=input_path.stat().st_size, sha256=digest(input_path))
            signed = dict(schema=1, passed=True, kind='android-release-signed-input', source_clean=True,
                source_commit=item['source_commit'], abi='x86_64', metadata=original['metadata'],
                original_input_sha256=original['apk_sha256'], original_input_proof_sha256=digest(input_path),
                apk_sha256=digest(apk), apk_bytes=apk.stat().st_size, intended_signer_verified=True,
                signer_role='intended-android-release', certificate_sha256=value['certificate_sha256'], zip_alignment_page_size_kib=16)
            (root/item['signature']['name']).write_text(json.dumps(signed), 'utf-8')
            self.rebind_files(root, value)
        self.rebind_files(root, value)
        return value

    def rebind_files(self, root, value):
        for role in ('predecessor', 'successor'):
            for kind in ('apk', 'signature', 'input'):
                path = root/value[role][kind]['name']
                if path.exists(): value[role][kind].update(bytes=path.stat().st_size, sha256=digest(path))
        (root/'runtime-inputs.json').write_text(json.dumps(value), 'utf-8')
        downloaded = dict(kind='android-signed-runtime-input-download', downloaded_original_bytes_verified=True,
            draft_id=value['draft_id'], manifest_sha256=digest(root/'runtime-inputs.json'),
            files={record['name']: record for role in ('predecessor', 'successor') for record in (value[role][kind] for kind in ('apk', 'signature', 'input'))})
        (root/'download-proof.json').write_text(json.dumps(downloaded), 'utf-8')

    def actual(self, apk, *args, **kwargs):
        return dict(metadata=dict(application_id='cc.kronecker.opennexus', version=kwargs['version'], version_code=kwargs['version_code'],
            min_sdk=24, target_sdk=36, debuggable=False, test_only=False, uses_cleartext_traffic=False, allow_backup=False))

    def test_original_payload_and_actual_sdk_binding_never_claim_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); value=self.fixtures(root)
            with patch.object(subject, 'inspect', side_effect=self.actual) as actual, patch.object(subject, 'elf', return_value=dict(allocated_sections={'synthetic':True},program_headers=['synthetic'])):
                result=subject.bind_pair(root,root,root/'java',root/'binding')
            self.assertEqual(actual.call_count,2)
            self.assertTrue(result['passed']);self.assertFalse(result['actual_GUI_or_upgrade_or_physical_verified'])
            self.assertFalse(result['published'])
            self.assertEqual(result['binaries']['successor']['source_commit'],value['successor']['source_commit'])

    def test_wrong_signer_source_or_signature_proof_link_rejected_before_sdk(self):
        for key,bad in [('certificate_sha256','f'*64),('source_commit','f'*40),('original_input_proof_sha256','f'*64),('kind','android-debug')]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);value=self.fixtures(root);path=root/'predecessor-signature.json'
                signed=json.loads(path.read_bytes());signed[key]=bad;path.write_text(json.dumps(signed),'utf-8');self.rebind_files(root,value)
                with patch.object(subject,'inspect') as actual,self.assertRaises(ReleaseError):subject.bind_pair(root,root,root/'java',root/'binding')
                actual.assert_not_called()

    def test_changed_signed_code_or_original_receipt_refused(self):
        for kind in ('apk','input'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);value=self.fixtures(root);path=root/value['predecessor'][kind]['name'];path.write_bytes(path.read_bytes()+b'changed')
                with patch.object(subject,'inspect') as actual,self.assertRaisesRegex(ReleaseError,'DOWNLOADED_PAIR_CHANGED'):
                    subject.bind_pair(root,root,root/'java',root/'binding')
                actual.assert_not_called()

    def test_actual_metadata_and_native_code_are_rechecked_not_trusted_from_receipt(self):
        for changed in ('metadata','native'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);self.fixtures(root)
                def sdk(*args,**kwargs):
                    value=self.actual(*args,**kwargs)
                    if changed=='metadata':value['metadata']['debuggable']=True
                    return value
                with patch.object(subject,'inspect',side_effect=sdk),patch.object(subject,'elf',return_value=dict(allocated_sections={},program_headers=[])),self.assertRaises(ReleaseError):
                    subject.bind_pair(root,root,root/'java',root/'binding')


if __name__=='__main__':unittest.main()
