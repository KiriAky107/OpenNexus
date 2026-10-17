"""Bounded original-HTTP peer for owned Android Sync acceptance, not a desktop product.

Only the already prepared exclusive fixture/session/vault is used. Intents and
original bytes are private and durable before writes. Unknown revision results
are reconciled through real changes pages, never a made-up operation endpoint.
"""
import hashlib
import http.client
import json
from pathlib import Path
import re
import ssl
import uuid
from urllib.parse import urlsplit
import android_auth_fixture as original
from android_sync_fixture import selected_target
from android_sync_setup import Setup,encoded

IMAGE_PATH='attachments/14/14be779d5555c7061cacf2f4979b63fb54737c221c723540b6b4dd9e8d13bd29.png'
PATHS=frozenset(('runtime-note.md','peer-note.md','peer-renamed.md','peer.canvas',IMAGE_PATH))


def revision_matches(value,body,device,vault):
    return all(value.get(key)==body[source] for key,source in (
        ('operation_id','operation_id'),('file_id','file_id'),('base_revision','base_revision'),
        ('path','path'),('operation','operation'),('hash','content_hash'),('size','size'))) and value.get('device_id')==device \
        and value.get('vault_id')==vault and type(value.get('sequence')) is int \
        and body['base_revision']<value['sequence']<=9007199254740991


