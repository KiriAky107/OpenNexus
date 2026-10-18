"""Original fixed-APK foreground Sync scopes on exclusive hosted providers.

Each named scope records only its actual case; none alone proves a desktop
product pair, all recovery cases, a physical phone or an upgrade. Original intents
and failures are retained; no failed UI mutation is automatically repeated.
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
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from android_auth_fixture import atomic_json, owned_root, read_json, require_hosted
from android_auth_runtime import AuthProbe, active_intent
from android_runtime_probe import APP, DRIVER, DRAFT, IMAGE_BYTES, build_job_origin, require, require_hosted_emulator, validate_binding
from android_sync_fixture import arm_control,control_root,observe_provider
from android_sync_setup import Setup
from android_sync_peer import IMAGE_PATH,PATHS,Peer
from android_sync_recovery import RecoveryMixin
from android_sync_conflict import ConflictMixin
from android_sync_webview_input import instrument_original_preview, instrument_original_cycle

PEER_NOTE='# 来自独立 HTTP 客户端 😀\n\n保持 Markdown 原文。\n'.encode()
PEER_CANVAS=(json.dumps({'nodes':[
    {'id':'original-peer-text','type':'text','x':0,'y':0,'width':300,'height':180,'text':'从独立客户端同步的画布 😀'},
    {'id':'original-peer-image','type':'file','x':0,'y':220,'width':320,'height':240,'file':IMAGE_PATH}],
    'edges':[],'vendor':{'original':'保留原始属性与 CRLF'}},ensure_ascii=False,indent=2).replace('\n','\r\n')+'\r\n').encode()


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


def validate_peer_applied(receipts,provider,native,device):
    """Prove original peer operations reached native apply, not just changes pages."""
    require(0<len(receipts)<=4 and len({r['operation_id'] for r in receipts})==len(receipts)
        and len({r['file_id'] for r in receipts})==len(receipts),'Peer observation originals are ambiguous')
    require(provider['vault']['id']==native['binding']['remote_vault'] and not native['conflicts']
        and native['binding']['cursor']==provider['vault']['sequence'],'Native incoming boundary is not fully confirmed')
    for receipt in receipts:
        require(receipt['vault_id']==provider['vault']['id'] and receipt['device_id']!=device
            and receipt['path'] in PATHS and receipt['operation'] in ('put','delete'),'Peer original scope differs')
        remote=[r for r in provider['rows']['revisions'] if r['operation_id']==receipt['operation_id']]
        inbox=[r for r in native['inbox'] if r['sequence']==receipt['sequence']]
        head=[r for r in native['heads'] if r['file_id']==receipt['file_id']]
        files=[r for r in provider['rows']['files'] if r['file_id']==receipt['file_id']]
        require(len(remote)==len(inbox)==len(head)==len(files)==1 and inbox[0]['state']=='applied',
            'Peer original lacks a unique actual provider/native applied record')
        original=json.loads(inbox[0]['revision'])
        # RemoteRevision serializes the content/CAS operation fields. Issuer
        # and fingerprint are verified against the independent PG receipt;
        # the native type does not persist either field.
        keys=('operation_id','file_id','base_revision','path','operation','hash','size','sequence')
        require(set(original)==set(keys)|{'vault_id'}
            and all(remote[0][key]==original[key]==receipt[key] for key in keys)
            and original['vault_id']==receipt['vault_id']
            and all(remote[0][key]==receipt[key] for key in ('device_id','fingerprint')),
            'Native inbox original revision or actual provider issuer differs')
        deleted=receipt['operation']=='delete'
        require(head[0]['revision']==files[0]['sequence']==receipt['sequence']
            and head[0]['path']==receipt['path'] and files[0]['path_key']==receipt['path'].casefold()
            and type(files[0]['deleted']) is int and files[0]['deleted']==int(deleted)
            and head[0]['hash']==('' if deleted else receipt['hash'])
            and (not deleted or receipt['hash'] is None and receipt['size']==0),
            'Actual native head or provider tombstone differs from original peer operation')
    progress={r['device_id']:r for r in provider['rows']['device_progress']}
    require(device in progress and progress[device]['offered_sequence']==provider['vault']['sequence']
        and all(r['device_id'] in progress and progress[r['device_id']]['committed_sequence']>=r['sequence']
            for r in receipts),'Actual offered/committed observations differ; no API apply ACK is inferred')


def validate_stopped_original(before,native,provider_before,provider_held,device,held):
    require(before['binding']['cursor']<provider_before['vault']['sequence'],
        'An empty already-applied changes page cannot prove late-response isolation')
    require(native==before,'Stopped original changed durable native transfer/head/cursor records')
    require(provider_before['vault']==provider_held['vault']
        and all(provider_before['rows'][key]==provider_held['rows'][key]
            for key in ('revisions','files','objects','uploads')),'Stopped original wrote provider content')
    require({k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')}
        =={k:v for k,v in provider_held['delivery']['counts'].items() if not k.startswith('changes:')},
        'Stopped original issued another revision request')
    require(held['mode']=='hold_changes' and held['action']=='changes' and held['service_status']==200
        and held['service_response_complete'] is True and held['raw_body_recorded'] is False,
        'The original actual generated changes response was not held')
    events=[r for r in provider_held['delivery']['events'] if r['fault_operation']==held['operation_id']]
    require(len(events)==1 and events[0]=={'action':'changes','service_status':200,'delivery_status':None,
        'fault_operation':held['operation_id']},'The original held response was missing or repeated')
    previous={r['device_id']:r for r in provider_before['rows']['device_progress']}
    current={r['device_id']:r for r in provider_held['rows']['device_progress']}
    require(set(previous)==set(current) and device in current
        and all(previous[k]==current[k] for k in previous if k!=device)
        and all(current[device][k]==v for k,v in previous[device].items() if k not in ('offered_sequence','offered_at'))
        and current[device]['offered_sequence']==provider_held['vault']['sequence'],
        'Generated progress is not an apply ACK and cannot change confirmed commits')


def validate_stop_resumed_original(before,native,provider_before,provider_after,device,receipt):
    validate_peer_applied([receipt],provider_after,native,device)
    require(native['binding']['id']==before['binding']['id']
        and receipt['sequence']>before['binding']['cursor']
        and all(native[key]==before[key] for key in ('jobs','attempts','issuers','association')),
        'Explicit resume changed original acknowledged upload or identity')
    heads={row['file_id']:row for row in native['heads']}
    require(len(heads)==len(native['heads'])==len(before['heads'])+1
        and all(heads.get(row['file_id'])==row for row in before['heads'])
        and len(native['inbox'])==len(before['inbox'])+1
        and all(row in native['inbox'] for row in before['inbox']),
        'Explicit resume changed existing heads/inbox or applied extra revisions')
    require(provider_after['vault']==provider_before['vault']
        and all(provider_after['rows'][key]==provider_before['rows'][key]
            for key in ('revisions','files','objects','uploads'))
        and {k:v for k,v in provider_after['delivery']['counts'].items() if not k.startswith('changes:')}
            =={k:v for k,v in provider_before['delivery']['counts'].items() if not k.startswith('changes:')},
        'Explicit resume created another provider write')


class SyncProbe(ConflictMixin,RecoveryMixin,AuthProbe):
    def association_observer(self,mode,observer_id):
        require(mode in ('install','read') and str(uuid.UUID(observer_id))==observer_id,
            'Unrelated association observer scope')
        require_hosted_emulator(self.args.serial)
        self.wait_owned_connection()
        port=None;report={'observer_id':observer_id,'observer_id_is_native_operation':False,
            'scope':'Bounded public UI events and pass-through association IPC phases; no input values or raw errors'}
        error=None
        try:
            process=self.shell('pidof',APP).stdout.decode().strip()
            require(re.fullmatch(r'[1-9][0-9]{0,9}',process),'Owned app process is ambiguous')
            port=self.device('forward','tcp:0','localabstract:webview_devtools_remote_'+process).stdout.decode().strip()
            require(re.fullmatch(r'[0-9]{4,5}',port) and 1024<=int(port)<=65535,'Unexpected owned forwarding port')
            connection=http.client.HTTPConnection('127.0.0.1',int(port),timeout=10)
            try:
                connection.request('GET','/json/list');response=connection.getresponse();body=response.read(1024*1024+1)
                require(response.status==200 and len(body)<=1024*1024,'Owned WebView metadata unavailable')
                pages=[item for item in json.loads(body) if item.get('type')=='page' and item.get('title')=='OpenNexus']
            finally:connection.close()
            require(len(pages)==1,'Owned WebView page is ambiguous')
            socket=urlsplit(pages[0]['webSocketDebuggerUrl'])
            require(socket.scheme=='ws' and socket.hostname in ('127.0.0.1','localhost') and socket.port==int(port)
                and re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+',socket.path) and not socket.query and not socket.fragment,
                'Refusing an unrelated association observer endpoint')
            script=Path(__file__).resolve().parent/'android-runtime/observe_sync_association.mjs'
            result=self.command(['node','--max-old-space-size=128',script,'ws://127.0.0.1:'+port+socket.path,mode,observer_id],timeout=20)
            require(0<len(result.stdout)<=65536,'Owned observer result exceeds bound')
            value=json.loads(result.stdout)
            require(value.get('observerId')==observer_id,'Another original observer result')
            if mode=='install':require(value.get('installed') is True,'Original association observer not installed')
            report.update(available=True,observation=value)
        except Exception as failure:
            error=failure;report.update(available=False,error_class=type(failure).__name__,original_exception_text_returned=False)
        finally:
            if port and re.fullmatch(r'[0-9]{4,5}',port):
                try:report['own_forward_removed']=self.device('forward','--remove','tcp:'+port,check=False).returncode==0
                except Exception as failure:report.update(own_forward_removed=False,forward_cleanup_error_class=type(failure).__name__)
            atomic_json(self.output/('association-observer-'+mode+'.json'),report,exclusive=True)
        if error is not None:raise RuntimeError('OWNED_ASSOCIATION_OBSERVER_UNAVAILABLE') from None
        return report

    def failure_observations(self):
        super().failure_observations()
        if getattr(getattr(self,'args',None),'revocation',False):
            from android_sync_rejection import failure_observations
            failure_observations(self)
        # Read-only debug WebView geometry/public error enums only; no DOM
        # input values, raw error text, screenshots or mutation replays.
        try:self.read_native_styles('sync-failure')
        except Exception as error:
            atomic_json(self.output/'sync-failure-renderer-unavailable.json',
                {'error':type(error).__name__,'no_mutation_retried':True},exclusive=True)

    def observe(self,label):
        value=observe_provider(self.owner)
        atomic_json(self.output/(label+'-provider.json'),value,exclusive=True)
        return value

    def sync_phase(self,phase):
        require(phase in ('associate','cycle','cycle-incoming','cycle-media','cycle-rename','cycle-delete',
            'cycle-after-stop','cycle-offline','cycle-dropped-upload','cycle-reconcile-original',
            'cycle-conflict','cycle-conflict-copy','conflict-copy','cold-original-decision',
            'cycle-revoked','refresh-revoked','status','association-original','close'),'Unexpected Sync UI phase')
        number=len(self.phases)+1
        # The actual association ID/digest is separately frozen in the driver
        # before its confirmation tap. This dispatch ID is not a server nonce.
        dispatch_id=str(uuid.uuid4())
        args=getattr(self,'args',None)
        webview=phase=='associate' and getattr(args,'webview_preview',False)
        semantic=phase=='associate' and getattr(args,'semantic_preview',False)
        browser_cycle=getattr(args,'webview_cycle',False) and (phase in ('cycle-conflict','cycle-conflict-copy','cycle-incoming','cycle-media')
            or phase=='cycle' and (getattr(args,'scope','')=='product-pair' or getattr(args,'original_native_outcome',False)))
        require(not (webview and semantic),'Choose exactly one original preview input backend')
        atomic_json(self.output/('sync-dispatch-'+str(number)+'.json'),{'phase':phase,
            'driver_dispatch_id':dispatch_id,'server_or_native_operation_id':False,'automatic_retry':False,
            'association_preview_input':('webview-touch' if webview else 'native-accessibility' if semantic else 'touch') if phase=='associate' else None,
            'cycle_input':'webview-touch' if browser_cycle else None},exclusive=True)
        self.start_app();started=time.monotonic()
        if phase=='associate':self.association_observer('install',dispatch_id)
        preview_args=['-e','association_preview','native-accessibility'] if semantic else []
        if webview:result=instrument_original_preview(self,dispatch_id)
        elif browser_cycle:result=instrument_original_cycle(self,dispatch_id,phase)
        else:result=self.shell('am','instrument','-w','-e','phase',phase,*preview_args,DRIVER+'/.SyncRuntimeProbe',timeout=180,check=False)
        require(len(result.stdout)<=1024*1024,'Owned static Sync result exceeds bound')
        text=result.stdout.decode('utf-8','replace')
        atomic_json(self.output/('sync-phase-'+str(number)+'.json'),{'phase':phase,'instrument_output':text},exclusive=True)
        observation=None
        if phase=='associate':
            try:observation=self.association_observer('read',dispatch_id)
            except Exception:pass  # Its bounded record is retained; never replace/retry the original UI result.
        if webview:
            events=observation.get('observation',{}).get('events',[]) if isinstance(observation,dict) else []
            clicks=[e for e in events if e.get('name')=='Preview merge' and e.get('kind')=='click']
            require(len(clicks)==1 and clicks[0].get('trusted') is True,'Original browser input did not produce one actual Preview UI click')
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

    def host_snapshot(self,label,*,include_outbox=False,include_payloads=False):
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
                'inbox':('sync_inbox','sequence,revision,operation_id,rename_id,state'),
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
            if include_outbox:
                self.host_observation_stage='owned-original-outbox'
                originals=db.execute("SELECT operation_id,file_id,revision,path,hash,operation,state,length(content) FROM outbox WHERE path='runtime-note.md' ORDER BY rowid LIMIT 17").fetchall()
                require(0<len(originals)<=16,'Owned original outbox is missing or exceeds bound')
                rows['outbox']=[dict(zip(('operation_id','file_id','revision','path','hash','operation','state','payload_size'),row)) for row in originals]
            if include_payloads:
                require(include_outbox,'Payload observation needs its original owned outbox')
                payloads=db.execute("SELECT p.operation_id,p.hash,p.size FROM payloads p JOIN outbox o ON o.operation_id=p.operation_id WHERE o.path='runtime-note.md' ORDER BY o.rowid LIMIT 17").fetchall()
                require(len(payloads)<=16,'Owned original payload metadata exceeds bound')
                rows['payloads']=[dict(zip(('operation_id','hash','size'),row)) for row in payloads]
            if getattr(getattr(self,'args',None),'conflicts',False):
                self.host_observation_stage='original-conflict-decisions'
                has_review=db.execute("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_review_receipts')").fetchone()[0]
                if has_review:
                    decisions=db.execute('SELECT r.sequence,r.choice,r.destination,r.expected,v.fingerprint,r.operation_id,r.rename_id,r.copy_id,r.retire_id,r.restore_id,r.state FROM sync_resolutions r LEFT JOIN sync_review_receipts v ON v.binding=r.binding AND v.sequence=r.sequence WHERE r.binding=? ORDER BY r.sequence LIMIT 17',(binding,)).fetchall()
                    require(len(decisions)<=16,'Original conflict decisions exceed bound')
                    keys=('sequence','choice','destination','expected_hash','fingerprint','operation_id','rename_id','copy_id','retire_id','restore_id','state')
                    rows['decisions']=[dict(zip(keys,row)) for row in decisions]
                else:
                    require(db.execute('SELECT COUNT(*) FROM sync_resolutions WHERE binding=?',(binding,)).fetchone()[0]==0,
                        'Legacy original decisions lack frozen review metadata')
                    rows['decisions']=[]
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
        if getattr(self.args,'revocation',False):
            from android_sync_rejection import run_revocation
            run_revocation(self,setup,prepared,device)
            return
        if getattr(self.args,'recovery',False):
            self.recover_original(setup,prepared,device)
            return
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
        if getattr(self.args,'media',False):self.media(setup,prepared,device)
        if getattr(self.args,'stop',False):self.stop_original(setup,prepared,device)
        if getattr(self.args,'conflicts',False):self.conflict_copy(setup,prepared,device)

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
        drafts=[r for r in self.draft_rows() if r[0]=='peer-note.md']
        require(len(drafts)==1 and drafts[0][1].encode()==PEER_NOTE,'Cold source editor did not load exact original native bytes')
        atomic_json(self.output/'incoming-reopen-original-draft.json',{'path':'peer-note.md',
            'content_sha256':hashlib.sha256(drafts[0][1].encode()).hexdigest(),'UTF8_bytes':len(drafts[0][1].encode()),
            'generation':drafts[0][2],'saved_generation':drafts[0][3]},exclusive=True)
        require(self.original('peer-note.md')==PEER_NOTE and self.host_snapshot('incoming-reopen')==native,
            'Cold reopen changed the original incoming file or native Sync records')
        (self.output/'incoming-original.md').write_bytes(PEER_NOTE)
        atomic_json(self.output/'sync-exchange-proof.json',{'passed':True,
            'scope':'Real actual Android saved-note upload plus independent HTTP peer original-note download/cold rendered reopen',
            'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,'APK_sha256':self.binding['sha256'],
            'phases':self.phases,'incoming_original_operation_id':receipt['operation_id'],'incoming_original_file_id':receipt['file_id'],
            'incoming_original_sha256':receipt['hash'],'client_apply_observed_from_owned_files_and_UI':True,
            'cold_source_native_draft_original_bytes_equal':True,'AX_text_is_not_used_as_original_file_bytes':True,
            'server_offered_sequence_is_not_API_apply_ACK':True,'API_client_apply_ACK_available':False,
            'desktop_product_pair_verified':False,'Canvas_PNG_rename_delete_conflicts_offline_Stop_verified':False,
            'physical_phone_verified':False,'upgrade_signing_release_verified':False},exclusive=True)

    def absent_original(self,name,label):
        require(name in ('peer-note.md','peer-renamed.md'),'Unrelated file absence observation refused')
        root=self.root+'vault/'
        result=self.shell('run-as',APP,'sh','-c','test -d '+shlex.quote(root)+' && test ! -e '+shlex.quote(root+name),check=False)
        require(result.returncode==0 and not result.stdout,'Original peer path is not confirmed absent')
        atomic_json(self.output/(label+'-absent.json'),{'path':name,'absent':True,'original_directory_verified':True,
            'read_only':True,'mutation_retried':False},exclusive=True)

    def sync_control(self,mode):
        require(mode in ('hold_changes','release'),'Unrelated original Sync control refused')
        current=read_json(control_root(self.owner)/'control.json');operation=str(uuid.uuid4())
        atomic_json(self.output/('sync-control-original-'+operation+'.json'),{'operation_id':operation,
            'mode':mode,'expected_revision':current['revision'],'native_or_server_operation_id':False},exclusive=True)
        receipt=arm_control(self.owner,operation,mode,current['revision'])
        atomic_json(self.output/('sync-control-'+mode+'-'+operation+'.json'),receipt,exclusive=True)
        return receipt

    def stop_original(self,setup,prepared,device):
        require_hosted_emulator(self.args.serial)
        receipt=Peer(self.owner).publish('stop-pending-note','peer-note.md',PEER_NOTE)
        atomic_json(self.output/'stop-pending-original-peer-receipt.json',receipt,exclusive=True)
        before=self.host_snapshot('stop-before');provider_before=self.observe('stop-before')
        require(receipt['sequence']==provider_before['vault']['sequence']>before['binding']['cursor']
            and not any(row['file_id']==receipt['file_id'] for row in before['heads']),
            'Stop must protect a genuinely unapplied original peer revision')
        self.absent_original('peer-note.md','stop-before-pending')
        auth_before=self.snapshot('stop-before');sealed=self.ciphertext_fingerprints();note=self.original('runtime-note.md')
        control=self.sync_control('hold_changes');held_id=control['operation_id']
        original={'driver_intent_id':str(uuid.uuid4()),'control_operation_id':held_id}
        atomic_json(self.output/'original-stop-driver-control-intent.json',dict(original,native_invocation_id_observed=False),exclusive=True)
        self.device('shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote(
            'umask 077; test ! -e no_backup/owned-sync-stop-input.json && cat > no_backup/owned-sync-stop-input.json'),input=json.dumps(original).encode())
        self.start_app();started=time.monotonic();number=len(self.phases)+1
        atomic_json(self.output/('sync-dispatch-'+str(number)+'.json'),{'phase':'cycle-stop',
            **original,'native_invocation_id_observed':False,'automatic_retry':False},exclusive=True)
        process=subprocess.Popen([str(self.adb),'-s',self.args.serial,'shell',shlex.join(
            ('am','instrument','-w','-e','phase','cycle-stop',DRIVER+'/.SyncRuntimeProbe'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        completed=False;released=False;release_attempted=False
        try:
            deadline=time.monotonic()+30;held_path=control_root(self.owner)/'fault-events'/(held_id+'.json')
            while True:
                require(process.poll() is None,'Original Stop driver ended before held-response admission')
                ready=self.shell('run-as',DRIVER,'cat','no_backup/owned-sync-cycle-stop-ready-intent.json',check=False)
                if ready.returncode==0 and ready.stdout and held_path.is_file():
                    require(len(ready.stdout)<=1024,'Original Stop checkpoint exceeds bound')
                    try:value=json.loads(ready.stdout)
                    except json.JSONDecodeError:value=None # Read only while the one producer finishes its original record.
                    if value is not None:
                        require(value==original,'Another original Stop driver is present');break
                require(time.monotonic()<deadline,'Original held changes/Stop-ready checkpoint unavailable');time.sleep(.2)
            held=read_json(held_path)
            require(held['operation_id']==held_id and held['revision']==control['result_revision'],
                'Original held control changed')
            atomic_json(self.output/'original-stop-held-response.json',held,exclusive=True)
            validate_stopped_original(before,self.host_snapshot('stop-held'),provider_before,self.observe('stop-held'),device,held)
            # Publish a complete file by the same proven same-directory mv-n
            # protocol; the Android reader must never see a partially written grant.
            self.device('shell','-T','run-as '+DRIVER+' sh -c '+shlex.quote(
                'umask 077; test ! -e no_backup/owned-sync-stop-authorize.json && test ! -e no_backup/owned-sync-stop-authorize.pending '
                '&& cat > no_backup/owned-sync-stop-authorize.pending && mv -n no_backup/owned-sync-stop-authorize.pending no_backup/owned-sync-stop-authorize.json '
                '&& test ! -e no_backup/owned-sync-stop-authorize.pending'),input=json.dumps(original).encode())
            output,error=process.communicate(timeout=35)
            require(len(output)<=1024*1024 and len(error)<=65536,'Original Stop result exceeds bound')
            text=output.decode('utf-8','replace')
            atomic_json(self.output/('sync-phase-'+str(number)+'.json'),{'phase':'cycle-stop','instrument_output':text},exclusive=True)
            require(process.returncode==0 and 'OPENNEXUS_SYNC_PHASE_OK cycle-stop' in text
                and 'OPENNEXUS_SYNC_PHASE_FAILED' not in text,'Original actual UI Stop was not confirmed; no new cycle started')
            self.phases.append({'phase':'cycle-stop','duration_ms':round((time.monotonic()-started)*1000),'passed':True})
            native=self.host_snapshot('stopped');provider=self.observe('stopped')
            validate_stopped_original(before,native,provider_before,provider,device,held)
            require(self.snapshot('stopped')==auth_before and self.ciphertext_fingerprints()==sealed
                and self.original('runtime-note.md')==note,'Stop changed original auth/credentials/saved bytes')
            self.absent_original('peer-note.md','stop-stopped-pending')
            release_attempted=True
            release=self.sync_control('release');released=True
            released_path=control_root(self.owner)/'fault-events'/(held_id+'.released.json');deadline=time.monotonic()+10
            while not released_path.is_file():
                require(time.monotonic()<deadline,'Original held response release was not observed');time.sleep(.1)
            delivered=read_json(released_path)
            require(delivered['operation_id']==held_id and delivered['original_response_send_attempted'] is True
                and delivered['client_receipt_known'] is False,'Original late response evidence differs')
            atomic_json(self.output/'original-stop-late-response-release.json',{'release_control':release,'original_response':delivered},exclusive=True)
            require(self.host_snapshot('stop-after-late-response')==before,'Late original response applied after Stop')
            self.absent_original('peer-note.md','stop-late-pending')
            self.sync_phase('cycle-after-stop');resumed=self.host_snapshot('stop-resumed');after=self.observe('stop-resumed')
            validate_stop_resumed_original(before,resumed,provider_before,after,device,receipt)
            require(self.original('runtime-note.md')==note and self.original('peer-note.md')==PEER_NOTE
                ==read_original_object(setup.endpoint,prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token']),
                'Explicit new cycle failed to resume original bytes without another revision')
            atomic_json(self.output/'sync-stop-proof.json',{'passed':True,'source_commit':self.args.commit,
                'runtime_tools_commit':self.args.tools_commit,'APK_sha256':self.binding['sha256'],'phases':self.phases,
                'original_driver_and_control':original,'native_invocation_id_observed':False,
                'original_pending_peer_revision':receipt,
                'actual_generated_response_held_before_one_UI_Stop':True,'native_durable_records_unchanged_while_stopped':True,
                'late_original_response_cannot_apply':True,'explicit_new_cycle_original_cursor_resume_without_new_revision':True,
                'API_generated_progress_is_not_client_apply_ACK':True,'desktop_phone_offline_conflict_release_verified':False},exclusive=True)
            completed=True
        finally:
            try:
                # An unknown release result must be reconciled from its original
                # control intent/receipt; never generate another release action.
                if not release_attempted:
                    release_attempted=True;self.sync_control('release')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:output,error=process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();output,error=process.communicate(timeout=5)
                    atomic_json(self.output/'original-stop-unfinished-driver.json',{'phase':'cycle-stop','completed':completed,
                        'original_driver_and_control':original,'instrument_output':output[:65536].decode('utf-8','replace'),
                        'no_mutation_retried':True},exclusive=True)

    def media(self,setup,prepared,device):
        before=self.original('runtime-note.md');peer=Peer(self.owner)
        originals=[peer.publish('media-png',IMAGE_PATH,IMAGE_BYTES),peer.publish('media-canvas','peer.canvas',PEER_CANVAS),
            peer.publish('media-note','peer-note.md',PEER_NOTE)]
        atomic_json(self.output/'peer-media-original-receipts.json',originals,exclusive=True)
        self.sync_phase('cycle-media');native=self.host_snapshot('media');provider=self.observe('media')
        validate_peer_applied(originals,provider,native,device)
        for receipt,content in zip(originals,(IMAGE_BYTES,PEER_CANVAS,PEER_NOTE)):
            require(self.original(receipt['path'])==content==read_original_object(setup.endpoint,
                prepared['vault']['vault_id'],receipt['hash'],setup.session()['access_token']),
                'Original peer Canvas/PNG/Markdown bytes differ between Android and actual S3-backed HTTP')
        self.sync_phase('close');self.phase('peer-media-reopen')
        require(self.original('peer.canvas')==PEER_CANVAS and self.original(IMAGE_PATH)==IMAGE_BYTES
            and self.host_snapshot('media-reopen')==native,'Cold Canvas reopen changed original content or Sync records')
        note=originals[-1]
        renamed=peer.publish('media-rename','peer-renamed.md',PEER_NOTE,file_id=note['file_id'],base_revision=note['sequence'])
        atomic_json(self.output/'peer-rename-original-receipt.json',renamed,exclusive=True)
        require(renamed['file_id']==note['file_id'] and renamed['base_revision']==note['sequence'],'Original rename identity changed')
        self.sync_phase('cycle-rename');native=self.host_snapshot('renamed');provider=self.observe('renamed')
        validate_peer_applied([renamed],provider,native,device)
        self.absent_original('peer-note.md','renamed-old-path')
        require(self.original('peer-renamed.md')==PEER_NOTE,'Original renamed Markdown bytes changed')
        deleted=peer.publish('media-delete','peer-renamed.md',b'',file_id=renamed['file_id'],
            base_revision=renamed['sequence'],operation='delete')
        atomic_json(self.output/'peer-delete-original-receipt.json',deleted,exclusive=True)
        require(deleted['file_id']==note['file_id'] and deleted['base_revision']==renamed['sequence'],'Original delete identity changed')
        self.sync_phase('cycle-delete');native=self.host_snapshot('deleted');provider=self.observe('deleted')
        validate_peer_applied([deleted],provider,native,device)
        self.absent_original('peer-renamed.md','deleted-original-path')
        require(self.original('runtime-note.md')==before and self.original('peer.canvas')==PEER_CANVAS
            and self.original(IMAGE_PATH)==IMAGE_BYTES,'Unrelated original saved bytes changed during rename/delete')
        atomic_json(self.output/'sync-media-proof.json',{'passed':True,'scope':'Actual original peer Canvas/PNG/Markdown incoming plus original-file rename and delete',
            'source_commit':self.args.commit,'runtime_tools_commit':self.args.tools_commit,'APK_sha256':self.binding['sha256'],
            'original_receipts':originals+[renamed,deleted],'phases':self.phases,'original_S3_Android_bytes_equal':True,
            'actual_Canvas_UI_text_and_image_reference_observed':True,'actual_image_pixels_verified':False,
            'native_applied_original_revision_head_cursor_verified':True,'unrelated_saved_bytes_preserved':True,
            'desktop_product_pair_verified':False,'phone_upgrade_signing_release_verified':False,
            'offline_Stop_conflict_dirty_draft_recovery_verified':False,'API_client_apply_ACK_available':False},exclusive=True)


def validate_original_outcome_args(args):
    if not getattr(args,'original_native_outcome',False):return
    require(args.conflicts and args.cold_originals and args.webview_preview and args.webview_cycle
            and not args.semantic_preview, 'Original native outcome requires its explicit fixed conflict scope')
    from android_product_pair_clients import FixedClientError, original_outcome_client
    try:original_outcome_client(args.commit,args.build_run_id)
    except FixedClientError:require(False, 'Original native outcome requires its explicit fixed conflict scope')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('bundle','sdk','output','source-checkout'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('commit','tools-commit','build-run-id'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--serial',default='emulator-5562')
    scope=parser.add_mutually_exclusive_group();scope.add_argument('--exchange',action='store_true');scope.add_argument('--media',action='store_true');scope.add_argument('--stop',action='store_true');scope.add_argument('--recovery',action='store_true');scope.add_argument('--conflicts',action='store_true');scope.add_argument('--revocation',action='store_true')
    parser.add_argument('--cold-originals',action='store_true')
    parser.add_argument('--semantic-preview',action='store_true')
    parser.add_argument('--webview-preview',action='store_true')
    parser.add_argument('--webview-cycle',action='store_true')
    parser.add_argument('--original-native-outcome',action='store_true')
    args=parser.parse_args();args.scope='sync-revocation' if args.revocation else 'sync-conflicts' if args.conflicts else 'sync-recovery' if args.recovery else 'sync-stop' if args.stop else 'sync-media' if args.media else 'sync-exchange' if args.exchange else 'sync-initial'
    require(not args.cold_originals or args.conflicts,'Cold original-entry scope requires actual conflicts')
    require(not args.semantic_preview or args.conflicts,'Native accessibility preview requires the explicit conflict scope')
    require(not args.webview_preview or args.conflicts or args.revocation,'Owned WebView preview requires an explicit conflict or revocation scope')
    require(not args.webview_cycle or args.conflicts,'Owned WebView cycle requires the explicit conflict scope')
    require(not (args.semantic_preview and args.webview_preview),'Choose exactly one original preview input backend')
    validate_original_outcome_args(args)
    require_hosted();require_hosted_emulator(args.serial);owner=owned_root()
    state=read_json(owner/'owner-state.json');require(state['result']=='ready' and state.get('fixture')=='sync','Owned Sync origin is not ready')
    apk,binding=validate_binding(args.bundle,args.commit)
    from release_plan import build_source
    require(re.fullmatch(r'[0-9a-f]{40}',args.commit) and re.fullmatch(r'[0-9a-f]{40}',args.tools_commit),'Immutable sources required')
    build_source(Path(__file__).resolve().parents[1],args.tools_commit)
    require(json.loads((args.bundle/'source-provenance.json').read_text('utf-8'))==build_source(args.source_checkout.resolve(),args.commit),'Compiled source differs from the actual APK')
    args.build_origin=build_job_origin(args.build_run_id,args.commit);probe=SyncProbe(args,apk,binding,owner)
    try:probe.run()
    except Exception as failure:
        if args.original_native_outcome:
            from android_original_outcome_status import OUTCOME_STAGES
            stage=getattr(probe,'original_outcome_stage',None)
            stage=stage if type(stage) is str and stage in OUTCOME_STAGES else 'not-entered'
            atomic_json(probe.output/'original-native-outcome-failure.json', {'stage':stage,
                'error_class':type(failure).__name__ if type(failure).__name__ in
                ('RuntimeError','HandoffError','TimeoutError','TimeoutExpired','OSError','ValueError') else 'OtherError',
                'raw_error_or_traceback_or_credentials_exported':False,'UI_or_stop_replayed':False},exclusive=True)
        probe.failure_observations()
        try:probe.host_snapshot('failure')
        except Exception as error:atomic_json(probe.output/'failure-host-unavailable.json',
            {'error':type(error).__name__,'stage':getattr(probe,'host_observation_stage','not_started'),
             'no_mutation_retried':True},exclusive=True)
        raise
    finally:
        probe.restore_test_ime()
        # Unknown original app/driver stop results must never be resent by the
        # generic cleanup. The workflow still closes its identity-checked AVD.
        if not args.original_native_outcome:probe.shell('am','force-stop',APP,check=False)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_ANDROID_SYNC_FAILED_'+type(error).__name__,file=__import__('sys').stderr)
        raise SystemExit(1)
