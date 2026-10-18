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


class Setup:
    def __init__(self,owner):
        original.require(owner.resolve()==original.owned_root(),'OWNED_SYNC_SETUP_ROOT_INVALID')
        config=original.fixed_service(owner)
        state=original.read_json(owner/'owner-state.json')
        device=original.read_json(owner/'device-private.json')
        endpoint=state.get('endpoint','')
        original.require(state.get('source')==original.SERVICE_SHA and state.get('result')=='ready'
            and state.get('fixture') in ('sync','sync-expiration','sync-quota') and re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com',endpoint)
            and device=={'endpoint':endpoint,'accounts':config['accounts']},'OWNED_SYNC_SETUP_SCOPE_INVALID')
        self.owner,self.endpoint=owner,endpoint
        self.account=config['accounts'][0]
        original.require(re.fullmatch(r'android-owned-a-[0-9a-f]{12}',self.account['username'])
            and 40<=len(self.account['password'])<=100,'OWNED_SYNC_SETUP_SCOPE_INVALID')
        self.root=owner/'sync-setup'
        original.require(not self.root.is_symlink(),'OWNED_SYNC_SETUP_ROOT_INVALID')
        self.root.mkdir(mode=0o700,exist_ok=True)

    def request(self,method,path,body=None,token=None):
        """No redirects, generic endpoints, raw provider errors or POST retries."""
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
        connection=http.client.HTTPSConnection(endpoint.hostname,timeout=25,context=ssl.create_default_context())
        try:
            connection.request(method,path,body=payload,headers=headers)
            response=connection.getresponse();raw=response.read(65537)
            original.require(len(raw)<=65536,'OWNED_SYNC_SETUP_RESPONSE_INVALID')
            value=json.loads(raw)
            original.require(isinstance(value,dict),'OWNED_SYNC_SETUP_RESPONSE_INVALID')
            return {'status':response.status,'received_at':int(time.time()),'body':value}
        finally:connection.close()

    def intent(self,kind,body):
        original.require(kind in ('login','vault'),'OWNED_SYNC_SETUP_ROUTE_INVALID')
        path=self.root/(kind+'-intent.json')
        if path.exists():
            value=original.read_json(path)
            original.require(value['kind']==kind and value['body_sha256']==hashlib.sha256(encoded(body)).hexdigest(),
                'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
            return value,False
        value={'kind':kind,'operation_id':str(uuid.uuid4()),'body_sha256':hashlib.sha256(encoded(body)).hexdigest(),
            'body':body,'server_operation_id_available':False}
        original.atomic_json(path,value,exclusive=True)
        return value,True

    def session(self):
        intent_path=self.root/'login-intent.json'
        if intent_path.exists():
            intent=original.read_json(intent_path)
            body=intent['body']
            original.require(body.get('username')==self.account['username'] and body.get('password')==self.account['password']
                and re.fullmatch(r'Owned setup [0-9a-f]{32}',body.get('device_name','')),'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
        else:
            body={'username':self.account['username'],'password':self.account['password'],
                'device_name':'Owned setup '+uuid.uuid4().hex}
        intent,fresh=self.intent('login',body)
        response_path=self.root/'login-response.private.json'
        if not response_path.exists():
            original.require(fresh,'OWNED_SYNC_SETUP_LOGIN_UNKNOWN')
            response=self.request('POST','/sync/v1/auth/sessions',body)
            original.atomic_json(response_path,response,exclusive=True)
        response=original.read_json(response_path)
        original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
        value=response['body']
        original.require(set(value)=={'access_token','refresh_token','expires_in','device_id','must_change_credentials'}
            and all(isinstance(value[key],str) and re.fullmatch(r'[A-Za-z0-9_-]{32,256}',value[key]) for key in ('access_token','refresh_token'))
            and re.fullmatch(r'[0-9a-f]{32}',value['device_id']) and value['expires_in']==900
            and value['must_change_credentials'] is False and type(response['received_at']) is int
            and response['received_at']+value['expires_in']>time.time(),'OWNED_SYNC_SETUP_SESSION_INVALID')
        return value

    def vault(self,session):
        path=self.root/'vault-intent.json'
        body=original.read_json(path)['body'] if path.exists() else {'name':'Owned Android Sync '+uuid.uuid4().hex}
        original.require(set(body)=={'name'} and re.fullmatch(r'Owned Android Sync [0-9a-f]{32}',body['name']),
            'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
        intent,fresh=self.intent('vault',body)
        receipt_path=self.root/'vault-receipt.json'
        if receipt_path.exists():
            receipt=original.read_json(receipt_path)
            original.require(receipt['operation_id']==intent['operation_id'] and receipt['name']==body['name']
                and re.fullmatch(r'[0-9a-f]{32}',receipt['vault_id']),'OWNED_SYNC_SETUP_ORIGINAL_CHANGED')
            return receipt
        response_path=self.root/'vault-response.private.json'
        if response_path.exists():
            response=original.read_json(response_path)
            original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
            value=response['body'];delivery='original_response_received'
        else:
            response=self.request('GET','/sync/v1/vaults',token=session['access_token'])
            original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
            listed=response['body']
            original.require(set(listed)=={'items'} and isinstance(listed['items'],list) and len(listed['items'])<=512,
                'OWNED_SYNC_SETUP_RESPONSE_INVALID')
            matches=[item for item in listed['items'] if item.get('name')==body['name']]
            original.require(len(matches)<=1,'OWNED_SYNC_SETUP_VAULT_AMBIGUOUS')
            if matches:
                value={'vault_id':matches[0]['id'],'name':matches[0]['name']};delivery='reconciled_by_original_name_GET'
            else:
                original.require(fresh,'OWNED_SYNC_SETUP_VAULT_UNKNOWN')
                response=self.request('POST','/sync/v1/vaults',body,session['access_token'])
                original.atomic_json(response_path,response,exclusive=True)
                original.require(response['status']==200,'OWNED_SYNC_SETUP_HTTP_REJECTED')
                value=response['body'];delivery='original_response_received'
        original.require(set(value)=={'vault_id','name'} and re.fullmatch(r'[0-9a-f]{32}',value['vault_id'])
            and value['name']==body['name'],'OWNED_SYNC_SETUP_RESPONSE_INVALID')
        receipt={'operation_id':intent['operation_id'],'vault_id':value['vault_id'],'name':value['name'],
            'delivery':delivery,'server_operation_id_available':False}
        original.atomic_json(receipt_path,receipt,exclusive=True)
        return receipt

    def run(self):
        session=self.session();vault=self.vault(session)
        observation=observe_provider(self.owner,vault['vault_id'])
        freeze_target(self.owner,vault['vault_id'])
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
