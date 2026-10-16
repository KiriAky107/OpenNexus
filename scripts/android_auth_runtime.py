"""Actual immutable Android APK auth UI against the exclusive hosted HTTPS origin.

Credentials are stdin-only to a private test-driver file. Artifact evidence is
metadata-only: no screenshots, AX/XML values, token store bytes or raw logcat.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid
from android_auth_fixture import arm_control, atomic_json, observe_provider, owned_root, read_json, require_hosted
from android_runtime_probe import APP, DRIVER, Probe, build_job_origin, require, require_hosted_emulator, validate_binding


def provider_account(observation,index):
    users=observation.get('users',[])
    require(len(users)==2,'Owned provider identity count changed')
    return users[index]


def expect_requests(observation,login,refresh,logout):
    counts=observation['requests']['counts']
    require(counts=={kind+':'+str(status):amount for kind,status,amount in
        [('login',200,login),('refresh',200,refresh),('logout',204,logout)] if amount},
        'Original auth request counts differ; no mutation is retried')


def active_intent(snapshot,kind,device):
    selected=snapshot.get('selection')
    require(selected is not None,'Native selected connection is absent')
    rows=[item for item in snapshot['intents'] if item['operation_id']==selected['operation_id']]
    require(len(rows)==1 and rows[0]['phase']=='active' and rows[0]['kind']==kind and rows[0]['device_id']==device,
        'Native selected connection did not confirm the actual owned device')
    return rows[0]


def expect_original_recovery(before,after):
    require(before['selection']==after['selection'],'Original native selection was replaced during recovery')
    selected=before['selection']['operation_id']
    require(len(before['intents'])==len(after['intents']),'Recovery created a new native operation')
    for original,recovered in zip(before['intents'],after['intents']):
        expected=dict(original)
        if original['operation_id']==selected:
            require(original['phase']=='refresh_pending' and recovered['phase']=='unknown',
                'Original unknown refresh was activated instead of reconciled')
            expected['phase']='unknown'
        require(recovered==expected,'Original native operation fields changed during recovery')


class AuthProbe(Probe):
    def __init__(self,args,apk,binding,owner):
        super().__init__(args,apk,binding)
        self.owner=owner
        self.observations=[]

    def observe(self,label):
        # The fixed service implementation opens repeatable-read/read-only PG;
        # it selects only owned device metadata and session counts.
        result=observe_provider(self.owner)
        atomic_json(self.output/(label+'-provider.json'),result,exclusive=True)
        return result

    def start_app(self):
        self.shell('am','start','-W','-n',self.activity)

    def auth_phase(self,name,*,cold=False):
        require(re.fullmatch(r'[a-z-]{1,40}',name),'Invalid owned auth phase')
        if cold:self.force_stop()
        self.start_app()
        started=time.monotonic()
        result=self.shell('am','instrument','-w','-e','phase',name,DRIVER+'/.AuthRuntimeProbe',timeout=180,check=False)
        # This instrumentation emits static stages/roles only, even on failure.
        text=result.stdout.decode('utf-8','replace')
        atomic_json(self.output/('phase-'+str(len(self.phases)+1)+'-'+name+'.json'),
            {'phase':name,'instrument_output':text},exclusive=True)
        require(result.returncode==0 and 'OPENNEXUS_AUTH_PHASE_OK '+name in text and 'OPENNEXUS_AUTH_PHASE_FAILED' not in text,
            'Actual owned auth UI phase failed; no mutation will be repeated')
        self.phases.append({'phase':name,'duration_ms':round((time.monotonic()-started)*1000),'passed':True})

    def snapshot(self,label):
        paths=self.shell('run-as',APP,'find','.','-name','mobile-sync-auth.sqlite3','-type','f').stdout.decode().splitlines()
        require(len(paths)==1 and re.fullmatch(r'\./(?:[A-Za-z0-9_.-]+/)*mobile-sync-auth\.sqlite3',paths[0]),
            'Native auth database origin is ambiguous')
        root=paths[0].removesuffix('mobile-sync-auth.sqlite3')
        folder=Path(tempfile.mkdtemp(prefix='auth-metadata-',dir=self.tools));database=folder/'index.sqlite3'
        for suffix in ('','-wal','-shm'):
            response=self.private(root+'mobile-sync-auth.sqlite3'+suffix,check=False)
            if response.returncode==0:Path(str(database)+suffix).write_bytes(response.stdout)
        columns=('operation_id','endpoint','account','device_name','kind','phase','device_id','generation','server_logout_confirmed','previous_operation')
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'Native auth metadata integrity failed')
            intents=[dict(zip(columns,row)) for row in db.execute('SELECT '+','.join(columns)+' FROM auth_intents ORDER BY rowid')]
            rows=db.execute('SELECT revision,endpoint,account,operation_id,generation FROM auth_selection').fetchall()
            require(len(rows)<=1,'Native selection is ambiguous')
            selection=dict(zip(('revision','endpoint','account','operation_id','generation'),rows[0])) if rows else None
        value={'intents':intents,'selection':selection,'integrity':'ok','secret_payload_selected':False}
        atomic_json(self.output/(label+'-native.json'),value,exclusive=True);return value

    def ciphertext_fingerprints(self):
        # Observe AtomicFile ciphertext hashes without exporting ciphertext or
        # asking the native store to decrypt any payload outside the real UI.
        result=self.shell('run-as',APP,'find','no_backup/sync-credentials','-type','f',check=False)
        if result.returncode:
            require(self.shell('run-as',APP,'test','!','-e','no_backup/sync-credentials',check=False).returncode==0,
                'Native ciphertext inventory is unavailable')
            return {}
        values={}
        for path in result.stdout.decode().splitlines():
            require(re.fullmatch(r'no_backup/sync-credentials/[0-9a-f]{64}/[0-9a-f]{64}\.bin',path),
                'Unexpected native secure-store file')
            fields=self.shell('run-as',APP,'sha256sum',path).stdout.decode().split()
            require(len(fields)==2 and re.fullmatch(r'[0-9a-f]{64}',fields[0]),'Native ciphertext fingerprint is unavailable')
            values[path]=fields[0]
        return values

    def control(self,mode):
        current=read_json(self.owner/'control.json');operation=str(uuid.uuid4())
        receipt=arm_control(self.owner,operation,mode,current['revision'])
        atomic_json(self.output/('control-'+mode+'-'+operation+'.json'),receipt,exclusive=True)
        return operation

    def stage_owned_input(self,configuration):
        require_hosted_emulator(self.args.serial)
        require(set(configuration)=={'endpoint','accounts'} and re.fullmatch(
            r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com',configuration['endpoint']),
            'Exclusive owned HTTPS input required')
        accounts=configuration['accounts'];require(len(accounts)==2,'Exclusive owned identities required')
        for index,account in enumerate(accounts):
            require(set(account)=={'username','password','device_name'} and re.fullmatch(
                'android-owned-'+('a' if index==0 else 'b')+'-[0-9a-f]{12}',account['username'])
                and re.fullmatch(r'[A-Za-z0-9_-]{40,100}',account['password'])
                and account['device_name'].startswith('Owned Android '),'Exclusive owned identity input required')
        self.shell('run-as',DRIVER,'mkdir','-p','no_backup')
        self.device('shell','-T','run-as '+DRIVER+' sh -c '+"'umask 077; test ! -e no_backup/owned-auth.json && cat > no_backup/owned-auth.json'",
            input=json.dumps(configuration).encode())

    def run(self):
        self.boot()
        require(self.binding.get('native_auth_gateway',{}).get('present') is True,'Fixed APK has no native auth gateway')
        configuration=read_json(self.owner/'device-private.json')
        # Passwords never appear in argv, ADB text commands or artifact files.
        self.stage_owned_input(configuration)
        baseline=self.observe('initial');expect_requests(baseline,0,0,0)
        require(all(not user['devices'] for user in baseline['users']),'Owned account already has a device; no login replayed')
        self.auth_phase('login-a',cold=True)
        login=self.observe('login');expect_requests(login,1,0,0)
        user=provider_account(login,0);require(len(user['devices'])==1 and user['session_counts'][0]['sessions']==1,'Actual server login did not create one session')
        device=user['devices'][0]['id'];first=self.snapshot('login');active_intent(first,'login',device)
        sealed=self.ciphertext_fingerprints();require(bool(sealed),'Native login did not leave secure ciphertext')
        self.auth_phase('current',cold=True)
        cold=self.observe('cold');expect_requests(cold,1,0,0)
        require(self.snapshot('cold')==first and self.ciphertext_fingerprints()==sealed,'Cold native current read changed original credentials or intents')
        self.auth_phase('refresh')
        refreshed=self.observe('refresh');expect_requests(refreshed,1,1,0)
        require(provider_account(refreshed,0)['devices']==user['devices'],'Refresh created a new device')
        active_intent(self.snapshot('refresh'),'refresh',device)
        self.auth_phase('logout')
        logged_out=self.observe('logout');expect_requests(logged_out,1,1,1)
        require(provider_account(logged_out,0)['session_counts']==[{'device_id':device,'sessions':0}],
            'Actual server DELETE did not remove the original session')
        require(not self.ciphertext_fingerprints(),'Confirmed logout did not erase original native credentials')
        self.snapshot('logout')
        self.auth_phase('login-a',cold=True)
        second=self.observe('second-login');expect_requests(second,2,1,1)
        require(len(provider_account(second,0)['devices'])==2,'Explicit new login did not create a distinct device')
        prior_rotation_ciphertext=self.ciphertext_fingerprints()
        self.control('drop_refresh')
        self.auth_phase('refresh-unknown')
        unknown=self.observe('lost-refresh');expect_requests(unknown,2,2,1)
        pending=self.snapshot('lost-refresh');selected=pending['selection']['operation_id']
        require(any(row['operation_id']==selected and row['kind']=='refresh' and row['phase']=='refresh_pending' for row in pending['intents']),
            'Lost refresh did not preserve its original unconfirmed native intent')
        require(self.ciphertext_fingerprints()==prior_rotation_ciphertext,'Lost refresh changed the retained original ciphertext')
        self.auth_phase('acknowledge-unknown')
        checked=self.observe('original-read');expect_requests(checked,2,2,1)
        # Original-ID lookup is read-only. The subsequent explicit Current
        # recovery classifies its retained mismatched rotation as unknown.
        expect_original_recovery(pending,self.snapshot('original-read'))
        self.auth_phase('current-unusable',cold=True)
        expect_requests(self.observe('cold-unknown'),2,2,1)
        require(self.ciphertext_fingerprints()==prior_rotation_ciphertext,'Cold unknown rotation replaced or removed its original ciphertext')
        self.control('normal')
        self.auth_phase('login-a')
        third=self.observe('third-login');expect_requests(third,3,2,1)
        require(len(provider_account(third,0)['devices'])==3,'New explicit recovery login did not create one distinct device')
        original_sealed=self.ciphertext_fingerprints()
        held_id=self.control('hold_login')
        self.auth_phase('login-b-held')
        deadline=time.monotonic()+35
        while not (self.owner/'fault-events'/(held_id+'.json')).exists():
            require(time.monotonic()<deadline,'Original held login did not reach the fixed service');time.sleep(.1)
        before_pid=self.shell('pidof',APP).stdout.strip();require(re.fullmatch(rb'[1-9][0-9]{0,9}',before_pid),'Owned app PID is unknown')
        self.shell('input','keyevent','KEYCODE_HOME')
        # This is a real Activity transition; no force-stop, reinstall or app
        # data change is used to manufacture a rejected late response.
        time.sleep(1);require(self.shell('pidof',APP).stdout.strip()==before_pid,'Activity background changed the owned process')
        self.control('release')
        deadline=time.monotonic()+10
        while not (self.owner/'fault-events'/(held_id+'.released.json')).exists():
            require(time.monotonic()<deadline,'Original held response release was not observed');time.sleep(.1)
        self.auth_phase('current-unusable')
        final=self.observe('late-background');expect_requests(final,4,2,1)
        require(len(provider_account(final,1)['devices'])==1,'Held login did not commit exactly once on the independent service')
        late=self.snapshot('late-background')
        b=[row for row in late['intents'] if row['account']==configuration['accounts'][1]['username']]
        require(len(b)==1 and b[0]['phase']=='unknown' and b[0]['device_id'] is None,
            'Backgrounded original login activated or lost its original unconfirmed intent')
        require(self.ciphertext_fingerprints()==original_sealed,'Late login changed the prior native ciphertext')
        atomic_json(self.output/'auth-runtime-proof.json',{'passed':True,'source_commit':self.args.commit,
            'tools_commit':self.args.tools_commit,'apk_sha256':self.binding['sha256'],'build_origin':self.args.build_origin,
            'actual_platform_TLS_and_production_service_login':True,'actual_cold_native_secure_resume':True,
            'actual_refresh_same_device':True,'actual_session_DELETE_and_local_erasure':True,
            'lost_refresh_original_read_no_repost_or_old_credential_activation':True,
            'same_process_background_late_response_no_activation_or_store_change':True,'phases':self.phases,
            'physical_phone':False,'bidirectional_vault_transfer':False,'server_device_revocation':False,
            'invalid_certificate_classification':False,'passwords_tokens_screenshots_or_raw_UI_artifact':False},exclusive=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('bundle','sdk','output','source-checkout'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('commit','tools-commit','build-run-id'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--serial',default='emulator-5562');args=parser.parse_args()
    require_hosted();require_hosted_emulator(args.serial);owner=owned_root()
    require(read_json(owner/'owner-state.json')['result']=='ready','Owned HTTPS origin is not ready')
    apk,binding=validate_binding(args.bundle,args.commit)
    from release_plan import build_source
    require(re.fullmatch(r'[0-9a-f]{40}',args.commit) and re.fullmatch(r'[0-9a-f]{40}',args.tools_commit),'Full immutable sources required')
    build_source(Path(__file__).resolve().parents[1],args.tools_commit)
    require(json.loads((args.bundle/'source-provenance.json').read_text('utf-8'))==build_source(args.source_checkout.resolve(),args.commit),
        'Compiled source differs from the actual APK')
    args.build_origin=build_job_origin(args.build_run_id,args.commit)
    probe=AuthProbe(args,apk,binding,owner)
    try:probe.run()
    finally:
        # Auth phases deliberately never invoke generic capture()/raw logcat.
        probe.shell('am','force-stop',APP,check=False)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_ANDROID_AUTH_FAILED_'+type(error).__name__,file=__import__('sys').stderr)
        raise SystemExit(1)
