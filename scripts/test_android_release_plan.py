"""Synthetic APK/receipt fixtures and real Git blobs, never actual device or release proof."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,subprocess,tempfile,unittest
from android_release_contract import PRODUCT,PACKAGE,validate
from github_release import Publisher
from release_plan import Plan,ReleaseError,atomic_json,check_local_source,digest,release_tag
from test_github_release import FakeGitHub,load_prepare


class AndroidReleasePlans(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='owned-android-release-plan-');self.root=Path(self.temp.name)
        self.repo=self.root/'repository';self.repo.mkdir()
        for args in [('init',),('config','user.email','fixture@example.invalid'),('config','user.name','Android release fixture')]:
            subprocess.run(['git',*args],cwd=self.repo,check=True,capture_output=True)
        (self.repo/'README.md').write_bytes(b'original-source\r\n');self.old=self.commit()
        (self.repo/'README.md').write_bytes(b'actual-different-source\r\n');self.sha=self.commit()
        self.apk=self.root/'synthetic-not-installable.apk';self.apk.write_bytes(b'fixture-only-not-an-actual-APK')
        self.cert='1'*64;self.meta=dict(application_id=PACKAGE,abi='arm64-v8a',version_code=2,certificate_sha256=self.cert)
        self.proof=dict(kind='android-release',passed=True,source_commit=self.sha,source_clean=True,version='0.1.0-alpha1',android=self.meta,
            apk_sha256=digest(self.apk),build_source_verified=True,debug_signing=False,signer_role='intended-android-release',
            release_binaries={abi:dict(abi=abi,source_commit=self.sha,apk_sha256=digest(self.apk) if abi=='arm64-v8a' else '3'*64,
                application_id=PACKAGE,certificate_sha256=self.cert,version_code=2,manifest_nondebuggable=True,
                compiled_source_verified=True,signing_payload_verified=True) for abi in ('arm64-v8a','x86_64')},
            native_runtime=dict(passed=True,abi='x86_64',source_commit=self.sha,apk_sha256='3'*64,application_id=PACKAGE,
                certificate_sha256=self.cert,version_code=2,nondebuggable_installed_binary=True,production_commands_verified=True,
                cold_originals_preserved=True,device_kind='owned-hosted-emulator',physical_phone_verified=False),
            upgrade=dict(passed=True,abi='x86_64',application_id=PACKAGE,after_source_commit=self.sha,after_apk_sha256='3'*64,
                before_source_commit=self.old,before_apk_sha256=hashlib.sha256(b'older-different-fixture').hexdigest(),certificate_sha256=self.cert,
                before_version_code=1,after_version_code=2,original_data_and_drafts_preserved=True,package_manager_update_without_clear_data=True))
        self.plan=self.prepare('0.1.0-alpha1','prepared')

    def tearDown(self):self.temp.cleanup()

    def commit(self):
        subprocess.run(['git','add','README.md'],cwd=self.repo,check=True,capture_output=True)
        subprocess.run(['git','commit','-m','owned source'],cwd=self.repo,check=True,capture_output=True)
        return subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo,text=True).strip()

    def prepare(self,version,name,proof=None,**options):
        value=deepcopy(proof or self.proof);value['version']=version;verification=self.root/(name+'-verification.json');atomic_json(verification,value)
        notes=self.root/(name+'-notes.md');names=[f'OpenNexus_Android_{version}_arm64-v8a.apk',f'OpenNexus_Android_{version}_source.zip','SHA256SUMS.txt']
        body=f'# {PRODUCT} {version}\n\n## 更新内容\n\n- '+('拥有的合成说明用于校验固定原件和标签，没有真实安装或发布声明。'*3)+'\n\n## English\n\n- '+('This is only an owned synthetic release test fixture. It binds source inputs, named immutable artifacts and explicit device scope, without claiming a real Android installation or a published download. '*4)+'\n\n'
        body+='\n'.join('https://github.com/owner/project/releases/download/'+release_tag(PRODUCT,version)+'/'+n for n in names)+'\n';notes.write_text(body,'utf-8')
        result=load_prepare().prepare(self.repo,self.root/name,repository='owner/project',product=PRODUCT,version=version,commit=self.sha,
            notes=notes,verification=verification,installer=self.apk,**options)
        self.assertFalse(result['remote_modified']);return Plan(result['plan'])

    def mutate(self,proof=None,data=None):
        value=deepcopy(proof if proof is not None else self.proof);record=self.plan.base/'verification.json';atomic_json(record,value)
        plan=deepcopy(data or self.plan.data);plan['verification']['sha256']=digest(record);atomic_json(self.plan.path,plan)
        return Plan(self.plan.path)

    def test_namespaced_tag_real_source_blobs_and_three_phone_assets_are_independent(self):
        self.assertEqual(self.plan.data['tag'],'android-v0.1.0-alpha1');self.assertTrue(self.plan.prerelease)
        self.assertFalse(self.plan.make_latest);self.assertFalse(self.plan.android['physical_phone_verified'])
        self.assertEqual({a['name'] for a in self.plan.data['assets']},{'OpenNexus_Android_0.1.0-alpha1_arm64-v8a.apk','OpenNexus_Android_0.1.0-alpha1_source.zip','SHA256SUMS.txt'})
        check_local_source(self.plan,self.repo)

    def test_actual_publisher_preserves_desktop_latest_even_for_android_stable(self):
        for plan in (self.plan,self.prepare('0.1.0','prepared-stable')):
            api=FakeGitHub(plan);api.latest={'id':42,'tag_name':'v0.6.0','prerelease':False,'draft':False}
            publisher=lambda:Publisher(Plan(plan.path),api,clock=lambda:api.now)
            publisher().stage();result=publisher().publish();self.assertFalse(result['latest']);self.assertEqual(api.latest['id'],42)
            self.assertEqual(result['formal'],not plan.prerelease);count=len(api.writes)
            publisher().publish();self.assertEqual(len(api.writes),count);self.assertEqual((api.uploads,api.tag_writes),(3,1))

    def test_unknown_publish_response_uses_original_release_and_asset_ids(self):
        api=FakeGitHub(self.plan);api.latest={'id':42};publisher=lambda:Publisher(Plan(self.plan.path),api,clock=lambda:api.now)
        publisher().stage();api.fault='publish-after'
        with self.assertRaisesRegex(ReleaseError,'REMOTE_OUTCOME_UNKNOWN'):publisher().publish()
        original_id=api.release['id'];count=len(api.writes);publisher().publish()
        self.assertEqual(api.release['id'],original_id);self.assertEqual((api.uploads,api.tag_writes),(3,1));self.assertEqual(len(api.writes),count)

    def test_debug_or_manifest_only_or_component_receipts_cannot_approve_release(self):
        for kind in ('android-debug','android-release-input','manifest-only'):
            with self.subTest(kind=kind),self.assertRaisesRegex(ReleaseError,'VERIFICATION_KIND_OR_SOURCE'):self.mutate(dict(self.proof,kind=kind))
        for key,value in [('source_clean',False),('build_source_verified',False),('debug_signing',True),('signer_role','local-QA')]:
            with self.subTest(key=key),self.assertRaises(ReleaseError):self.mutate(dict(self.proof,**{key:value}))

    def test_wrong_apk_or_certificate_or_missing_actual_runtime_is_refused(self):
        for key,value in [('apk_sha256','2'*64),('native_runtime',{})]:
            with self.subTest(key=key),self.assertRaises(ReleaseError):self.mutate(dict(self.proof,**{key:value}))
        for key,value in [('application_id',PACKAGE+'.dev'),('apk_sha256','2'*64),('certificate_sha256','2'*64),('source_commit',self.old),
            ('nondebuggable_installed_binary',False),('production_commands_verified',False),('cold_originals_preserved',False)]:
            bad=deepcopy(self.proof);bad['native_runtime'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ReleaseError,'ACTUAL_RUNTIME_REQUIRED'):self.mutate(bad)

    def test_reinstall_same_source_or_wrong_signer_is_not_upgrade(self):
        for key,value in [('before_source_commit',self.sha),('before_apk_sha256','3'*64),('certificate_sha256','2'*64),
            ('before_version_code',True),('before_version_code',2),('package_manager_update_without_clear_data',False),('original_data_and_drafts_preserved',False)]:
            bad=deepcopy(self.proof);bad['upgrade'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ReleaseError,'ACTUAL_UPGRADE_REQUIRED'):self.mutate(bad)

    def test_hosted_emulator_cannot_silently_turn_into_physical_phone_evidence(self):
        for kind,physical in [('owned-hosted-emulator',True),('owned-physical-device',False),('unspecified',False),('owned-hosted-emulator',0)]:
            bad=deepcopy(self.proof);bad['native_runtime'].update(device_kind=kind,physical_phone_verified=physical)
            with self.subTest(kind=kind,physical=physical),self.assertRaisesRegex(ReleaseError,'DEVICE_SCOPE_REQUIRED'):self.mutate(bad)

    def test_hosted_x86_proof_binds_both_real_binaries_without_claiming_the_arm64_asset_executed(self):
        self.assertEqual(self.plan.android['runtime_abi'],'x86_64')
        self.assertFalse(self.plan.android['phone_asset_executed_in_this_runtime'])
        self.assertNotEqual(self.proof['native_runtime']['apk_sha256'],self.proof['apk_sha256'])
        for field,value in [('source_commit',self.old),('certificate_sha256','9'*64),('compiled_source_verified',False),('signing_payload_verified',False)]:
            bad=deepcopy(self.proof);bad['release_binaries']['arm64-v8a'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ReleaseError,'ABI_SOURCE_OR_SIGNER_BINDING'):self.mutate(bad)

    def test_missing_or_aliased_abi_pair_and_upgrade_abi_substitution_are_refused(self):
        bad=deepcopy(self.proof);bad['release_binaries'].pop('arm64-v8a')
        with self.assertRaisesRegex(ReleaseError,'SAME_SOURCE_ABI_PAIR_REQUIRED'):self.mutate(bad)
        bad=deepcopy(self.proof);bad['release_binaries']['x86_64']['apk_sha256']=digest(self.apk)
        with self.assertRaisesRegex(ReleaseError,'PHONE_ABI_BINDING'):self.mutate(bad)
        bad=deepcopy(self.proof);bad['upgrade']['abi']='arm64-v8a'
        with self.assertRaisesRegex(ReleaseError,'ACTUAL_UPGRADE_REQUIRED'):self.mutate(bad)

    def test_desktop_tag_or_old_release_replacement_is_refused_before_new_plan(self):
        data=deepcopy(self.plan.data);data['tag']='v0.1.0-alpha1'
        with self.assertRaisesRegex(ReleaseError,'TAG_OR_COMMIT_INVALID'):self.mutate(data=data)
        with self.assertRaisesRegex(ReleaseError,'REPLACEMENT_FORBIDDEN'):
            self.prepare('0.1.0-alpha1','not-created',original={'release_id':1})
        self.assertFalse((self.root/'not-created').exists())

if __name__=='__main__':unittest.main()
