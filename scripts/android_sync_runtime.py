"""First actual fixed-APK foreground Sync exchange on exclusive hosted providers.

This scope proves neither a desktop product pair nor the remaining cancellation,
conflict, recovery, physical-phone or upgrade cases. Original intents and failures
are retained; no failed UI mutation is automatically repeated.
"""
import argparse
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import ssl
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from android_auth_fixture import atomic_json, owned_root, read_json, require_hosted
from android_auth_runtime import AuthProbe, active_intent
from android_runtime_probe import APP, DRIVER, DRAFT, build_job_origin, require, require_hosted_emulator, validate_binding
from android_sync_fixture import observe_provider
from android_sync_setup import Setup
from android_sync_peer import Peer

PEER_NOTE='# 来自独立 HTTP 客户端 😀\n\n保持 Markdown 原文。\n'.encode()


def validate_upload(provider, native, device_id):
    """Match actual original operation IDs across UI/native/PG, never just counts."""
    revisions=provider['rows']['revisions'];jobs=native['jobs']
    expected_hash=hashlib.sha256(DRAFT.encode()).hexdigest()
    require(0<len(revisions)==len(jobs)<=4,'Original upload operation count differs')
    require(len({item['operation_id'] for item in revisions})==len(revisions),'A revision operation was duplicated')
    by_id={row['operation_id']:row for row in jobs}
    require(len(by_id)==len(jobs),'Native operation IDs were duplicated')
    require(all(row['state']=='acked' and row['error'] is None for row in jobs),
        'Provider revisions alone do not prove original native acknowledgement')
    attempts={row['operation_id']:row for row in native['attempts']}
    issuers={row['operation_id']:row['device_id'] for row in native['issuers']}
    require(len(attempts)==len(native['attempts'])==len(jobs) and set(attempts)==set(by_id)
        and len(issuers)==len(native['issuers'])==len(jobs) and set(issuers)==set(by_id),
        'Original native attempt/issuing-device records do not match the uploaded jobs')
    require(all(row['attempts']==1 and row['outcome']=='succeeded' and row['error'] is None for row in attempts.values())
        and all(issuer==device_id for issuer in issuers.values()),'Original native attempt repeated, failed or changed device')
    for remote in revisions:
        job=by_id.get(remote['operation_id'])
        require(job is not None and remote['device_id']==device_id
            and remote['path']==job['path']=='runtime-note.md'
            and remote['file_id']==job['file_id'] and remote['hash']==job['hash']
            and remote['size']==job['size'] and remote['sequence']==job['remote_revision'],
            'Native original upload does not match its actual provider revision')
    latest=revisions[-1]
    require(latest['hash']==expected_hash and latest['size']==len(DRAFT.encode())
        and latest['sequence']==provider['vault']['sequence'],'The saved note was not the actual latest provider revision')
    heads=native['heads']
    require(len(heads)==1 and heads[0]['file_id']==latest['file_id']
        and heads[0]['revision']==latest['sequence'] and heads[0]['hash']==expected_hash,
        'Native confirmed head differs from the original provider revision')
    return latest


def read_original_object(endpoint, vault, content_hash, token):
    require(re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com',endpoint)
        and re.fullmatch(r'[0-9a-f]{32}',vault) and re.fullmatch(r'[0-9a-f]{64}',content_hash)
        and re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token),'Owned original object scope invalid')
    connection=http.client.HTTPSConnection(urlsplit(endpoint).hostname,timeout=25,context=ssl.create_default_context())
    try:
        connection.request('GET','/sync/v1/vaults/'+vault+'/objects/'+content_hash,
            headers={'Authorization':'Bearer '+token,'Accept':'application/octet-stream'})
        response=connection.getresponse();body=response.read(2*1024*1024+1)
        require(response.status==200 and len(body)<=2*1024*1024,'Actual owned object unavailable; no write retried')
        require(hashlib.sha256(body).hexdigest()==content_hash,'Actual original object digest differs')
        return body
    finally:connection.close()


