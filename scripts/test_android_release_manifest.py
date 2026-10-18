"""Manifest/certificate contracts only; these fixtures are not built release APKs."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import check_android_release_manifest as subject

VERSION='0.1.0-alpha1';CERTIFICATE='ab'*32
BADGING="package: name='cc.kronecker.opennexus' versionCode='2' versionName='0.1.0-alpha1'\nsdkVersion:'24'\ntargetSdkVersion:'36'\n"
XML='''E: manifest (line=1)
  E: application (line=3)
    A: android:allowBackup(0x01010280)=(type 0x12)0x0
    A: android:usesCleartextTraffic(0x010104ec)=(type 0x12)0x0
    E: activity (line=9)
'''
SIGNATURE='Verified using v2 scheme (APK Signature Scheme v2): true\nSigner #1 certificate SHA-256 digest: '+CERTIFICATE+'\n'

class AndroidReleaseManifestContracts(unittest.TestCase):
    def manifest(self,xml=XML,badging=BADGING):return subject.release_manifest(badging,xml,version=VERSION,version_code=2)

    def test_stable_identity_and_explicit_existing_storage_transport_flags(self):
        value=self.manifest();self.assertEqual(value['application_id'],'cc.kronecker.opennexus')
        for flag in ('debuggable','test_only','uses_cleartext_traffic','allow_backup'):self.assertIs(value[flag],False)

    def test_debug_suffix_and_wrong_version_or_code_are_rejected(self):
        for before,after in [('cc.kronecker.opennexus','cc.kronecker.opennexus.dev'),("versionCode='2'","versionCode='1'"),(VERSION,'0.1.0-dev1')]:
            with self.subTest(after=after),self.assertRaises(Exception):self.manifest(badging=BADGING.replace(before,after))

    def test_debug_and_test_only_reject_every_nonzero_boolean(self):
        for flag in ('debuggable','testOnly'):
            for true in ('1','ffffffff'):
                xml=XML.replace('    E: activity','    A: android:'+flag+'(0x0101000f)=(type 0x12)0x'+true+'\n    E: activity')
                with self.subTest(flag=flag,true=true),self.assertRaises(Exception):self.manifest(xml)

    def test_explicit_false_debug_flags_are_allowed(self):
        xml=XML.replace('    E: activity','    A: android:debuggable(0x0101000f)=(type 0x12)0x0\n    A: android:testOnly(0x01010272)=(type 0x12)0x0\n    E: activity')
        self.assertFalse(self.manifest(xml)['debuggable'])

    def test_transport_and_backup_flags_cannot_be_true_missing_malformed_or_duplicated(self):
        for flag in ('usesCleartextTraffic','allowBackup'):
            line=next(v for v in XML.splitlines() if 'android:'+flag+'(' in v)
            for changed in (line.replace('0x0','0xffffffff'),'',line.replace('(type 0x12)','(type 0x10)'),line+'\n'+line):
                with self.subTest(flag=flag,changed=changed),self.assertRaises(Exception):self.manifest(XML.replace(line,changed))

    def test_child_activity_attributes_cannot_supply_application_policy(self):
        xml=XML.replace('    A: android:usesCleartextTraffic(0x010104ec)=(type 0x12)0x0\n','')+'      A: android:usesCleartextTraffic(0x010104ec)=(type 0x12)0x0\n'
        with self.assertRaises(Exception):self.manifest(xml)

    def test_single_application_and_no_instrumentation_or_unreviewed_network_override(self):
        for xml in (XML+XML,XML+'  E: instrumentation (line=20)\n',XML.replace('    E: activity','    A: android:networkSecurityConfig(0x01010527)=@0x7f010001\n    E: activity')):
            with self.subTest(xml=xml),self.assertRaises(Exception):self.manifest(xml)

    def test_signer_must_be_one_exact_certificate_and_support_api24_v2(self):
        self.assertEqual(subject.release_certificate(SIGNATURE,CERTIFICATE.upper()),CERTIFICATE)
        for text in (SIGNATURE.replace(CERTIFICATE,'cd'*32),SIGNATURE+f'Signer #2 certificate SHA-256 digest: {CERTIFICATE}\n',SIGNATURE.replace(': true',': false')):
            with self.subTest(text=text),self.assertRaises(Exception):subject.release_certificate(text,CERTIFICATE)

    def test_cli_inspection_uses_only_bounded_readers_and_keeps_runtime_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);apk=root/'owned.apk';apk.write_bytes(b'owned synthetic test fixture')
            tools=root/'sdk/build-tools/36.0.0';(tools/'lib').mkdir(parents=True)
            (tools/('aapt2.exe' if subject.os.name=='nt' else 'aapt2')).touch();(tools/'lib/apksigner.jar').touch()
            with patch.object(subject.subprocess,'check_output',side_effect=[BADGING.encode(),XML.encode(),SIGNATURE.encode()]) as run:
                result=subject.inspect(apk,root/'sdk',Path('java'),version=VERSION,version_code=2,expected_sha256=subject.digest(apk),certificate=CERTIFICATE)
            self.assertTrue(result['passed']);self.assertEqual(run.call_count,3)
            for key in ('native_runtime_verified','source_bytes_verified','upgrade_verified','physical_phone_verified'):self.assertFalse(result[key])
            self.assertIn('-Xmx256m',run.call_args_list[-1].args[0]);self.assertEqual(run.call_args_list[-1].kwargs['timeout'],90)

    def test_hash_mismatch_is_rejected_before_any_sdk_command(self):
        with tempfile.TemporaryDirectory() as directory:
            apk=Path(directory)/'owned.apk';apk.write_bytes(b'owned')
            with patch.object(subject.subprocess,'check_output') as run,self.assertRaises(Exception):
                subject.inspect(apk,Path(directory),Path('java'),version=VERSION,version_code=2,expected_sha256='0'*64,certificate=CERTIFICATE)
            run.assert_not_called()

    def test_an_output_created_during_inspection_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'original.json'
            args=['checker','--apk','owned.apk','--sdk','sdk','--java','java','--version',VERSION,
                '--version-code','2','--expected-sha256','a'*64,'--certificate-sha256',CERTIFICATE,'--output',str(output)]
            def raced(*_args,**_kwargs):
                output.write_bytes(b'original receipt created during inspection');return {'passed':True}
            with patch('sys.argv',args),patch.object(subject,'inspect',side_effect=raced),self.assertRaises(Exception):subject.main()
            self.assertEqual(output.read_bytes(),b'original receipt created during inspection')

if __name__=='__main__':unittest.main()
