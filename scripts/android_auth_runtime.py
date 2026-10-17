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
import shlex
import subprocess
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

    def native_keystore_tests(self):
        # The selected immutable b270 APK already has its five actual native
        # cases preserved. This auth-only scope neither replays nor reports
        # them as new acceptance; it exercises real app/Keystore paths via UI.
        require(self.binding.get('native_tests',{}).get('expected_tests')==5,
            'Auth-only scope requires the fixed native worker test binding')

    def failure_observations(self):
        # A timed-out UI wait is not evidence that its request was never sent.
        # Preserve the original provider counts and native intent metadata for
        # reconciliation without screenshots, raw logs or secret payloads.
        for label,read in (('provider',lambda:self.observe('failure')),
                ('native',lambda:self.snapshot('failure')),
                ('ciphertext-fingerprints',lambda:atomic_json(self.output/'failure-ciphertext-fingerprints.json',
                    self.ciphertext_fingerprints(),exclusive=True))):
            try:read()
            except Exception as error:
                atomic_json(self.output/('failure-'+label+'-unavailable.json'),
                    {'error':type(error).__name__,'no_mutation_retried':True},exclusive=True)

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
        self.record_auth_phase(name,started,result.returncode,result.stdout)

    def record_auth_phase(self,name,started,returncode,output):
        # This instrumentation emits static stages/roles only, even on failure.
        require(len(output)<=1024*1024,'Owned static phase output exceeds its bound')
        text=output.decode('utf-8','replace')
        atomic_json(self.output/('phase-'+str(len(self.phases)+1)+'-'+name+'.json'),
            {'phase':name,'instrument_output':text},exclusive=True)
        require(returncode==0 and 'OPENNEXUS_AUTH_PHASE_OK '+name in text and 'OPENNEXUS_AUTH_PHASE_FAILED' not in text,
            'Actual owned auth UI phase failed; no mutation will be repeated')
        self.phases.append({'phase':name,'duration_ms':round((time.monotonic()-started)*1000),'passed':True})

    def app_is_resumed(self):
        # Never persist the raw Activity dump. It is not an auth/UI artifact.
        value=self.shell('dumpsys','activity','activities').stdout.decode('utf-8','replace')
        return any(APP+'/' in line and ('topResumedActivity' in line or 'mResumedActivity' in line)
            for line in value.splitlines())

    def held_background(self,held_id,original_sealed):
        require_hosted_emulator(self.args.serial)
        require(str(uuid.UUID(held_id))==held_id,'Original held control ID is invalid')
        self.start_app();started=time.monotonic()
        command=('am','instrument','-w','-e','phase','login-b-held',DRIVER+'/.AuthRuntimeProbe')
        process=subprocess.Popen([str(self.adb),'-s',self.args.serial,'shell',shlex.join(command)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output=b'';completed=False
        try:
            deadline=time.monotonic()+35
            while True:
                require(process.poll() is None,'Owned held UI driver ended before actual background')
                ready=self.shell('run-as',DRIVER,'cat','no_backup/owned-auth-held.ready',check=False)
                if ready.returncode==0 and ready.stdout==b'ready\n' and (self.owner/'fault-events'/(held_id+'.json')).is_file():break
                require(time.monotonic()<deadline,'Original held service/driver checkpoint was not observed');time.sleep(.2)
            expect_requests(self.observe('held-before-home'),4,2,1)
            native=self.snapshot('held-before-home')
            selected=native['selection']['operation_id']
            pending=[row for row in native['intents'] if row['operation_id']==selected]
            require(len(pending)==1 and pending[0]['kind']=='login' and pending[0]['phase']=='login_pending'
                and pending[0]['device_id'] is None,'Held native login was already classified or activated')
            require(self.ciphertext_fingerprints()==original_sealed,'Held response changed prior ciphertext before background')
            before=self.shell('pidof',APP).stdout.strip()
            require(re.fullmatch(rb'[1-9][0-9]{0,9}',before) and self.app_is_resumed(),
                'Original app was not actually foreground while the held UI driver remained alive')
            self.shell('input','keyevent','KEYCODE_HOME')
            time.sleep(1)
            require(self.shell('pidof',APP).stdout.strip()==before and not self.app_is_resumed()
                and process.poll() is None,'Actual Home transition changed the process or ended instrumentation early')
            self.control('release');deadline=time.monotonic()+10
            while not (self.owner/'fault-events'/(held_id+'.released.json')).is_file():
                require(time.monotonic()<deadline,'Original held release was not observed');time.sleep(.1)
            self.device('shell','-T','run-as '+DRIVER+' sh -c '+
                "'umask 077; test ! -e no_backup/owned-auth-held.finish && cat > no_backup/owned-auth-held.finish'",
                input=b'finish\n')
            output,_=process.communicate(timeout=30);completed=True
            atomic_json(self.output/'held-background-transition.json',{'same_original_app_process':True,
                'actual_foreground_before_Home':True,'actual_background_after_Home':True,
                'same_UI_instrumentation_alive_until_after_original_release':True,
                'original_native_login_pending_before_Home':True,'original_control_id':held_id,
                'raw_activity_dump_or_credentials_saved':False},exclusive=True)
        finally:
            if not completed:
                if process.poll() is None:process.terminate()
                try:output,_=process.communicate(timeout=10)
                except subprocess.TimeoutExpired:process.kill();output,_=process.communicate(timeout=10)
            if not completed:
                atomic_json(self.output/'held-phase-unfinished.json',{'passed':False,'original_not_retried':True},exclusive=True)
            if output:self.record_auth_phase('login-b-held',started,process.returncode,output)

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
        # refresh-unknown already read the original ID in the same actual UI
        # connection. Do not expect its RAM-only pending panel to survive an
        # instrumentation disconnect. Keep the pending native original intact
        # until the following cold, explicit Current read reconciles it.
        self.auth_phase('current-unusable',cold=True)
        expect_requests(self.observe('cold-unknown'),2,2,1)
        expect_original_recovery(pending,self.snapshot('cold-unknown'))
        require(self.ciphertext_fingerprints()==prior_rotation_ciphertext,'Cold unknown rotation replaced or removed its original ciphertext')
        self.control('normal')
        self.auth_phase('login-a')
        third=self.observe('third-login');expect_requests(third,3,2,1)
        require(len(provider_account(third,0)['devices'])==3,'New explicit recovery login did not create one distinct device')
        original_sealed=self.ciphertext_fingerprints()
        held_id=self.control('hold_login')
        self.held_background(held_id,original_sealed)
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
    except Exception:
        probe.failure_observations()
        raise
    finally:
        # Auth phases deliberately never invoke generic capture()/raw logcat.
        probe.shell('am','force-stop',APP,check=False)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_ANDROID_AUTH_FAILED_'+type(error).__name__,file=__import__('sys').stderr)
        raise SystemExit(1)