def validate_incoming(receipt,provider,native,device):
    require(receipt['vault_id']==provider['vault']['id']==native['binding']['remote_vault']
        and receipt['path']=='peer-note.md' and receipt['operation']=='put'
        and receipt['hash']==hashlib.sha256(PEER_NOTE).hexdigest() and receipt['size']==len(PEER_NOTE),
        'Original independent-peer content differs')
    revisions=[row for row in provider['rows']['revisions'] if row['operation_id']==receipt['operation_id']]
    require(len(revisions)==1 and all(revisions[0][key]==receipt[key] for key in
        ('sequence','device_id','file_id','hash','size','path')),'Actual peer receipt does not match provider revision')
    heads=[row for row in native['heads'] if row['file_id']==receipt['file_id']]
    require(len(heads)==1 and heads[0]['hash']==receipt['hash'] and heads[0]['revision']==receipt['sequence']
        and heads[0]['path']==receipt['path'] and not native['conflicts']
        and native['binding']['cursor']==provider['vault']['sequence'],'Native incoming head/cursor is not confirmed')
    progress={row['device_id']:row for row in provider['rows']['device_progress']}
    require(device in progress and progress[device]['offered_sequence']==provider['vault']['sequence']
        and receipt['device_id']!=device and receipt['device_id'] in progress
        and progress[receipt['device_id']]['committed_sequence']==receipt['sequence'],
        'Actual generated changes/peer commit observations differ; no API apply ACK is inferred')


