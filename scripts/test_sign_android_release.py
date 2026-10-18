from copy import deepcopy
import os
import json
from pathlib import Path
import tempfile
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch
import zipfile

from release_plan import digest, ReleaseError
import sign_android_release as subject
from verify_android_release_package import archive_inventory


class SigningBoundaries(unittest.TestCase):
    def fixture(self, root):
        apk=root/'original.apk'
        with zipfile.ZipFile(apk,'w') as z:
            z.writestr('AndroidManifest.xml', b'synthetic stable manifest')
            z.writestr('classes.dex', b'synthetic dex, never executable')
            z.writestr('assets/original', b'%25\r\noriginal \xe4\xb8\xad\xe6\x96\x87')
            z.writestr('lib/x86_64/libopennexus_mobile.so', b'synthetic ELF placeholder')
        proof=dict(schema=1,passed=True,kind='android-release-input',source_clean=True,source_commit='a'*40,
            apk_sha256=digest(apk),apk_bytes=apk.stat().st_size,abi='x86_64',zip_alignment_page_size_kib=16,
            metadata=dict(application_id='cc.kronecker.opennexus',version='0.1.0-alpha1',version_code=2,
                debuggable=False,test_only=False,uses_cleartext_traffic=False,allow_backup=False),
            inventory=archive_inventory(apk,'x86_64'),native_library=dict(profile='release',
                debug_NativeCredentialProbe_roundTrip_export_absent=True,
                equivalence=dict(exact_runtime_header_and_loader_segments_and_allocated_code_data_exports=True)))
        return apk, proof

    def test_wrong_source_debug_metadata_and_changed_original_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            apk,proof=self.fixture(Path(folder));subject.bind_input(apk,proof)
            for field,value in [('kind','android-debug'),('source_clean',False),('apk_sha256','b'*64)]:
                with self.subTest(field=field),self.assertRaises(ReleaseError):subject.bind_input(apk,dict(proof,**{field:value}))
            for field,value in [('application_id','cc.kronecker.opennexus.dev'),('debuggable',True),('version_code',True)]:
                bad=deepcopy(proof);bad['metadata'][field]=value
                with self.subTest(field=field),self.assertRaises(ReleaseError):subject.bind_input(apk,bad)
            with self.assertRaises(ReleaseError):subject.bind_input(apk,dict(proof,inventory={}))
            with self.assertRaises(ReleaseError):subject.bind_input(apk,dict(proof,native_library={}))

    def test_only_the_expected_signature_files_may_change(self):
        before={'classes.dex':dict(sha256='a'*64,bytes=12),'META-INF/build.properties':dict(sha256='b'*64,bytes=5)}
        after=dict(before,**{name:dict(sha256='c'*64,bytes=100) for name in ('META-INF/MANIFEST.MF','META-INF/ONXREL.SF','META-INF/ONXREL.RSA')})
        result=subject.same_signing_payload(before,after)
        self.assertTrue(result['exact_uncompressed_payload_preserved'])
        for name in before:
            bad=deepcopy(after);bad[name]['sha256']='d'*64
            with self.subTest(name=name),self.assertRaisesRegex(ReleaseError,'NON_SIGNATURE_PAYLOAD_CHANGED'):subject.same_signing_payload(before,bad)
        bad=deepcopy(after);del bad['META-INF/build.properties']
        with self.assertRaises(ReleaseError):subject.same_signing_payload(before,bad)
        bad=dict(after,**{'META-INF/EXTRA.RSA':{}})
        with self.assertRaisesRegex(ReleaseError,'V1_FILES_UNEXPECTED'):subject.same_signing_payload(before,bad)

    def test_hosted_signing_missing_identity_and_existing_output_refused_before_subprocess(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(subject.subprocess,'run') as run:
            root=Path(folder);apk,proof=self.fixture(root);key=root/'key.p12';key.write_bytes(b'not a real key')
            args=(apk,root/'input.json',root/'signed',key,'release','OPENNEXUS_ANDROID_SIGN_TEST','a'*64,root,root/'java')
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','OPENNEXUS_ANDROID_SIGN_TEST':'synthetic-not-secret'}):
                with self.assertRaisesRegex(ReleaseError,'PRIVATE_KEY_LOCAL_ONLY'):subject.sign(*args)
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'},clear=True):
                with self.assertRaisesRegex(ReleaseError,'PASSWORD_ENV_REQUIRED'):subject.sign(*args)
            (root/'signed').mkdir()
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'false','OPENNEXUS_ANDROID_SIGN_TEST':'synthetic-not-secret'}):
                with self.assertRaisesRegex(ReleaseError,'OUTPUT_EXISTS_RECONCILE_ONLY'):subject.sign(*args)
            run.assert_not_called()

    def test_output_is_separate_and_only_verified_payload_gets_a_non_runtime_receipt(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{
                'GITHUB_ACTIONS':'false','OPENNEXUS_ANDROID_SIGN_TEST':'synthetic-not-secret'}):
            root=Path(folder);apk,proof=self.fixture(root);original=digest(apk)
            receipt=root/'input.json';receipt.write_text(json.dumps(proof),'utf-8')
            key=root/'key.p12';key.write_bytes(b'synthetic-not-a-real-key')
            java=root/'java';java.write_bytes(b'not-executable')
            jar=root/'build-tools/36.0.0/lib/apksigner.jar';jar.parent.mkdir(parents=True);jar.write_bytes(b'not-executable')
            commands=[]
            def fake_run(args,**kwargs):
                commands.append(args)
                if 'sign' in args:
                    target=Path(args[args.index('--out')+1]);self.assertNotEqual(target,apk)
                    with zipfile.ZipFile(apk) as source,zipfile.ZipFile(target,'w') as signed:
                        for name in source.namelist():signed.writestr(name,source.read(name))
                        for name in ('META-INF/MANIFEST.MF','META-INF/ONXREL.SF','META-INF/ONXREL.RSA'):
                            signed.writestr(name,b'synthetic, not a real signature')
                return CompletedProcess(args,0,b'synthetic operation only',b'')
            def fake_inspect(target,*args,**kwargs):
                return dict(metadata=proof['metadata'],apk_sha256=digest(target),native_runtime_verified=False)
            with patch.object(subject.subprocess,'run',side_effect=fake_run),patch.object(subject,'inspect_signature',side_effect=fake_inspect):
                value=subject.sign(apk,receipt,root/'signed',key,'release','OPENNEXUS_ANDROID_SIGN_TEST','a'*64,root,java)
            self.assertEqual(digest(apk),original)
            self.assertTrue(value['payload']['exact_uncompressed_payload_preserved'])
            self.assertFalse(value['actual_native_runtime_verified']);self.assertFalse(value['upgrade_verified']);self.assertFalse(value['published'])
            self.assertEqual(value['kind'],'android-release-signed-input')
            self.assertEqual(len(commands),2)
            self.assertFalse(any('synthetic-not-secret' in str(arg) for args in commands for arg in args))
            self.assertIn('env:OPENNEXUS_ANDROID_SIGN_TEST',commands[0])


if __name__ == '__main__': unittest.main()
