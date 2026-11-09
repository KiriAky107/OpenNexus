"""Private real-API setup for the exclusive hosted Sync fixture.

This is an independent setup actor, never desktop/Android product evidence.
Opaque sessions and original responses remain in the private owner directory.
Login and vault-create each send at most once; unknown create reconciles by the
original unique name through the real vault-list API, never a fabricated ID API.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import re
import ssl
import time
import uuid
from urllib.parse import urlsplit
import android_auth_fixture as original
from android_sync_fixture import freeze_target, observe_provider


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')


_FRESH_ERROR = 'OWNED_SYNC_FRESH_QUOTA32_SETUP_UNAVAILABLE'


class Setup:
    def __init__(self,owner,*,fresh_quota32=False):
        self._fresh_quota32=False
        self._fresh_state='NEW'
        self._fresh_seen=set()
        if type(fresh_quota32) is not bool:self._fresh_fail()
        self._fresh_quota32=fresh_quota32
        try:self._initialize(owner)
        except BaseException:
            if self._fresh_quota32:self._fresh_fail()
            raise

    def _fresh_fail(self):
        self._fresh_state='FAILED'
        raise RuntimeError(_FRESH_ERROR) from None

    def _fresh_need(self,value):
        if not value:self._fresh_fail()

    def _fresh_enter(self,kind):
        if not self._fresh_quota32:return
        self._fresh_need(self._fresh_state=='RUNNING')
        if kind in ('session','vault'):
            self._fresh_need(kind not in self._fresh_seen)
            self._fresh_seen.add(kind)

    def _fresh_absent(self,path):
        self._fresh_need(not self._step(path.exists) and not self._step(path.is_symlink))

    def _step(self,function,*args,**kwargs):
        if self._fresh_quota32:self._fresh_need(self._fresh_state=='RUNNING')
        value=function(*args,**kwargs)
        if self._fresh_quota32:self._fresh_need(self._fresh_state=='RUNNING')
        return value

    def _initialize(self,owner):
        original.require(owner.resolve()==original.owned_root(),'OWNED_SYNC_SETUP_ROOT_INVALID')
        config=original.fixed_service(owner)
        state=original.read_json(owner/'owner-state.json')
        device=original.read_json(owner/'device-private.json')
        endpoint=state.get('endpoint','')
        original.require(state.get('source')==original.SERVICE_SHA and state.get('result')=='ready'
            and state.get('fixture') in ('sync','sync-expiration','sync-quota','signed-release-quota32') and re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com',endpoint)
            and device=={'endpoint':endpoint,'accounts':config['accounts']},'OWNED_SYNC_SETUP_SCOPE_INVALID')
        if self._fresh_quota32:self._fresh_need(state.get('fixture')=='signed-release-quota32')
        if state.get('fixture') == 'signed-release-quota32':
            from android_release_sync_quota_fixture import require_prepare_proof
            require_prepare_proof(owner, state=state, config=config)
        self.owner,self.endpoint=owner,endpoint
        self.account=config['accounts'][0]
        original.require(re.fullmatch(r'android-owned-a-[0-9a-f]{12}',self.account['username'])
            and 40<=len(self.account['password'])<=100,'OWNED_SYNC_SETUP_SCOPE_INVALID')
        self.root=owner/'sync-setup'
        original.require(not self.root.is_symlink(),'OWNED_SYNC_SETUP_ROOT_INVALID')
        self.root.mkdir(mode=0o700,exist_ok=not self._fresh_quota32)

    def request(self,method,path,body=None,token=None):
        """No redirects, generic endpoints, raw provider errors or POST retries."""
        self._fresh_enter('request')
        allowed=(method,path) in {('POST','/sync/v1/auth/sessions'),('POST','/sync/v1/vaults'),('GET','/sync/v1/vaults')}
        original.require(allowed,'OWNED_SYNC_SETUP_ROUTE_INVALID')
        endpoint=urlsplit(self.endpoint)
        payload=None if body is None else encoded(body)
        original.require(payload is None or len(payload)<=4096,'OWNED_SYNC_SETUP_BODY_INVALID')
        headers={'Accept':'application/json'}
        if payload is not None:headers['Content-Type']='application/json'
        if token is not None:
            original.require(isinstance(token,str) and re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token),'OWNED_SYNC_SETUP_SESSION_INVALID')
            headers['Authorization']='Bearer '+token
        connection=self._step(http.client.HTTPSConnection,endpoint.hostname,timeout=25,context=ssl.create_default_context())
        try:
            self._step(connection.request,method,path,body=payload,headers=headers)
            response=self._step(connection.getresponse);raw=self._step(response.read,65537)
            original.require(len(raw)<=65536,'OWNED_SYNC_SETUP_RESPONSE_INVALID')
            value=json.loads(raw)
            original.require(isinstance(value,dict),'OWNED_SYNC_SETUP_RESPONSE_INVALID')
            return {'status':response.status,'received_at':int(time.time()),'body':value}
        finally:connection.close()

    def intent(self,kind,body):
        self._fresh_enter('intent')
        original.require(kind in ('login','vault'),'OWNED_SYNC_SETUP_ROUTE_INVALID')
        path=self.root/(kind+'-intent.json')
        if self._fresh_quota32:self._fresh_absent(path)
        if not self._fresh_quota32 and path.exists():
            value=original.read_json(path)
            original.require(value['kind']==kind and value['body_sha256']==hashlib.sha256(encoded(body)).hexdigest(),
                'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
            return value,False
        value={'kind':kind,'operation_id':str(uuid.uuid4()),'body_sha256':hashlib.sha256(encoded(body)).hexdigest(),
            'body':body,'server_operation_id_available':False}
        self._step(original.atomic_json,path,value,exclusive=True)
        return value,True

    def session(self):
        self._fresh_enter('session')
        intent_path=self.root/'login-intent.json'
        if self._fresh_quota32:
            for name in ('login-intent.json','login-response.private.json'):
                self._fresh_absent(self.root/name)
        if not self._fresh_quota32 and intent_path.exists():
            intent=original.read_json(intent_path)
            body=intent['body']
            original.require(body.get('username')==self.account['username'] and body.get('password')==self.account['password']
                and re.fullmatch(r'Owned setup [0-9a-f]{32}',body.get('device_name','')),'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
        else:
            body={'username':self.account['username'],'password':self.account['password'],
                'device_name':'Owned setup '+uuid.uuid4().hex}
        intent,fresh=self.intent('login',body)
        response_path=self.root/'login-response.private.json'
        if self._fresh_quota32:self._fresh_absent(response_path)
        if self._fresh_quota32 or not response_path.exists():
            original.require(fresh,'OWNED_SYNC_SETUP_LOGIN_UNKNOWN')
            response=self._step(self.request,'POST','/sync/v1/auth/sessions',body)
            self._step(original.atomic_json,response_path,response,exclusive=True)
        response=self._step(original.read_json,response_path)
        original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
        value=response['body']
        original.require(set(value)=={'access_token','refresh_token','expires_in','device_id','must_change_credentials'}
            and all(isinstance(value[key],str) and re.fullmatch(r'[A-Za-z0-9_-]{32,256}',value[key]) for key in ('access_token','refresh_token'))
            and re.fullmatch(r'[0-9a-f]{32}',value['device_id']) and value['expires_in']==900
            and value['must_change_credentials'] is False and type(response['received_at']) is int
            and response['received_at']+value['expires_in']>time.time(),'OWNED_SYNC_SETUP_SESSION_INVALID')
        return value

    def vault(self,session):
        self._fresh_enter('vault')
        path=self.root/'vault-intent.json'
        if self._fresh_quota32:
            for name in ('vault-intent.json','vault-receipt.json','vault-response.private.json'):
                self._fresh_absent(self.root/name)
        body=original.read_json(path)['body'] if not self._fresh_quota32 and path.exists() else {'name':'Owned Android Sync '+uuid.uuid4().hex}
        original.require(set(body)=={'name'} and re.fullmatch(r'Owned Android Sync [0-9a-f]{32}',body['name']),
            'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
        intent,fresh=self.intent('vault',body)
        receipt_path=self.root/'vault-receipt.json'
        if not self._fresh_quota32 and receipt_path.exists():
            receipt=original.read_json(receipt_path)
            original.require(receipt['operation_id']==intent['operation_id'] and receipt['name']==body['name']
                and re.fullmatch(r'[0-9a-f]{32}',receipt['vault_id']),'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
            return receipt
        response_path=self.root/'vault-response.private.json'
        if not self._fresh_quota32 and response_path.exists():
            response=original.read_json(response_path)
            original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
            value=response['body'];delivery='original_response_received'
        else:
            response=self._step(self.request,'GET','/sync/v1/vaults',token=session['access_token'])
            original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
            listed=response['body']
            original.require(set(listed)=={'items'} and isinstance(listed['items'],list) and len(listed['items'])<=512,
                'OWNED_SYNC_SETUP_RESPONSE_INVALID')
            matches=[item for item in listed['items'] if item.get('name')==body['name']]
            original.require(len(matches)<=1,'OWNED_SYNC_SETUP_VAULT_AMBIGUOUS')
            if matches:
                if self._fresh_quota32:self._fresh_fail()
                value={'vault_id':matches[0]['id'],'name':matches[0]['name']};delivery='reconciled_by_original_name_GET'
            else:
                original.require(fresh,'OWNED_SYNC_SETUP_VAULT_UNKNOWN')
                response=self._step(self.request,'POST','/sync/v1/vaults',body,session['access_token'])
                self._step(original.atomic_json,response_path,response,exclusive=True)
                original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
                value=response['body'];delivery='original_response_received'
        original.require(set(value)=={'vault_id','name'} and re.fullmatch(r'[0-9a-f]{32}',value['vault_id'])
            and value['name']==body['name'],'OWNED_SYNC_SETUP_RESPONSE_INVALID')
        receipt={'operation_id':intent['operation_id'],'vault_id':value['vault_id'],'name':value['name'],
            'delivery':delivery,'server_operation_id_available':False}
        self._step(original.atomic_json,receipt_path,receipt,exclusive=True)
        return receipt

    def run(self):
        """Fresh mode is a concrete one-shot Setup call, not runtime admission.

        The source-owning factory must own this exact instance and original
        GUI/history lineage. A returned dict or this option cannot attest it.
        Directory replacement/private-fd and host-child lifetime gates remain
        separate; no immutable-file or Android/Native acceptance is implied.
        """
        if not self._fresh_quota32:return self._run()
        self._fresh_need(self._fresh_state=='NEW')
        self._fresh_state='RUNNING'
        try:
            self._fresh_need(self._step(self.root.is_dir) is True)
            self._fresh_need(self._step(self.root.is_symlink) is False)
            self._fresh_need(self._step(lambda:next(self.root.iterdir(),None)) is None)
            value=self._run()
            self._fresh_need(self._fresh_state=='RUNNING')
            self._fresh_state='DONE'
            return value
        except BaseException:self._fresh_fail()

    def _run(self):
        session=self._step(self.session);vault=self._step(self.vault,session)
        observation=self._step(observe_provider,self.owner,vault['vault_id'])
        self._step(freeze_target,self.owner,vault['vault_id'])
        return {'source':original.SERVICE_SHA,'kind':'independent_setup_actor_only','vault':vault,
            'device_id':session['device_id'],'real_API_setup':True,'desktop_or_Android_verified':False,
            'provider':observation,'credentials_or_session_in_artifacts':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args()
    owner=original.owned_root();print(json.dumps(Setup(owner).run()))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('OWNED_SYNC_SETUP_FAILED_'+type(error).__name__,file=__import__('sys').stderr)
        raise SystemExit(1)