class SyncProbe(AuthProbe):
    def observe(self,label):
        value=observe_provider(self.owner)
        atomic_json(self.output/(label+'-provider.json'),value,exclusive=True)
        return value

    def sync_phase(self,phase):
        require(phase in ('associate','cycle','cycle-incoming','status','association-original','close'),'Unexpected Sync UI phase')
        number=len(self.phases)+1
        # The actual association ID/digest is separately frozen in the driver
        # before its confirmation tap. This dispatch ID is not a server nonce.
        atomic_json(self.output/('sync-dispatch-'+str(number)+'.json'),{'phase':phase,
            'driver_dispatch_id':str(uuid.uuid4()),'server_or_native_operation_id':False,'automatic_retry':False},exclusive=True)
        self.start_app();started=time.monotonic()
        result=self.shell('am','instrument','-w','-e','phase',phase,DRIVER+'/.SyncRuntimeProbe',timeout=180,check=False)
        require(len(result.stdout)<=1024*1024,'Owned static Sync result exceeds bound')
        text=result.stdout.decode('utf-8','replace')
        atomic_json(self.output/('sync-phase-'+str(number)+'.json'),{'phase':phase,'instrument_output':text},exclusive=True)
        require(result.returncode==0 and 'OPENNEXUS_SYNC_PHASE_OK '+phase in text and 'OPENNEXUS_SYNC_PHASE_FAILED' not in text,
            'Actual owned Sync UI failed; original result must be checked before any further mutation')
        self.phases.append({'phase':phase,'duration_ms':round((time.monotonic()-started)*1000),'passed':True})

    def stage_sync_input(self,vault):
        value={'vault_id':vault['vault_id'],'vault_name':vault['name']}
        require(re.fullmatch(r'[0-9a-f]{32}',value['vault_id'])
            and re.fullmatch(r'Owned Android Sync [0-9a-f]{32}',value['vault_name']),'Owned vault input changed')
        self.device('shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote(
            'umask 077; test ! -e no_backup/owned-sync.json && cat > no_backup/owned-sync.json'),
            input=json.dumps(value).encode())

    def original_association(self):
        result=self.shell('run-as',DRIVER,'cat','no_backup/owned-sync-association-intent.json',check=False)
        require(result.returncode==0 and 0<len(result.stdout)<=2048,'Original UI association intent is unavailable')
        value=json.loads(result.stdout)
        require(set(value)=={'operation_id','preview_fingerprint','remote_vault'}
            and str(uuid.UUID(value['operation_id']))==value['operation_id']
            and re.fullmatch(r'[0-9a-f]{64}',value['preview_fingerprint'])
            and re.fullmatch(r'[0-9a-f]{32}',value['remote_vault']),'Original UI association intent changed')
        atomic_json(self.output/'original-association-intent.json',value,exclusive=True)
        return value

    def host_snapshot(self,label):
        self.host_observation_stage='origin'
        paths=self.shell('run-as',APP,'find','.','-name','host.sqlite3','-type','f').stdout.decode().splitlines()
        require(len(paths)==1 and re.fullmatch(r'\./(?:[A-Za-z0-9_.-]+/)*host\.sqlite3',paths[0]),'Native host metadata origin ambiguous')
        self.host_observation_stage='copy'
        folder=Path(tempfile.mkdtemp(prefix='sync-metadata-',dir=self.tools));database=folder/'host.sqlite3'
        for suffix in ('','-wal','-shm'):
            response=self.private(paths[0]+suffix,check=False)
            if response.returncode==0:Path(str(database)+suffix).write_bytes(response.stdout)
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            self.host_observation_stage='integrity'
            require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'Original native metadata integrity failed')
            self.host_observation_stage='binding'
            bindings=db.execute('SELECT id,remote_vault,state,cursor,endpoint,account FROM sync_bindings').fetchall()
            require(len(bindings)==1 and bindings[0][2]=='active','Original native binding is not unique/active')
            binding=bindings[0][0]
            self.host_observation_stage='transfers'
            projections={'jobs':('sync_jobs','operation_id,file_id,path,hash,size,state,remote_revision,error'),
                'attempts':('sync_attempts','operation_id,attempts,outcome,error'),
                'heads':('sync_heads','file_id,revision,path,hash'),
                'inbox':('sync_inbox','sequence,operation_id,rename_id,state'),
                'conflicts':('sync_conflicts','sequence,file_id,local_path,local_hash,state')}
            rows={}
            for name,(table,columns) in projections.items():
                count=db.execute('SELECT COUNT(*) FROM '+table+' WHERE binding=?',(binding,)).fetchone()[0]
                require(0<=count<=512,'Native metadata observation limit exceeded')
                rows[name]=[dict(zip(columns.split(','),row)) for row in db.execute(
                    'SELECT '+columns+' FROM '+table+' WHERE binding=? ORDER BY rowid LIMIT 512',(binding,))]
                require(len(rows[name])==count,'Native metadata observation changed')
            has_issuers=db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_sync_attempt_devices')").fetchone()[0]
            rows['issuers']=[]
            if has_issuers:
                count=db.execute('SELECT COUNT(*) FROM mobile_sync_attempt_devices WHERE binding=?',(binding,)).fetchone()[0]
                require(0<=count<=512,'Native issuing-device observation limit exceeded')
                rows['issuers']=[dict(zip(('operation_id','device_id'),row)) for row in db.execute(
                    'SELECT operation_id,device_id FROM mobile_sync_attempt_devices WHERE binding=? ORDER BY rowid LIMIT 512',(binding,))]
                require(len(rows['issuers'])==count,'Native issuing-device observation changed')
            self.host_observation_stage='association'
            intents=db.execute('SELECT operation_id,request_hash,request,state,attempt_revision,binding FROM mobile_sync_binding_intents').fetchall()
            require(len(intents)==1 and intents[0][3]=='bound','Original binding receipt is not uniquely bound')
            original=intents[0];request=json.loads(original[2])
            # Ticket::record persists the immutable original Binding JSON. Its
            # cursor may precede live progress; it is not just the binding ID.
            receipt=json.loads(original[5])
            require(isinstance(receipt,dict) and set(receipt)=={'id','endpoint','remote_vault','account','cursor'}
                and receipt['id']==binding and receipt['remote_vault']==bindings[0][1]==request['remote_vault']
                and receipt['endpoint']==bindings[0][4]==request['selection']['endpoint']
                and receipt['account']==bindings[0][5]==request['selection']['account']
                and type(receipt['cursor']) is int and 0<=receipt['cursor']<=bindings[0][3]<=9007199254740991,
                'Original serialized binding receipt differs from native scope')
            rows['association']={'operation_id':original[0],'request_hash':original[1],
                'original_request_bytes_sha256':hashlib.sha256(original[2].encode()).hexdigest(),
                'original_binding_bytes_sha256':hashlib.sha256(original[5].encode()).hexdigest(),
                'preview_fingerprint':request['preview_fingerprint'],'remote_vault':request['remote_vault'],
                'auth_operation_id':request['selection']['operation_id'],'state':original[3],'attempt_revision':original[4]}
        value={'binding':dict(zip(('id','remote_vault','state','cursor'),bindings[0])),**rows,
            'integrity':'ok','credentials_selected':False,'original_database_uploaded':False}
        atomic_json(self.output/(label+'-host.json'),value,exclusive=True)
        self.host_observation_stage='complete'
        return value

    def run(self):
        self.boot()
        require(self.binding.get('native_cycle_gateway',{}).get('present') is True,'Fixed APK has no foreground cycle gateway')
        self.enable_test_ime();self.phase('ime-seed');self.restore_test_ime()
        require(self.original('runtime-note.md')==DRAFT.encode(),'Actual fresh Sync seed differs from original UI bytes')
        setup=Setup(self.owner);prepared=setup.run()
        atomic_json(self.output/'independent-setup.json',prepared,exclusive=True)
        require(not prepared['provider']['rows']['revisions'],'Fresh owned remote vault already contains revisions')
        self.stage_owned_input(read_json(self.owner/'device-private.json'));self.stage_sync_input(prepared['vault'])
        self.auth_phase('login-a',cold=True)
        auth=self.snapshot('sync-login');selected=auth['selection']
        require(selected is not None and len(auth['intents'])==1,'Original Android login is ambiguous')
        login=auth['intents'][0];device=login['device_id'];active_intent(auth,'login',device)
        require(device!=prepared['device_id'],'Setup actor was substituted for the actual Android device')
        self.sync_phase('associate');original=self.original_association();bound=self.host_snapshot('associated')
        require(original['remote_vault']==bound['binding']['remote_vault']==prepared['vault']['vault_id']
            and all(bound['association'][key]==original[key] for key in original)
            and bound['association']['auth_operation_id']==selected['operation_id'],'Actual original association differs from the frozen UI or identity')
        require(not self.observe('associated')['rows']['revisions'],'Association unexpectedly transferred note revisions')
        self.sync_phase('cycle');native=self.host_snapshot('first-cycle');provider=self.observe('first-cycle')
        latest=validate_upload(provider,native,device)
        body=read_original_object(setup.endpoint,prepared['vault']['vault_id'],latest['hash'],setup.session()['access_token'])
        require(body==DRAFT.encode()==self.original('runtime-note.md'),'Actual remote object and saved Android source differ')
        (self.output/'original-confirmed-note.md').write_bytes(body)
        self.sync_phase('association-original');self.sync_phase('status')
        require(self.host_snapshot('after-readonly')==native,'Read-only UI changed original native Sync records')
        atomic_json(self.output/'sync-initial-proof.json',{'passed':True,'scope':'First actual fixed-APK foreground upload only',
            'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,'compiled_from':self.args.build_origin,
            'APK_sha256':self.binding['sha256'],'phases':self.phases,'original_operation_ids':[r['operation_id'] for r in provider['rows']['revisions']],
            'original_note_sha256':latest['hash'],'independent_actual_HTTP_object_bytes_equal':True,
            'provider':'Owned fixed Sync0.8/PostgreSQL/S3','actual_Android_UI_auth_binding_cycle':True,
            'desktop_product_pair_verified':False,'bidirectional_conflict_recovery_verified':False,
            'client_apply_ACK_available':False,'physical_phone_verified':False,'published':False},exclusive=True)
        if getattr(self.args,'exchange',False):self.exchange(setup,prepared,device)

    def exchange(self,setup,prepared,device):
        prior=self.original('runtime-note.md')
        peer=Peer(self.owner)
        receipt=peer.publish('incoming-markdown','peer-note.md',PEER_NOTE)
        atomic_json(self.output/'independent-peer-revision.json',receipt,exclusive=True)
        self.sync_phase('cycle-incoming')
        native=self.host_snapshot('incoming');provider=self.observe('incoming')
        validate_incoming(receipt,provider,native,device)
        require(self.original('peer-note.md')==PEER_NOTE and self.original('runtime-note.md')==prior,
            'Incoming Markdown bytes or original saved Android note changed unexpectedly')
        actual=read_original_object(setup.endpoint,prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token'])
        require(actual==PEER_NOTE,'Actual independent peer object bytes changed')
        self.sync_phase('close');self.phase('peer-reopen')
        require(self.original('peer-note.md')==PEER_NOTE and self.host_snapshot('incoming-reopen')==native,
            'Cold reopen changed the original incoming file or native Sync records')
        (self.output/'incoming-original.md').write_bytes(PEER_NOTE)
        atomic_json(self.output/'sync-exchange-proof.json',{'passed':True,
            'scope':'Real actual Android saved-note upload plus independent HTTP peer original-note download/cold rendered reopen',
            'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,'APK_sha256':self.binding['sha256'],
            'phases':self.phases,'incoming_original_operation_id':receipt['operation_id'],'incoming_original_file_id':receipt['file_id'],
            'incoming_original_sha256':receipt['hash'],'client_apply_observed_from_owned_files_and_UI':True,
            'server_offered_sequence_is_not_API_apply_ACK':True,'API_client_apply_ACK_available':False,
            'desktop_product_pair_verified':False,'Canvas_PNG_rename_delete_conflicts_offline_Stop_verified':False,
            'physical_phone_verified':False,'upgrade_signing_release_verified':False},exclusive=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('bundle','sdk','output','source-checkout'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('commit','tools-commit','build-run-id'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--serial',default='emulator-5562');parser.add_argument('--exchange',action='store_true')
    args=parser.parse_args();args.scope='sync-exchange' if args.exchange else 'sync-initial'
    require_hosted();require_hosted_emulator(args.serial);owner=owned_root()
    state=read_json(owner/'owner-state.json');require(state['result']=='ready' and state.get('fixture')=='sync','Owned Sync origin is not ready')
    apk,binding=validate_binding(args.bundle,args.commit)
    from release_plan import build_source
    require(re.fullmatch(r'[0-9a-f]{40}',args.commit) and re.fullmatch(r'[0-9a-f]{40}',args.tools_commit),'Immutable sources required')
    build_source(Path(__file__).resolve().parents[1],args.tools_commit)
    require(json.loads((args.bundle/'source-provenance.json').read_text('utf-8'))==build_source(args.source_checkout.resolve(),args.commit),'Compiled source differs from the actual APK')
    args.build_origin=build_job_origin(args.build_run_id,args.commit);probe=SyncProbe(args,apk,binding,owner)
    try:probe.run()
    except Exception:
        probe.failure_observations()
        try:probe.host_snapshot('failure')
        except Exception as error:atomic_json(probe.output/'failure-host-unavailable.json',
            {'error':type(error).__name__,'stage':getattr(probe,'host_observation_stage','not_started'),
             'no_mutation_retried':True},exclusive=True)
        raise
    finally:
        probe.restore_test_ime();probe.shell('am','force-stop',APP,check=False)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_ANDROID_SYNC_FAILED_'+type(error).__name__,file=__import__('sys').stderr)
        raise SystemExit(1)
