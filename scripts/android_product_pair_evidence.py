"""Match original product transfer metadata to actual owned file bytes.

These checks alone do not prove a client action. Hosted stage controllers must
also retain the real UI action/cleanup receipts and original provider evidence.
"""
from copy import deepcopy
import hashlib
import json

from android_desktop_handoff import require
from android_product_pair_data import TEXT_NODE
from android_product_pair_stages import IMAGE_PATH,PATHS


def sha(value):return hashlib.sha256(value).hexdigest()


def validate_canvas_edit(before,after,text,position):
    require(type(before) is bytes and type(after) is bytes and 0<len(after)<=4*1024**2,
            'PAIR_CANVAS_BYTES_INVALID')
    expected=json.loads(before);actual=json.loads(after)
    selected=[n for n in expected.get('nodes',[]) if n.get('id')==TEXT_NODE]
    require(len(selected)==1 and selected[0].get('type')=='text', 'PAIR_CANVAS_ORIGINAL_NODE_MISSING')
    selected[0].update(text=text,x=position[0],y=position[1])
    require(actual==expected, 'PAIR_CANVAS_UNKNOWN_FIELDS_OR_OTHER_NODES_CHANGED')
    return {'before_sha256':sha(before),'after_sha256':sha(after),
            'unknown_fields_and_other_nodes_preserved':True,
            'before_CRLF_count':before.count(b'\r\n'),'after_CRLF_count':after.count(b'\r\n'),
            'byte_identical_claimed_for_edited_JSON':False}


def validate_scope(native,identity,bodies):
    require(type(bodies) is dict and set(bodies)==PATHS
            and all(type(v) is bytes and 0<len(v)<=4*1024**2 for v in bodies.values()),
            'PAIR_TARGET_BODIES_INVALID')
    binding=native.get('binding',{})
    require(native.get('integrity')=='ok' and binding.get('id')==identity['binding_id']
            and binding.get('remote_vault')==identity['remote_vault_id'] and binding.get('state')=='active'
            and type(binding.get('cursor')) is int and binding['cursor']>=0
            and not native.get('conflicts'), 'PAIR_NATIVE_SCOPE_NOT_CONFIRMED')


def desktop_outbound_files(native,identity,bodies):
    validate_scope(native,identity,bodies);files=[]
    for path,body in sorted(bodies.items()):
        heads=[r for r in native['heads'] if r['path']==path]
        require(len(heads)==1 and heads[0]['hash']==sha(body), 'PAIR_NATIVE_FILE_HEAD_NOT_CONFIRMED')
        head=heads[0]
        jobs=[r for r in native['jobs'] if r['path']==path and r['file_id']==head['file_id']
              and r['hash']==sha(body) and r['size']==len(body) and r['remote_revision']==head['revision']]
        require(len(jobs)==1 and jobs[0]['state']=='acked' and jobs[0]['operation']=='put'
                and jobs[0]['error'] is None, 'PAIR_NATIVE_UPLOAD_NOT_ACKNOWLEDGED')
        job=jobs[0];attempts=[r for r in native['attempts'] if r['operation_id']==job['operation_id']]
        require(len(attempts)==1 and attempts[0]['outcome']=='succeeded' and attempts[0]['error'] is None
                and type(attempts[0]['attempts']) is int and 1<=attempts[0]['attempts']<=4,
                'PAIR_NATIVE_ORIGINAL_ATTEMPT_NOT_CONFIRMED')
        require(type(job['base_revision']) is int and type(job['remote_revision']) is int
                and 0<=job['base_revision']<job['remote_revision']<=binding_cursor(native),
                'PAIR_NATIVE_ORIGINAL_REVISION_INVALID')
        files.append({'path':path,'file_id':job['file_id'],'operation_id':job['operation_id'],
                      'device_id':identity['current_device_id'],'sequence':job['remote_revision'],
                      'base_revision':job['base_revision'],'sha256':sha(body),'bytes':len(body)})
    # Actual preference seeding may produce other legitimate operations. It
    # never authorizes substituting them for one of the three requested files.
    return files


def binding_cursor(native):return native['binding']['cursor']


def desktop_cold_files(native,identity,bodies,previous_native,android_files):
    validate_scope(native,identity,bodies)
    require(type(android_files) is list and len(android_files)==3 and {r['path'] for r in android_files}==PATHS,
            'PAIR_COLD_ORIGINAL_FILES_INVALID')
    def target_jobs(value):return [r for r in value['jobs'] if r['path'] in PATHS]
    require(target_jobs(native)==target_jobs(previous_native), 'PAIR_COLD_REUPLOADED_INCOMING_ORIGINAL')
    original_ops={r['operation_id'] for r in target_jobs(previous_native)}
    require([r for r in native['attempts'] if r['operation_id'] in original_ops]
            ==[r for r in previous_native['attempts'] if r['operation_id'] in original_ops],
            'PAIR_COLD_ORIGINAL_ATTEMPTS_CHANGED')
    for row in android_files:
        path=row['path'];body=bodies[path]
        require(sha(body)==row['sha256'] and len(body)==row['bytes'], 'PAIR_COLD_ORIGINAL_BYTES_CHANGED')
        head=[r for r in native['heads'] if r['file_id']==row['file_id']]
        inbox=[r for r in native['inbox'] if r['sequence']==row['sequence']]
        require(len(head)==len(inbox)==1 and head[0]['path']==path and head[0]['hash']==row['sha256']
                and head[0]['revision']==row['sequence'] and inbox[0]['state']=='applied'
                and binding_cursor(native)>=row['sequence'], 'PAIR_COLD_ORIGINAL_NOT_APPLIED')
    return deepcopy(android_files)


def match_provider_files(files,provider,remote_vault):
    require(provider['vault']['id']==remote_vault and provider.get('secrets_selected') is False
            and provider.get('object_IO') is False, 'PAIR_PROVIDER_SCOPE_INVALID')
    for row in files:
        remote=[r for r in provider['rows']['revisions'] if r['operation_id']==row['operation_id']]
        require(len(remote)==1 and remote[0]['operation']=='put' and all(remote[0][k]==row[k]
                for k in ('file_id','operation_id','device_id','sequence','base_revision','path'))
                and remote[0]['hash']==row['sha256'] and remote[0]['size']==row['bytes'],
                'PAIR_PROVIDER_ORIGINAL_RECEIPT_MISMATCH')
        current=[r for r in provider['rows']['files'] if r['file_id']==row['file_id']]
        require(len(current)==1 and current[0]['sequence']==row['sequence'] and current[0]['deleted']==0
                and current[0]['path_key']==row['path'].casefold(), 'PAIR_PROVIDER_ORIGINAL_HEAD_MISMATCH')
