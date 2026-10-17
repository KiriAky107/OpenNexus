"""Strict public stage receipts; declarations alone are not product evidence.

Each actual driver must verify native/provider/file bytes before publishing.
Only original identities and file metadata cross jobs. Note content, auth
payloads and credentials are not accepted fields.
"""
import re
import uuid

from android_desktop_handoff import context_bytes,require
from android_desktop_product import INSTALLER_SHA256 as DESKTOP_INSTALLER
from android_product_pair_clients import fixed_android_client


IMAGE_PATH='attachments/14/14be779d5555c7061cacf2f4979b63fb54737c221c723540b6b4dd9e8d13bd29.png'
PATHS=frozenset(('runtime-note.md','peer.canvas',IMAGE_PATH))
STAGES={
    'desktop-first':('desktop-pair','Publish original desktop outbound'),
    'android-return':('android-desktop-pair','Publish original Android return'),
    'desktop-final':('desktop-pair','Publish original desktop cold receipt'),
}
ACTIONS={
    'desktop-first':{'native_login_and_merge':True,'source_edit_and_save':True,'Canvas_edit_and_save':True,'actual_client_cycle':True,'cold_original_reopen':False},
    'android-return':{'native_login_and_merge':False,'source_edit_and_save':True,'Canvas_edit_and_save':True,'actual_client_cycle':True,'cold_original_reopen':True},
    'desktop-final':{'native_login_and_merge':False,'source_edit_and_save':False,'Canvas_edit_and_save':False,'actual_client_cycle':True,'cold_original_reopen':True},
}


def digest(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def uid(value):
    try:return type(value) is str and str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def device(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{32}',value) is not None


def validate_stage(context, stage, body):
    context_bytes(context)
    require(stage in STAGES and type(body) is dict and set(body)=={
        'passed','client_source','client_artifact_sha256','native_metadata_sha256','identity','files','actions',
        'actual_client_closed','owned_state_preserved'}, 'PAIR_STAGE_BODY_INVALID')
    require(body['passed'] is True and body['actual_client_closed'] is True and body['owned_state_preserved'] is True
            and body['actions']==ACTIONS[stage] and all(type(v) is bool for v in body['actions'].values())
            and digest(body['client_artifact_sha256']) and digest(body['native_metadata_sha256']), 'PAIR_STAGE_BODY_INVALID')
    android=stage=='android-return'
    require(body['client_source']==context['android_commit' if android else 'desktop_commit']
            and body['client_artifact_sha256']==(fixed_android_client(context['android_commit']).apk_sha256 if android else DESKTOP_INSTALLER), 'PAIR_STAGE_CLIENT_INVALID')
    identity=body['identity']
    require(type(identity) is dict and set(identity)=={'local_vault_id','binding_id','remote_vault_id','current_device_id'}
            and uid(identity['local_vault_id']) and uid(identity['binding_id'])
            and device(identity['remote_vault_id']) and device(identity['current_device_id']), 'PAIR_STAGE_IDENTITY_INVALID')
    files=body['files']
    require(type(files) is list and len(files)==3 and all(type(x) is dict and set(x)=={
        'path','file_id','operation_id','device_id','sequence','base_revision','sha256','bytes'} for x in files),
        'PAIR_STAGE_FILES_INVALID')
    require(all(type(x['path']) is str for x in files) and {x['path'] for x in files}==PATHS,
            'PAIR_STAGE_FILES_INVALID')
    for row in files:
        require(uid(row['file_id']) and uid(row['operation_id']) and device(row['device_id'])
                and type(row['base_revision']) is int and type(row['sequence']) is int
                and 0<=row['base_revision']<row['sequence']<=9007199254740991
                and digest(row['sha256']) and type(row['bytes']) is int and 0<row['bytes']<=4*1024**2,
                'PAIR_STAGE_FILE_INVALID')
    require(len({x['file_id'] for x in files})==len({x['operation_id'] for x in files})==3,
            'PAIR_STAGE_FILES_DUPLICATED')
    if stage=='desktop-first':
        require(all(x['device_id']==identity['current_device_id'] for x in files), 'PAIR_STAGE_DESKTOP_ISSUER_INVALID')
    if stage=='android-return':
        require(next(x for x in files if x['path']=='runtime-note.md')['device_id']==identity['current_device_id'],
                'PAIR_STAGE_ANDROID_ISSUER_INVALID')
    return body


def validate_return(context, desktop, android):
    validate_stage(context,'desktop-first',desktop);validate_stage(context,'android-return',android)
    require(desktop['identity']['remote_vault_id']==android['identity']['remote_vault_id']
            and all(desktop['identity'][k]!=android['identity'][k] for k in ('current_device_id','local_vault_id','binding_id')),
            'PAIR_STAGE_DISTINCT_CLIENTS_REQUIRED')
    before={x['path']:x for x in desktop['files']};after={x['path']:x for x in android['files']}
    require(before[IMAGE_PATH]==after[IMAGE_PATH], 'PAIR_STAGE_UNCHANGED_ORIGINAL_REPLACED')
    for path in ('runtime-note.md','peer.canvas'):
        old,new=before[path],after[path]
        require(new['file_id']==old['file_id'] and new['base_revision']==old['sequence']
                and new['operation_id']!=old['operation_id'] and new['sha256']!=old['sha256']
                and new['device_id']==android['identity']['current_device_id'],
                'PAIR_STAGE_RETURN_ORIGINAL_INVALID')


def validate_final(context, desktop, android, final):
    validate_return(context,desktop,android);validate_stage(context,'desktop-final',final)
    require(final['identity']==desktop['identity'], 'PAIR_STAGE_COLD_DESKTOP_IDENTITY_CHANGED')
    require({x['path']:x for x in final['files']}=={x['path']:x for x in android['files']},
            'PAIR_STAGE_FINAL_ORIGINALS_CHANGED')
