"""Controlled factory/PG/S3/ASGI responses only; no actual hosted runtime."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import android_auth_hosted as hosted
import android_release_sync_quota_fixture as fixture
import android_release_sync_quota_validation as validation
import android_sync_setup as setup_module
from test_android_release_sync_quota_validation import fixture as metadata_fixture


@contextmanager
def owner_scope(*, target=True):
    with tempfile.TemporaryDirectory(prefix='opennexus-quota-fixture-') as directory:
        root=Path(directory);assert root.is_absolute() and root==root.resolve()
        accounts=[dict(username='android-owned-'+kind+'-'+'a'*12,password='synthetic-'+kind+'x'*44,device_name='Owned Android '+kind) for kind in ('a','b')]
        config=dict(source=validation.SYNC,bucket='onx-android-auth-12345-1',accounts=accounts,
            environment=dict(SYNC_DATABASE_URL='postgresql+psycopg://synthetic-private',SYNC_S3_ENDPOINT='http://127.0.0.1:19072',
                SYNC_S3_BUCKET='onx-android-auth-12345-1',SYNC_STAGING_DIR=str(root/'staging')))
        state=dict(schema=1,source=validation.SYNC,fixture=fixture.FIXTURE,owner='12345-1-'+'a'*24,result='ready',
            fixture_script_sha256=hashlib.sha256(Path(fixture.__file__).read_bytes()).hexdigest(),endpoint='https://unit-owned.trycloudflare.com')
        fixture.auth.atomic_json(root/'owner-state.json',state,exclusive=True)
        fixture.auth.atomic_json(root/'device-private.json',dict(endpoint=state['endpoint'],accounts=accounts),exclusive=True)
        fixture.auth.initialize_control(root);fixture.sync.initialize_control(root)
        if target:fixture.auth.atomic_json(root/'sync-control/target.json',dict(schema=1,vault_id='1'*32),exclusive=True)
        with patch.object(fixture.auth,'require_hosted'),patch.object(fixture.auth,'owned_root',return_value=root),\
             patch.object(fixture.auth,'fixed_service',return_value=config),patch.dict(os.environ,GITHUB_RUN_ID='12345',GITHUB_RUN_ATTEMPT='1'):
            yield root,state,config


class FakePG:
    sqlite=False
    def __init__(self):
        self.engine=SimpleNamespace(dispose=Mock());self.calls=[];self.overrides={};self.reads=0
    @contextmanager
    def read_only(self):
        self.reads+=1;yield self
    def execute(self, query, parameters):
        query=str(query);self.calls.append((query,parameters))
        if query=='SELECT id FROM users WHERE username=:name':
            value='a'*32 if '-a-' in parameters['name'] else 'b'*32
        else:
            assert query.startswith('SELECT COUNT(*) FROM ') and query.endswith(' WHERE user_id=:user')
            table=query.split()[3]
            assert table in ('devices','vaults','account_policy')
            value=self.overrides.get(table,0)
        return SimpleNamespace(scalar_one=lambda:value)


@contextmanager
def pg_modules(db):
    package=ModuleType('sync_server');package.__path__=[]
    constructor=Mock(return_value=db)
    with patch.dict(sys.modules,{'sync_server':package,'sync_server.database':SimpleNamespace(Database=constructor),
                                 'sqlalchemy':SimpleNamespace(text=lambda v:v)}):
        yield constructor


def observed_provider(after=False):
    value=metadata_fixture()['provider_after' if after else 'provider_before']
    return dict(source=validation.SYNC,provider='actual PostgreSQL; repeatable-read/read-only',
        vault=dict(id='1'*32,name='Owned synthetic quota fixture',sequence=0,quota=32,used=0),
        rows=dict(value['rows'],device_progress=value['progress']),delivery=value['delivery'],
        secrets_selected=False,object_IO=False,client_received_or_applied_acknowledgement=False)


@contextmanager
def s3_modules(response=None, error=None):
    package=ModuleType('sync_server');package.__path__=[]
    response=deepcopy(response) if response is not None else dict(ResponseMetadata=dict(HTTPStatusCode=200,RetryAttempts=0),
        IsTruncated=False,Name='onx-android-auth-12345-1',Prefix='1'*32+'/',MaxKeys=1,KeyCount=0)
    client=SimpleNamespace(list_objects_v2=Mock(return_value=response,side_effect=error),close=Mock())
    constructor=Mock(return_value=SimpleNamespace(client=client))
    with patch.dict(sys.modules,{'sync_server':package,'sync_server.storage':SimpleNamespace(S3Objects=constructor)}):
        yield constructor,client,response


class SignedQuotaFixture(unittest.TestCase):
    def test_trusted_new_selector_uses_new_script_without_selecting_old_quota64(self):
        with owner_scope() as (root,_,_):
            origin=hosted.HostedOrigin(root,fixture=fixture.FIXTURE)
            self.assertEqual(origin.fixture_script(),Path(fixture.__file__).resolve())
            self.assertEqual(hosted.HostedOrigin(root,fixture='sync-quota').fixture_script().name,'android_sync_quota.py')

    def test_new_prepare_initializes_both_original_normal_controls_before_any_external_start(self):
        with tempfile.TemporaryDirectory(prefix='opennexus-quota-prepare-') as directory:
            parent=Path(directory);self.assertEqual(parent,parent.resolve());root=parent/'fresh-owner'
            origin=hosted.HostedOrigin(root,fixture=fixture.FIXTURE)
            with patch.object(hosted,'require_hosted'),patch.dict(os.environ,GITHUB_WORKSPACE=str(parent),GITHUB_RUN_ID='12345',GITHUB_RUN_ATTEMPT='1'),\
                 patch.object(hosted.subprocess,'check_output',side_effect=[validation.SYNC+'\n','']),\
                 patch.object(hosted.secrets,'token_urlsafe',side_effect=RuntimeError('controlled stop after normal controls')),\
                 patch.object(origin,'command') as command,patch.object(origin,'spawn') as spawn,patch.object(origin,'start_container') as docker:
                with self.assertRaisesRegex(RuntimeError,'^controlled stop after normal controls$'):origin.prepare()
                command.assert_not_called();spawn.assert_not_called();docker.assert_not_called()
            for path in (root/'control.json',root/'sync-control/control.json'):
                self.assertEqual(json.loads(path.read_bytes()),dict(revision=0,operation_id=None,mode='normal'))
            state=json.loads((root/'owner-state.json').read_bytes())
            self.assertEqual(state['fixture'],fixture.FIXTURE);self.assertEqual(state['result'],'preparing')
            self.assertFalse((root/'service-private.json').exists())

    def test_factory_passes_declared32_to_fixed_app_and_normal_ASGI_does_not_fabricate413(self):
        with owner_scope() as (root,_,config):
            calls=[]
            async def app(scope,receive,send):
                calls.append(scope)
                await send(dict(type='http.response.start',status=413,headers=[]))
                await send(dict(type='http.response.body',body=b'actual-controlled-response',more_body=False))
            create=Mock(return_value=app);database=Mock();objects=Mock()
            package=ModuleType('sync_server');package.__path__=[]
            with patch.dict(sys.modules,{'sync_server':package,'sync_server.__main__':SimpleNamespace(Database=database,
                    S3Objects=objects,create_app=create,operations_path=lambda:root/'operations.sqlite3')}):
                proxy=fixture.build_app(root)
            self.assertEqual(create.call_args.kwargs,dict(quota=32,operations_path=root/'operations.sqlite3',operator_user_ids=()))
            database.assert_called_once_with(config['environment']['SYNC_DATABASE_URL'])
            objects.assert_called_once_with('http://127.0.0.1:19072','onx-android-auth-12345-1')
            sent=[]
            async def send(value):sent.append(value)
            async def receive():return dict(type='http.request',body=b'',more_body=False)
            asyncio.run(proxy(dict(type='http',method='POST',path='/sync/v1/vaults/'+'1'*32+'/uploads'),receive,send))
            self.assertEqual(len(calls),1);self.assertEqual(sent[0]['status'],413)
            self.assertEqual(sent[1]['body'],b'actual-controlled-response')
            counts=json.loads((root/'sync-control/request-counts.json').read_bytes())
            self.assertEqual(counts['counts'],{'upload:413':1})
            self.assertEqual(counts['events'],[dict(action='upload',service_status=413,delivery_status=413,fault_operation=None)])

    def test_scope_mismatch_rejects_before_any_production_constructor(self):
        for target,key,change in (('state','fixture','sync-quota'),('state','source','0'*40),('state','owner','foreign'),
                                 ('state','fixture_script_sha256','0'*64),('config','bucket','onx-android-auth-999-1')):
            with self.subTest(key=key),owner_scope() as (root,state,config):
                (state if target=='state' else config)[key]=change
                if target=='state':fixture.auth.atomic_json(root/'owner-state.json',state)
                with patch.dict(sys.modules,{'sync_server.__main__':None}),self.assertRaisesRegex(RuntimeError,'^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'):
                    fixture.build_app(root)

    def test_normal_only_rejects_fault_partial_duplicate_type_or_target_before_app(self):
        cases=[('control.json',dict(revision=1,operation_id=None,mode='hold_refresh')),
               ('sync-control/control.json',dict(revision=True,operation_id=None,mode='normal')),
               ('sync-control/target.json',dict(schema=True,vault_id='1'*32)),('control.json',b'{'),
               ('control.json',b'{"revision":0,"revision":0,"operation_id":null,"mode":"normal"}')]
        for path,value in cases:
            with self.subTest(path=path),owner_scope() as (root,_,_):
                (root/path).write_bytes(value if type(value)is bytes else json.dumps(value).encode())
                app=Mock();proxy=fixture.NormalOnly(app,root)
                async def noop(*_):pass
                with self.assertRaisesRegex(RuntimeError,'^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'):
                    asyncio.run(proxy({},noop,noop))
                app.assert_not_called()

    def test_zero_device_policy_vault_proof_is_exclusive_and_existing_proof_is_not_requeried(self):
        with owner_scope(target=False) as (root,_,_),pg_modules(FakePG()) as constructor:
            db=constructor.return_value;proof=fixture.verify_prepared(root)
            self.assertEqual(db.reads,1);self.assertEqual(len(db.calls),8);db.engine.dispose.assert_called_once()
            self.assertEqual(proof['factory_default_quota'],32);self.assertFalse(proof['Android_or_Native_verified'])
            self.assertEqual([r['devices'] for r in proof['accounts']],[0,0])
            raw=(root/fixture.PROOF).read_bytes();db.overrides['devices']=1
            self.assertEqual(fixture.verify_prepared(root),proof)
            self.assertEqual((root/fixture.PROOF).read_bytes(),raw);constructor.assert_called_once()
            self.assertNotIn('synthetic-private',raw.decode());self.assertNotIn('password',raw.decode())

    def test_nonzero_unknown_or_SQLite_prepare_state_creates_no_proof(self):
        for key,value in (('devices',1),('vaults',1),('account_policy',1),('devices',True),('sqlite',True)):
            with self.subTest(key=key),owner_scope(target=False) as (root,_,_):
                db=FakePG()
                if key=='sqlite':db.sqlite=value
                else:db.overrides[key]=value
                with pg_modules(db),self.assertRaises(RuntimeError):fixture.verify_prepared(root)
                self.assertFalse((root/fixture.PROOF).exists());db.engine.dispose.assert_called_once()

    def test_partial_prepare_write_is_preserved_no_requery_and_new_Setup_requires_closed_proof(self):
        with owner_scope(target=False) as (root,state,config),pg_modules(FakePG()) as constructor:
            with self.assertRaises(RuntimeError):setup_module.Setup(root)
            self.assertFalse((root/'sync-setup').exists())
            writer=fixture.auth.atomic_json;writes=[]
            def partial(path,value,**kwargs):
                writes.append((path,kwargs));path.write_bytes(b'{"schema"');raise OSError('synthetic-private-provider')
            with patch.object(fixture.auth,'atomic_json',side_effect=partial),self.assertRaisesRegex(RuntimeError,'^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'):
                fixture.verify_prepared(root)
            self.assertEqual(len(writes),1);self.assertTrue(writes[0][1]['exclusive'])
            raw=(root/fixture.PROOF).read_bytes()
            with self.assertRaises(RuntimeError):fixture.verify_prepared(root)
            self.assertEqual((root/fixture.PROOF).read_bytes(),raw);constructor.assert_called_once()
        with owner_scope(target=False) as (root,_,_),pg_modules(FakePG()):
            fixture.verify_prepared(root);setup=setup_module.Setup(root)
            self.assertEqual(setup.owner,root)
            bad=json.loads((root/fixture.PROOF).read_bytes());bad['private_UI']='synthetic-private'
            fixture.auth.atomic_json(root/fixture.PROOF,bad)
            with self.assertRaises(RuntimeError):setup_module.Setup(root)

    def test_actual_scoped_empty_S3_list_is_independent_of_PG_and_metadata_is_detached(self):
        with owner_scope() as (root,_,_),s3_modules() as (constructor,client,_):
            scope=metadata_fixture()['scope'];base=observed_provider()
            with patch.object(fixture.sync,'observe_provider',return_value=base) as read:
                before=fixture.observe_quota_provider(root,scope)
            read.assert_called_once_with(root,'1'*32)
            client.list_objects_v2.assert_called_once_with(Bucket='onx-android-auth-12345-1',Prefix='1'*32+'/',MaxKeys=1)
            client.close.assert_called_once();constructor.assert_called_once_with('http://127.0.0.1:19072','onx-android-auth-12345-1')
            with patch.object(fixture.sync,'observe_provider',return_value=observed_provider(True)):
                after=fixture.observe_quota_provider(root,scope,before=before)
            self.assertEqual(after['delivery']['counts'],{'changes:200':1,'upload:413':1})
            self.assertTrue(after['s3']['listing_complete_reported'])
            base['delivery']['counts']['private']=9
            self.assertEqual(before['delivery']['counts'],{})
            self.assertNotIn('bucket',json.dumps(after));self.assertNotIn('synthetic-private',json.dumps(after))

    def test_bad_PG_or_target_does_not_trigger_an_independent_S3_read(self):
        for kind in ('quota64','content','provider','source','target','schema-bool'):
            with self.subTest(kind=kind),owner_scope() as (root,_,_),s3_modules() as (objects,_,_):
                value=observed_provider()
                if kind=='quota64':value['vault']['quota']=64
                if kind=='content':value['rows']['uploads']=[{}]
                if kind=='provider':value['provider']='SQLite'
                if kind=='source':value['source']='0'*40
                if kind=='target':fixture.auth.atomic_json(root/'sync-control/target.json',dict(schema=1,vault_id='9'*32))
                if kind=='schema-bool':fixture.auth.atomic_json(root/'sync-control/target.json',dict(schema=True,vault_id='1'*32))
                with patch.object(fixture.sync,'observe_provider',return_value=value),self.assertRaises(RuntimeError):fixture.observe_quota_provider(root,metadata_fixture()['scope'])
                objects.assert_not_called()

    def test_S3_partial_truncated_private_retry_type_or_SDK_error_is_unavailable_without_retry(self):
        changes=[('IsTruncated',True),('KeyCount',True),('KeyCount',1),('Contents',[{'Key':'synthetic-private'}]),
                 ('NextContinuationToken','more'),('Prefix','9'*32+'/'),('Name','foreign'),('MaxKeys',True),
                 ('private_secret','not retained'),('ResponseMetadata',dict(HTTPStatusCode=200,RetryAttempts=1))]
        for key,value in changes:
            with self.subTest(key=key),owner_scope() as (root,_,_),s3_modules() as (_,client,response):
                response[key]=value
                with patch.object(fixture.sync,'observe_provider',return_value=observed_provider()),self.assertRaisesRegex(RuntimeError,'^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'):
                    fixture.observe_quota_provider(root,metadata_fixture()['scope'])
                client.list_objects_v2.assert_called_once();client.close.assert_called_once()
        with owner_scope() as (root,_,_),s3_modules(error=OSError('synthetic-private-provider-credentials')) as (_,client,_):
            with patch.object(fixture.sync,'observe_provider',return_value=observed_provider()),self.assertRaisesRegex(RuntimeError,'^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'):
                fixture.observe_quota_provider(root,metadata_fixture()['scope'])
            client.list_objects_v2.assert_called_once();client.close.assert_called_once()


if __name__=='__main__':
    unittest.main()