class Peer:
    def __init__(self,owner):
        self.setup=Setup(owner)
        original.require((self.setup.root/'login-response.private.json').is_file()
            and (self.setup.root/'vault-receipt.json').is_file(),'OWNED_PEER_SETUP_REQUIRED')
        self.vault=original.read_json(self.setup.root/'vault-receipt.json')['vault_id']
        original.require(selected_target(owner)==self.vault and re.fullmatch(r'[0-9a-f]{32}',self.vault),'OWNED_PEER_TARGET_CHANGED')
        self.device=self.setup.session()['device_id']
        self.root=owner/'sync-peer';original.require(not self.root.is_symlink(),'OWNED_PEER_ROOT_INVALID')
        self.root.mkdir(mode=0o700,exist_ok=True)
        self.prefix='/sync/v1/vaults/'+self.vault

    def request(self,method,path,body=None):
        suffix=path.removeprefix(self.prefix)
        allowed=path.startswith(self.prefix) and (
            method=='POST' and suffix in ('/uploads','/revisions')
            or method=='POST' and re.fullmatch(r'/uploads/[0-9a-f]{32}/complete',suffix)
            or method=='PUT' and re.fullmatch(r'/uploads/[0-9a-f]{32}\?offset=0',suffix)
            or method=='GET' and re.fullmatch(r'/changes\?cursor=[0-9]{1,16}&limit=500(?:&boundary=[0-9]{1,16})?',suffix))
        original.require(allowed,'OWNED_PEER_ROUTE_INVALID')
        payload=body if isinstance(body,bytes) else None if body is None else encoded(body)
        original.require(payload is None or len(payload)<=1024*1024,'OWNED_PEER_BODY_LIMIT')
        session=self.setup.session();original.require(session['device_id']==self.device,'OWNED_PEER_DEVICE_CHANGED')
        headers={'Authorization':'Bearer '+session['access_token'],'Accept':'application/json'}
        if payload is not None:headers['Content-Type']='application/octet-stream' if isinstance(body,bytes) else 'application/json'
        connection=http.client.HTTPSConnection(urlsplit(self.setup.endpoint).hostname,timeout=25,context=ssl.create_default_context())
        try:
            connection.request(method,path,body=payload,headers=headers)
            response=connection.getresponse();raw=response.read(2*1024*1024+1)
            original.require(len(raw)<=2*1024*1024,'OWNED_PEER_RESPONSE_LIMIT')
            value=json.loads(raw);original.require(isinstance(value,dict),'OWNED_PEER_RESPONSE_INVALID')
            return {'status':response.status,'body':value}
        finally:connection.close()

    def once(self,folder,name,method,path,body=None):
        intent=folder/(name+'-intent.json');response=folder/(name+'-response.json')
        payload=body if isinstance(body,bytes) else b'' if body is None else encoded(body)
        value={'method':method,'path':path,'body_sha256':hashlib.sha256(payload).hexdigest(),'body_bytes':len(payload)}
        if intent.exists():
            original.require(original.read_json(intent)==value,'OWNED_PEER_ORIGINAL_CHANGED')
            original.require(response.exists(),'OWNED_PEER_UPLOAD_RESULT_UNKNOWN')
        else:
            original.require(not response.exists(),'OWNED_PEER_ORIGINAL_CHANGED')
            original.atomic_json(intent,value,exclusive=True)
            result=self.request(method,path,body)
            original.atomic_json(response,result,exclusive=True)
        return original.read_json(response)

    def changes(self):
        items=[];cursor=0;boundary=None;self.last_lookup=[]
        for page in range(2):
            path=self.prefix+'/changes?cursor='+str(cursor)+'&limit=500'
            if boundary is not None:path+='&boundary='+str(boundary)
            value=self.request('GET',path)
            frame={'method':'GET','path':path,'response':value,
                'server_offered_progress_may_advance':True,'client_apply_acknowledged':False}
            frame_path=self.root/('changes-'+uuid.uuid4().hex+'.json')
            original.atomic_json(frame_path,frame,exclusive=True)
            self.last_lookup.append({'file':frame_path.name,'sha256':hashlib.sha256(frame_path.read_bytes()).hexdigest()})
            original.require(value['status']==200,'OWNED_PEER_CHANGES_REJECTED');body=value['body']
            original.require(set(body)=={'items','cursor','boundary','has_more'} and isinstance(body['items'],list)
                and len(body['items'])<=500 and type(body['cursor']) is int and type(body['boundary']) is int
                and type(body['has_more']) is bool and cursor<=body['cursor']<=body['boundary']<=9007199254740991
                and (boundary is None or body['boundary']==boundary),'OWNED_PEER_CHANGES_INVALID')
            values=body['items'];sequences=[row.get('sequence') for row in values]
            original.require(all(type(seq) is int for seq in sequences) and sequences==sorted(set(sequences))
                and all(cursor<seq<=body['boundary'] for seq in sequences)
                and body['cursor']==(sequences[-1] if sequences else cursor)
                and body['has_more']==(body['cursor']<body['boundary']),'OWNED_PEER_CHANGES_INVALID')
            items.extend(values);original.require(len(items)<=512,'OWNED_PEER_CHANGES_LIMIT')
            cursor,boundary=body['cursor'],body['boundary']
            if not body['has_more']:return items
        raise RuntimeError('OWNED_PEER_CHANGES_LIMIT')

    def upload(self,folder,content):
        digest=hashlib.sha256(content).hexdigest()
        result=self.once(folder,'upload','POST',self.prefix+'/uploads',{'content_hash':digest,'size':len(content)})
        original.require(result['status']==200,'OWNED_PEER_UPLOAD_REJECTED');start=result['body']
        original.require(set(start)=={'complete','upload_id','offset'} and type(start['complete']) is bool
            and type(start['offset']) is int,'OWNED_PEER_UPLOAD_INVALID')
        if start['complete']:
            original.require(start['upload_id'] is None and start['offset']==len(content),'OWNED_PEER_UPLOAD_INVALID');return
        upload=start['upload_id'];original.require(re.fullmatch(r'[0-9a-f]{32}',upload) and start['offset']==0,'OWNED_PEER_UPLOAD_INVALID')
        result=self.once(folder,'chunk','PUT',self.prefix+'/uploads/'+upload+'?offset=0',content)
        original.require(result=={'status':200,'body':{'offset':len(content)}},'OWNED_PEER_CHUNK_UNCONFIRMED')
        result=self.once(folder,'complete','POST',self.prefix+'/uploads/'+upload+'/complete')
        original.require(result=={'status':200,'body':{'complete':True,'content_hash':digest}},'OWNED_PEER_COMPLETE_UNCONFIRMED')

    def publish(self,label,path,content,*,file_id=None,base_revision=0,operation='put',retry_original=False):
        with original.control_lock(self.root):
            return self._publish(label,path,content,file_id=file_id,base_revision=base_revision,
                operation=operation,retry_original=retry_original)

    def _publish(self,label,path,content,*,file_id=None,base_revision=0,operation='put',retry_original=False):
        original.require(re.fullmatch(r'[a-z][a-z0-9-]{0,31}',label) and path in PATHS
            and isinstance(content,bytes) and len(content)<=1024*1024 and type(base_revision) is int
            and 0<=base_revision<=9007199254740991 and operation in ('put','delete')
            and (operation!='delete' or content==b''),'OWNED_PEER_ACTION_INVALID')
        folder=self.root/label;original.require(not folder.is_symlink(),'OWNED_PEER_ROOT_INVALID');folder.mkdir(mode=0o700,exist_ok=True)
        intent_path=folder/'revision-intent.json';payload_path=folder/'original.bin';receipt_path=folder/'revision-receipt.json'
        fresh=not intent_path.exists()
        if fresh:
            body={'operation_id':str(uuid.uuid4()),'file_id':file_id or str(uuid.uuid4()),'base_revision':base_revision,
                'path':path,'operation':operation,'content_hash':hashlib.sha256(content).hexdigest() if operation=='put' else None,'size':len(content)}
            original.require(re.fullmatch(r'[a-zA-Z0-9-]{16,80}',body['file_id']),'OWNED_PEER_ACTION_INVALID')
            original.require(not payload_path.exists() and not receipt_path.exists(),'OWNED_PEER_ORIGINAL_CHANGED')
            # All source bytes and original IDs precede any object/revision write.
            import os
            fd=os.open(payload_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as out:out.write(content);out.flush();os.fsync(out.fileno())
            intent={'body':body,'sha256':hashlib.sha256(encoded(body)).hexdigest(),'device_id':self.device}
            original.atomic_json(intent_path,intent,exclusive=True)
        else:
            intent=original.read_json(intent_path);body=intent['body']
            original.require(not payload_path.is_symlink() and payload_path.stat().st_size==len(content) and payload_path.read_bytes()==content
                and intent['sha256']==hashlib.sha256(encoded(body)).hexdigest() and intent['device_id']==self.device
                and body['path']==path and body['operation']==operation and body['base_revision']==base_revision
                and (file_id is None or body['file_id']==file_id),'OWNED_PEER_ORIGINAL_CHANGED')
        if receipt_path.exists():
            receipt=original.read_json(receipt_path)
            original.require(revision_matches(receipt,body,self.device,self.vault),'OWNED_PEER_RECEIPT_CHANGED');return receipt
        matches=[row for row in self.changes() if row.get('operation_id')==body['operation_id']]
        original.require(len(matches)<=1,'OWNED_PEER_ORIGINAL_AMBIGUOUS')
        if matches:
            receipt=matches[0]
            original.require(revision_matches(receipt,body,self.device,self.vault),'OWNED_PEER_RECEIPT_CHANGED')
        else:
            attempted=folder/'revision-attempted.json'
            original.require(fresh or not attempted.exists() or retry_original,'OWNED_PEER_COMMIT_UNKNOWN')
            original.require(len(list(folder.glob('revision-retry-*.json')))<1,'OWNED_PEER_EXPLICIT_RETRY_LIMIT')
            # A retry is caller-explicit, only after the preceding actual GET
            # found no original operation. No new IDs/body/device are generated.
            attempt_path=folder/('revision-retry-'+uuid.uuid4().hex+'.json') if attempted.exists() else attempted
            original.atomic_json(attempt_path,{'body_sha256':intent['sha256'],'operation_id':body['operation_id'],
                'explicit_original_retry':attempted.exists(),'original_lookup_missing':True,
                'actual_lookup_frames':self.last_lookup},exclusive=True)
            if operation=='put':self.upload(folder,content)
            response=self.request('POST',self.prefix+'/revisions',body)
            original.atomic_json(folder/('revision-response-'+uuid.uuid4().hex+'.json'),response,exclusive=True)
            original.require(response['status']==200,'OWNED_PEER_COMMIT_UNCONFIRMED')
            receipt=response['body'];original.require(revision_matches(receipt,body,self.device,self.vault),'OWNED_PEER_RECEIPT_CHANGED')
        original.atomic_json(receipt_path,receipt,exclusive=True)
        return receipt
