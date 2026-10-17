"""Same original hosted Android client, real PG/S3 and packaged Desktop pair.

The workflow must keep this job's owned providers and AVD alive between stages.
No HTTP actor publishes desktop/Android revisions in these stages. Actual
private metadata, keys and fixture credentials are never uploaded.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from android_auth_fixture import atomic_json,fixed_service,owned_root,read_json,require_hosted
from android_desktop_exchange import exchange_bytes
from android_desktop_exchange_api import OriginalRunAPI,wait_original_exchange
from android_desktop_handoff import canonical,context_bytes,decode,require,seal
from android_desktop_product import DESKTOP_SOURCE
from android_desktop_recipient import write_new
from android_product_pair_android import auth_identity,capture_anchor,hosted_scope,native_identity,original_pair_ui_phase,read_frozen_fixture,resume_probe
from android_product_pair_data import ANDROID_NOTE,ANDROID_CANVAS_TEXT,ANDROID_POSITION,CANVAS_SEED,DESKTOP_NOTE,DESKTOP_CANVAS_TEXT,DESKTOP_POSITION,DRAFT,IMAGE_BYTES,IMAGE_PATH
from android_product_pair_evidence import match_provider_files,sha,validate_canvas_edit
from android_product_pair_stages import ACTIONS,ANDROID_APK_SHA256,PATHS,validate_final,validate_return,validate_stage
from android_runtime_probe import APP,build_job_origin,require_hosted_emulator,validate_binding
from android_sync_fixture import observe_provider
from android_sync_runtime import SyncProbe


ANDROID_SOURCE='18b6abbbf8bc8dae249469f7bcd9e5139cb39fee'
ANDROID_BUILD='39054475515'


def context_and_scope(args):
    require_hosted();require_hosted_emulator(args.serial)
    raw=os.environ.get('GITHUB_RUN_ID','')
    require(re.fullmatch(r'[1-9][0-9]{0,15}',raw) is not None
            and args.commit==ANDROID_SOURCE and args.build_run_id==ANDROID_BUILD
            and args.tools_commit==os.environ.get('GITHUB_SHA'), 'PAIR_LINUX_FROZEN_SOURCE_INVALID')
    context={'run_id':int(raw),'tools_commit':args.tools_commit,'android_commit':args.commit,'desktop_commit':DESKTOP_SOURCE}
    context_bytes(context);owner=owned_root();hosted_scope(context,args.serial,owner)
    workspace=Path(os.environ['GITHUB_WORKSPACE']).resolve()
    expected=workspace/'.build/android-product-pair'/args.stage/'evidence'
    require(args.output.resolve()==expected and owner.is_dir(), 'PAIR_LINUX_EVIDENCE_SCOPE_INVALID')
    return context,owner


def original_local_vault(probe,anchor):
    paths=probe.shell('run-as',APP,'find','.','-name','host.sqlite3','-type','f').stdout.decode().splitlines()
    require(len(paths)==1 and re.fullmatch(r'\./(?:[A-Za-z0-9_.-]+/)*host\.sqlite3',paths[0]), 'PAIR_LOCAL_VAULT_ORIGIN_INVALID')
    folder=Path(tempfile.mkdtemp(prefix='pair-original-binding-',dir=probe.tools));database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        result=probe.private(paths[0]+suffix,check=False)
        if result.returncode==0:Path(str(database)+suffix).write_bytes(result.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'PAIR_LOCAL_VAULT_METADATA_INVALID')
        rows=db.execute('SELECT request FROM mobile_sync_binding_intents WHERE operation_id=?',
                        (anchor['native']['association']['operation_id'],)).fetchall()
        require(len(rows)==1 and sha(rows[0][0].encode())==anchor['native']['association']['original_request_bytes_sha256'],
                'PAIR_LOCAL_VAULT_ORIGINAL_CHANGED')
        request=json.loads(rows[0][0]);local=request.get('local_vault')
        from android_product_pair_android import canonical_uuid
        require(canonical_uuid(local) and request.get('remote_vault')==anchor['native']['remote_vault_id'],
                'PAIR_LOCAL_VAULT_ORIGINAL_CHANGED')
        return local


def provider_bodies(owner,files,vault):
    config=fixed_service(owner)
    from sync_server.storage import S3Objects
    store=S3Objects(config['environment']['SYNC_S3_ENDPOINT'],config['bucket'])
    result={}
    try:
        require(len(files)==3 and {r['path'] for r in files}==PATHS
                and re.fullmatch(r'[0-9a-f]{32}',vault), 'PAIR_S3_ORIGINAL_SCOPE_INVALID')
        for row in files:
            require(re.fullmatch(r'[0-9a-f]{64}',row['sha256']) and type(row['bytes']) is int
                    and 0<row['bytes']<=4*1024**2, 'PAIR_S3_ORIGINAL_SCOPE_INVALID')
            with store.open(vault+'/'+row['sha256']) as stream:body=stream.read(row['bytes']+1)
            require(len(body)==row['bytes'] and sha(body)==row['sha256'],'PAIR_S3_ORIGINAL_BYTES_MISMATCH')
            result[row['path']]=body
        return result
    finally:store.client.close()


def validate_android_incoming(native,files,provider,anchor):
    require(native['binding']['id']==anchor['native']['binding_id']
            and native['binding']['remote_vault']==anchor['native']['remote_vault_id']
            and native['binding']['cursor']==provider['vault']['sequence'] and not native['conflicts'],
            'PAIR_ANDROID_INCOMING_SCOPE_INVALID')
    for row in files:
        head=[r for r in native['heads'] if r['file_id']==row['file_id']]
        inbox=[r for r in native['inbox'] if r['sequence']==row['sequence']]
        require(len(head)==len(inbox)==1 and inbox[0]['state']=='applied'
                and head[0]['path']==row['path'] and head[0]['revision']==row['sequence'] and head[0]['hash']==row['sha256'],
                'PAIR_ANDROID_DESKTOP_ORIGINAL_NOT_APPLIED')
        remote=json.loads(inbox[0]['revision'])
        require(all(remote[k]==row[k] for k in ('operation_id','file_id','base_revision','path','sequence'))
                and remote['vault_id']==anchor['native']['remote_vault_id'] and remote['operation']=='put'
                and remote['hash']==row['sha256'] and remote['size']==row['bytes'],
                'PAIR_ANDROID_DESKTOP_ORIGINAL_INBOX_CHANGED')


def first(context,owner,args,apk,binding):
    probe=SyncProbe(args,apk,binding,owner)
    try:
        probe.run()  # Exactly one real first setup/install/login/association/upload.
        anchor=capture_anchor(probe,context)
        local=original_local_vault(probe,anchor)
        atomic_json(owner/'pair-local-vault.private.json',{'local_vault_id':local},exclusive=True)
        atomic_json(owner/'pair-first-native.json',read_json(probe.output/'first-cycle-host.json'),exclusive=True)
    except Exception:
        probe.failure_observations();raise
    finally:
        probe.restore_test_ime();probe.force_stop()
    api=OriginalRunAPI(context,os.environ)
    recipient,origin=wait_original_exchange(context,'recipient',api,owner/'pair-recipient-cache')
    payload=read_frozen_fixture(owner,context,args.serial)
    envelope=seal(decode(recipient['public_key'],32),payload,context)
    output=owner/'pair-sealed';output.mkdir(exist_ok=False)
    write_new(output/'exchange.json',exchange_bytes(context,'sealed',envelope))
    atomic_json(probe.output/'actual-first-stage.json',{'context':context,'original_Android_client_closed':True,
                'recipient_origin':origin,'APK_sha256':binding['sha256'],'Desktop_product_pair_complete':False},exclusive=True)


def returned_files(provider,native,bodies,desktop,device):
    rows=[]
    for original in desktop['files']:
        if original['path']==IMAGE_PATH:rows.append(original);continue
        current=[r for r in provider['rows']['revisions'] if r['path']==original['path']
                 and r['device_id']==device and r['base_revision']==original['sequence']]
        require(len(current)==1,'PAIR_ANDROID_RETURN_ORIGINAL_AMBIGUOUS')
        remote=current[0];body=bodies[original['path']]
        jobs=[r for r in native['jobs'] if r['operation_id']==remote['operation_id']]
        issuers=[r for r in native['issuers'] if r['operation_id']==remote['operation_id']]
        attempts=[r for r in native['attempts'] if r['operation_id']==remote['operation_id']]
        require(len(jobs)==len(issuers)==len(attempts)==1 and jobs[0]['state']=='acked' and remote['operation']=='put'
                and jobs[0]['error'] is None and jobs[0]['file_id']==remote['file_id']==original['file_id']
                and jobs[0]['path']==remote['path']
                and jobs[0]['remote_revision']==remote['sequence'] and jobs[0]['hash']==remote['hash']==sha(body)
                and jobs[0]['size']==remote['size']==len(body) and issuers[0]['device_id']==device
                and attempts[0]['outcome']=='succeeded' and attempts[0]['error'] is None
                and type(attempts[0]['attempts']) is int and 1<=attempts[0]['attempts']<=4,
                'PAIR_ANDROID_RETURN_NATIVE_ACK_MISSING')
        rows.append({**{k:remote[k] for k in ('path','file_id','operation_id','device_id','sequence','base_revision')},
                     'sha256':sha(body),'bytes':len(body)})
    return rows


def returned(context,owner,args,apk,binding):
    api=OriginalRunAPI(context,os.environ)
    desktop,origin=wait_original_exchange(context,'desktop-first',api,owner/'pair-desktop-first-cache')
    validate_stage(context,'desktop-first',desktop)
    provider=observe_provider(owner);match_provider_files(desktop['files'],provider,desktop['identity']['remote_vault_id'])
    original=provider_bodies(owner,desktop['files'],desktop['identity']['remote_vault_id'])
    require(original['runtime-note.md']==DESKTOP_NOTE and original[IMAGE_PATH]==IMAGE_BYTES,'PAIR_DESKTOP_SOURCE_OR_IMAGE_CHANGED')
    validate_canvas_edit(CANVAS_SEED,original['peer.canvas'],DESKTOP_CANVAS_TEXT,DESKTOP_POSITION)
    probe,anchor=resume_probe(context,args,apk,binding,owner)
    try:
        probe.sync_phase('cycle-incoming')
        incoming=probe.host_snapshot('pair-desktop-incoming');current=probe.observe('pair-desktop-incoming')
        validate_android_incoming(incoming,desktop['files'],current,anchor)
        bodies={name:probe.original(name) for name in PATHS}
        require(bodies==original,'PAIR_ANDROID_DESKTOP_SAVED_BYTES_CHANGED')
        first_native=read_json(owner/'pair-first-native.json')
        require([r for r in incoming['jobs'] if r['path'] in PATHS]==[r for r in first_native['jobs'] if r['path'] in PATHS],
                'PAIR_ANDROID_REUPLOADED_DESKTOP_INPUT')
        original_pair_ui_phase(probe,context,'pair-note-reopen')
        original_pair_ui_phase(probe,context,'pair-canvas-reopen',keep_running=True)
        rendered=probe.read_native_styles('pair-canvas-reopen')
        images=rendered.get('styles',{}).get('imageSave',{}).get('canvasImages',[])
        require(rendered.get('available') is True and images==[{'complete':True,'width':2,'height':2,'visible':True}],
                'PAIR_ANDROID_DESKTOP_ORIGINAL_IMAGE_NOT_DECODED')
        probe.force_stop()
        original_pair_ui_phase(probe,context,'pair-note-return')
        original_pair_ui_phase(probe,context,'pair-canvas-return')
        after={name:probe.original(name) for name in PATHS}
        require(after['runtime-note.md']==ANDROID_NOTE and after[IMAGE_PATH]==IMAGE_BYTES,'PAIR_ANDROID_RETURN_BYTES_CHANGED')
        canvas=validate_canvas_edit(original['peer.canvas'],after['peer.canvas'],ANDROID_CANVAS_TEXT,ANDROID_POSITION)
        probe.sync_phase('cycle-media')
        native=probe.host_snapshot('pair-returned');provider=probe.observe('pair-returned')
        auth=auth_identity(probe.snapshot('pair-returned-auth'),anchor['auth']);native_identity(native,auth,anchor['native'])
        files=returned_files(provider,native,after,desktop,auth['device_id'])
        match_provider_files(files,provider,anchor['native']['remote_vault_id'])
        require(provider_bodies(owner,files,anchor['native']['remote_vault_id'])==after,'PAIR_ANDROID_RETURN_S3_BYTES_CHANGED')
        require({name:probe.original(name) for name in PATHS}==after,'PAIR_ANDROID_POST_CYCLE_BYTES_CHANGED')
        local=read_json(owner/'pair-local-vault.private.json')['local_vault_id']
        identity={'local_vault_id':local,'binding_id':anchor['native']['binding_id'],
                  'remote_vault_id':anchor['native']['remote_vault_id'],'current_device_id':auth['device_id']}
        atomic_json(probe.output/'actual-return-actions.json',{'Desktop_original_origin':origin,'Canvas_preservation':canvas,
                    'actual_source_and_Canvas_UI_and_Save':True,'actual_2x2_original_image_decoded':True},exclusive=True)
    except Exception:
        probe.failure_observations();raise
    finally:probe.restore_test_ime();probe.force_stop()
    body={'passed':True,'client_source':context['android_commit'],'client_artifact_sha256':binding['sha256'],
          'native_metadata_sha256':sha(canonical(native)),'identity':identity,'files':files,'actions':ACTIONS['android-return'],
          'actual_client_closed':True,'owned_state_preserved':True}
    validate_return(context,desktop,body)
    output=owner/'pair-android-return';output.mkdir(exist_ok=False)
    write_new(output/'exchange.json',exchange_bytes(context,'android-return',body))
    atomic_json(owner/'pair-desktop-stage.json',desktop,exclusive=True);atomic_json(owner/'pair-android-stage.json',body,exclusive=True)


def final(context,owner,args):
    api=OriginalRunAPI(context,os.environ)
    final,origin=wait_original_exchange(context,'desktop-final',api,owner/'pair-desktop-final-cache')
    desktop=read_json(owner/'pair-desktop-stage.json');android=read_json(owner/'pair-android-stage.json')
    validate_final(context,desktop,android,final)
    provider=observe_provider(owner);match_provider_files(final['files'],provider,final['identity']['remote_vault_id'])
    bodies=provider_bodies(owner,final['files'],final['identity']['remote_vault_id'])
    require(bodies['runtime-note.md']==ANDROID_NOTE and bodies[IMAGE_PATH]==IMAGE_BYTES,'PAIR_FINAL_PROVIDER_BYTES_CHANGED')
    args.output.mkdir(parents=True,exist_ok=False)
    atomic_json(args.output/'actual-product-pair-proof.json',{'passed':True,'context':context,'Desktop_final_original_origin':origin,
        'actual_Android_Desktop_native_UI_original_bidirectional_pair':True,'files':final['files'],
        'actual_original_MD_Canvas_and_2x2_image':True,'provider':'Owned fixed Sync0.8/PostgreSQL/S3',
        'physical_phone_OEM_upgrade_signing_release_or_all_A2_A6_complete':False,'credentials_or_raw_databases_uploaded':False},exclusive=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=('first','returned','final'))
    for name in ('bundle','sdk','output','source-checkout'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('commit','tools-commit','build-run-id'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--serial',default='emulator-5562');args=parser.parse_args()
    context,owner=context_and_scope(args)
    if args.stage=='final':final(context,owner,args);return
    from release_plan import build_source
    build_source(Path(__file__).resolve().parents[1],args.tools_commit)
    require(read_json(args.bundle/'source-provenance.json')==build_source(args.source_checkout.resolve(),args.commit),
            'PAIR_LINUX_COMPILED_SOURCE_CHANGED')
    apk,binding=validate_binding(args.bundle,args.commit)
    require(binding['sha256']==ANDROID_APK_SHA256,'PAIR_LINUX_ORIGINAL_APK_REPLACED')
    args.build_origin=build_job_origin(args.build_run_id,args.commit)
    args.scope='product-pair';args.conflicts=False;args.media=False;args.stop=False;args.recovery=False;args.exchange=False
    args.semantic_preview=False;args.webview_preview=True;args.webview_cycle=True
    {'first':first,'returned':returned}[args.stage](context,owner,args,apk,binding)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_ANDROID_PRODUCT_PAIR_FAILED_'+type(error).__name__)
        raise SystemExit(1)
