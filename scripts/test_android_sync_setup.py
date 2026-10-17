"""Unit-only original setup request journal; no real provider, HTTPS or device."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import android_auth_fixture as original
import android_sync_setup as module


class OwnedSyncSetupTests(unittest.TestCase):
    def configuration(self,root,*,fixture='sync',endpoint='https://unit-owned.trycloudflare.com'):
        accounts=[{'username':'android-owned-'+kind+'-'+'a'*12,'password':'unit-only-'+kind+'x'*44,
            'device_name':'Owned Android '+kind} for kind in ('a','b')]
        config={'accounts':accounts}
        original.atomic_json(root/'owner-state.json',{'source':original.SERVICE_SHA,'result':'ready','fixture':fixture,'endpoint':endpoint},exclusive=True)
        original.atomic_json(root/'device-private.json',{'endpoint':endpoint,'accounts':accounts},exclusive=True)
        return config

    def setup(self,root,config):
        with patch.object(original,'owned_root',return_value=root.resolve()),patch.object(original,'fixed_service',return_value=config):
            return module.Setup(root)

    def session(self):
        return {'access_token':'a'*43,'refresh_token':'b'*64,'expires_in':900,'device_id':'c'*32,'must_change_credentials':False}

    def response(self,body,*,status=200):return {'status':status,'received_at':100,'body':body}

    def test_only_exact_ready_owned_sync_scope_can_create_setup_state_before_network(self):
        for options in ({'fixture':'auth'},{'endpoint':'http://unit-owned.trycloudflare.com'},{'endpoint':'https://production.example'}):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);config=self.configuration(root,**options)
                with patch.object(module.http.client,'HTTPSConnection') as network:
                    with self.assertRaisesRegex(RuntimeError,'SCOPE_INVALID'):self.setup(root,config)
                    self.assertFalse((root/'sync-setup').exists());network.assert_not_called()

    def test_original_login_body_is_private_and_a_known_response_never_sends_a_second_login(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root))
            reply=self.response(self.session())
            with patch.object(client,'request',return_value=reply) as request,patch.object(module.time,'time',return_value=101):
                self.assertEqual(client.session(),self.session());self.assertEqual(client.session(),self.session())
                request.assert_called_once()
            method,path,body=request.call_args.args
            self.assertEqual((method,path),('POST','/sync/v1/auth/sessions'))
            self.assertEqual(set(body),{'username','password','device_name'})
            original_intent=original.read_json(client.root/'login-intent.json')
            self.assertFalse(original_intent['server_operation_id_available'])
            self.assertEqual(original_intent['body'],body)
            self.assertEqual(original.read_json(client.root/'login-response.private.json'),reply)

    def test_unknown_login_without_opaque_response_refuses_automatic_new_session_or_repost(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root))
            with patch.object(client,'request',side_effect=TimeoutError('unit-only interrupted response')) as request:
                with self.assertRaises(TimeoutError):client.session()
                before=(client.root/'login-intent.json').read_bytes()
                with self.assertRaisesRegex(RuntimeError,'LOGIN_UNKNOWN'):client.session()
                self.assertEqual((client.root/'login-intent.json').read_bytes(),before)
                request.assert_called_once()

    def test_received_rejection_and_expiry_are_not_activated_or_replaced(self):
        for response,clock,error in [(self.response({'error':{'code':'LOGIN_FAILED'}},status=401),101,'HTTP_REJECTED'),
                (self.response(self.session()),1001,'SESSION_INVALID')]:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);client=self.setup(root,self.configuration(root))
                with patch.object(client,'request',return_value=response) as request,patch.object(module.time,'time',return_value=clock):
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError,error):client.session()
                    request.assert_called_once()
                self.assertEqual(original.read_json(client.root/'login-response.private.json'),response)

    def test_vault_creation_uses_only_actual_name_body_and_reuses_received_original_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root));calls=[]
            def api(method,path,body=None,token=None):
                calls.append((method,path,body,token))
                if method=='GET':return self.response({'items':[]})
                return self.response({'vault_id':'d'*32,'name':body['name']})
            with patch.object(client,'request',side_effect=api):
                receipt=client.vault(self.session());before=(client.root/'vault-intent.json').read_bytes()
                self.assertEqual(client.vault(self.session()),receipt)
            self.assertEqual([method for method,*_ in calls],['GET','POST'])
            self.assertEqual(set(calls[-1][2]),{'name'})
            self.assertFalse(receipt['server_operation_id_available'])
            self.assertEqual(receipt['delivery'],'original_response_received')
            self.assertEqual((client.root/'vault-intent.json').read_bytes(),before)

    def test_lost_vault_response_recovers_by_original_unique_name_get_without_reposting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root));remote=[];calls=[]
            def api(method,path,body=None,token=None):
                calls.append((method,path,body))
                if method=='GET':return self.response({'items':list(remote)})
                remote.append({'id':'d'*32,'name':body['name'],'sequence':0,'used':0,'quota':100})
                raise ConnectionError('unit-only reply lost after fake provider accepted')
            with patch.object(client,'request',side_effect=api):
                with self.assertRaises(ConnectionError):client.vault(self.session())
                before=(client.root/'vault-intent.json').read_bytes()
                receipt=client.vault(self.session())
            self.assertEqual([method for method,*_ in calls],['GET','POST','GET'])
            self.assertEqual(receipt['vault_id'],'d'*32);self.assertEqual(receipt['delivery'],'reconciled_by_original_name_GET')
            self.assertEqual((client.root/'vault-intent.json').read_bytes(),before)
            self.assertFalse((client.root/'vault-response.private.json').exists())

    def test_unknown_missing_or_ambiguous_vault_does_not_become_a_new_post(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root));name=None;calls=[]
            def api(method,path,body=None,token=None):
                nonlocal name
                calls.append(method)
                if method=='POST':name=body['name'];raise ConnectionError('unit-only unknown')
                return self.response({'items':[]})
            with patch.object(client,'request',side_effect=api):
                with self.assertRaises(ConnectionError):client.vault(self.session())
                with self.assertRaisesRegex(RuntimeError,'VAULT_UNKNOWN'):client.vault(self.session())
            self.assertEqual(calls,['GET','POST','GET'])
            duplicate={'items':[{'id':'d'*32,'name':name},{'id':'e'*32,'name':name}]}
            with patch.object(client,'request',return_value=self.response(duplicate)) as request:
                with self.assertRaisesRegex(RuntimeError,'VAULT_AMBIGUOUS'):client.vault(self.session())
                self.assertEqual(request.call_args.args,('GET','/sync/v1/vaults'))

    def test_original_private_request_body_cannot_be_changed_after_it_is_journaled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root))
            with patch.object(client,'request',side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):client.session()
            path=client.root/'login-intent.json';intent=original.read_json(path)
            intent['body']['password']='unit-only-changed-password'*2;original.atomic_json(path,intent)
            with patch.object(client,'request') as request:
                with self.assertRaisesRegex(RuntimeError,'ORIGINAL_CHANGED'):client.session()
                request.assert_not_called()

    def test_https_transport_keeps_verification_and_never_follows_redirect_or_accepts_extra_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=self.setup(root,self.configuration(root));response=Mock()
            response.status=302;response.read.return_value=b'{"redirect":"https://other.example"}'
            connection=Mock();connection.getresponse.return_value=response
            with patch.object(module.http.client,'HTTPSConnection',return_value=connection) as factory:
                receipt=client.request('POST','/sync/v1/auth/sessions',{'original':'unit-only'})
                self.assertEqual(receipt['status'],302);factory.assert_called_once()
                self.assertEqual(factory.call_args.args,('unit-owned.trycloudflare.com',))
                context=factory.call_args.kwargs['context']
                self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,module.ssl.CERT_REQUIRED)
                self.assertEqual(connection.request.call_count,1);connection.close.assert_called_once()
            with patch.object(module.http.client,'HTTPSConnection') as network:
                for method,path in [('GET','https://other.example'),('DELETE','/sync/v1/vaults'),('POST','/sync/v1/arbitrary')]:
                    with self.assertRaisesRegex(RuntimeError,'ROUTE_INVALID'):client.request(method,path)
                network.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'ROUTE_INVALID'):client.intent('../outside',{})
            self.assertFalse((root/'outside-intent.json').exists())
