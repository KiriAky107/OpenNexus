from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest,warnings,zipfile
import verify_android_release_package as subject
from test_android_release_native import library


class ReleasePackageInputs(unittest.TestCase):
    def apk(self,path,*,abi='arm64-v8a',extra=()):
        with zipfile.ZipFile(path,'w') as z:
            for name,raw in [('AndroidManifest.xml',b'owned-placeholder-not-actual-manifest'),('classes.dex',b'owned-placeholder-not-actual-dex'),
                (f'lib/{abi}/libopennexus_mobile.so',library()),*extra]:
                # ZipInfo normally rewrites Windows separators when constructed.
                entry=zipfile.ZipInfo('placeholder');entry.filename=name;z.writestr(entry,raw)

    def test_exact_split_archive_is_observed_without_runtime_or_signature_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'input.apk';self.apk(p);result=subject.archive_inventory(p,'arm64-v8a')
            self.assertEqual(set(result),{'AndroidManifest.xml','classes.dex','lib/arm64-v8a/libopennexus_mobile.so'})
            self.assertEqual(result['lib/arm64-v8a/libopennexus_mobile.so']['bytes'],len(library()))
            self.assertNotIn('passed',result)

    def test_extra_native_abi_or_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'input.apk'
            for name in ('lib/x86_64/libopennexus_mobile.so','lib/arm64-v8a/libother.so'):
                self.apk(p,extra=[(name,b'other')])
                with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'SPLIT_ABI_SET'):subject.archive_inventory(p,'arm64-v8a')
            self.apk(p,abi='x86_64')
            with self.assertRaisesRegex(RuntimeError,'SPLIT_ABI_SET'):subject.archive_inventory(p,'arm64-v8a')

    def test_duplicate_or_traversal_or_symlink_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'input.apk'
            for name in ('../elsewhere','/absolute','C:/user','folder\\user'):
                self.apk(p,extra=[(name,b'data')])
                with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'ZIP_PATH'):subject.archive_inventory(p,'arm64-v8a')
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning);self.apk(p,extra=[('classes.dex',b'different')])
            with self.assertRaisesRegex(RuntimeError,'ZIP_ENTRIES'):subject.archive_inventory(p,'arm64-v8a')
            self.apk(p)
            with zipfile.ZipFile(p,'a') as z:
                entry=zipfile.ZipInfo('symbolic');entry.external_attr=0o120777<<16;z.writestr(entry,b'elsewhere')
            with self.assertRaisesRegex(RuntimeError,'ZIP_LINK'):subject.archive_inventory(p,'arm64-v8a')

    def test_tls_origin_keeps_the_existing_upstream_hashes_and_exact_two_extensions(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'origin.json'
            value=dict(coordinate=subject.TLS_COORDINATE,upstream_commit=subject.TLS_UPSTREAM,
                artifacts=[dict(extension=ext,sha256=sha,bytes=size) for ext,(sha,size) in subject.TLS_ARTIFACTS.items()])
            p.write_text(json.dumps(value));self.assertEqual(subject.tls_origin(p),value)
            for change in ('upstream','digest','size','duplicate'):
                bad=json.loads(json.dumps(value))
                if change=='upstream':bad['upstream_commit']='other'
                elif change=='digest':bad['artifacts'][0]['sha256']='0'*64
                elif change=='size':bad['artifacts'][0]['bytes']+=1
                else:bad['artifacts'][1]=bad['artifacts'][0]
                p.write_text(json.dumps(bad))
                with self.subTest(change=change),self.assertRaises(RuntimeError):subject.tls_origin(p)

    def test_debug_profile_cannot_be_used_as_a_release_native_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'aarch64-linux-android/debug/libopennexus_mobile.so';path.parent.mkdir(parents=True);path.write_bytes(library())
            with patch.object(subject.subprocess,'check_output') as command,self.assertRaisesRegex(RuntimeError,'NATIVE_INPUT_MISSING'):
                subject.compiled_release(root/'any.apk',root,'arm64-v8a','llvm-nm')
            command.assert_not_called()

if __name__=='__main__':unittest.main()
