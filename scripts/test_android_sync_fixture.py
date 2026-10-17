"""Unit-only original-delivery controls. No Android, HTTPS or provider claim."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import uuid
import android_auth_fixture as common
import android_sync_fixture as module


class OriginalSyncDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, proxy, *, method='POST', path='/sync/v1/vaults/owned/revisions', query=b''):
        sent=[]
        async def receive():return {'type':'http.request','body':b'{"original":"unit-only"}','more_body':False}
        async def send(message):sent.append(message)
        await proxy({'type':'http','method':method,'path':path,'query_string':query},receive,send)
        return sent

    def app(self, calls, *, status=201, body=b'{"server_result":"unit-only-private-value"}'):
        async def run(scope,receive,send):
            request=await receive();calls.append((dict(scope),request['body']))
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':body})
        return run

    async def test_original_commit_response_can_be_dropped_once_without_replaying_or_recording_body(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory);root=module.initialize_control(owner);op=str(uuid.uuid4())
            module.arm_control(owner,op,'drop_revision',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls),owner,'owned')
            first=await self.request(proxy);second=await self.request(proxy)
            self.assertEqual([first[0]['status'],second[0]['status']],[503,201])
            self.assertEqual(len(calls),2)  # Two explicit test requests, no proxy retry.
            self.assertEqual(calls[0][1],b'{"original":"unit-only"}')
            self.assertNotIn(b'unit-only-private-value',first[1]['body'])
            marker=common.read_json(root/'fault-events'/(op+'.json'))
            self.assertTrue(marker['service_response_complete']);self.assertFalse(marker['raw_body_recorded'])
            self.assertEqual(common.read_json(root/'request-counts.json')['counts'],{'revision:201':2})
            self.assertFalse(any(b'unit-only-private-value' in path.read_bytes() for path in root.rglob('*.json')))

    async def test_only_exact_owned_routes_are_controlled_and_original_cursor_is_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory);root=module.initialize_control(owner)
            module.arm_control(owner,str(uuid.uuid4()),'drop_revision',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls,status=200),owner,'owned')
            for method,path in [('POST','/sync/v1/vaults/other/revisions'),('GET','/sync/v1/vaults/owned/revisions'),('POST','/sync/v1/auth/sessions')]:
                value=await self.request(proxy,method=method,path=path)
                self.assertEqual(value[0]['status'],200)
            self.assertEqual(list((root/'fault-events').iterdir()),[])
            value=await self.request(proxy,method='GET',path='/sync/v1/vaults/owned/changes',query=b'cursor=7&limit=100')
            self.assertEqual(value[0]['status'],200)
            self.assertEqual(calls[-1][0]['query_string'],b'cursor=7&limit=100')
            self.assertEqual(common.read_json(root/'request-counts.json')['counts'],{'changes:200':1})

    async def test_rejected_commit_is_not_a_lost_success_and_does_not_consume_original_control(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory);root=module.initialize_control(owner);op=str(uuid.uuid4())
            module.arm_control(owner,op,'drop_revision',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls,status=409),owner,'owned')
            value=await self.request(proxy);self.assertEqual(value[0]['status'],409)
            self.assertFalse((root/'fault-events'/(op+'.json')).exists())
            self.assertEqual(common.read_json(root/'request-counts.json')['events'][0]['delivery_status'],409)
            self.assertEqual(len(calls),1)

    async def test_original_control_receipt_cannot_rearm_an_already_consumed_commit_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory);root=module.initialize_control(owner);op=str(uuid.uuid4())
            receipt=module.arm_control(owner,op,'drop_revision',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls),owner,'owned')
            await self.request(proxy)
            release=str(uuid.uuid4());module.arm_control(owner,release,'release',1)
            original=(root/'control.json').read_bytes()
            self.assertEqual(module.arm_control(owner,op,'drop_revision',0),receipt)
            self.assertEqual((root/'control.json').read_bytes(),original)
            value=await self.request(proxy);self.assertEqual(value[0]['status'],201)
            self.assertEqual(len(calls),2)

    async def test_generated_changes_can_be_held_without_claiming_client_receipt_or_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory);root=module.initialize_control(owner);op=str(uuid.uuid4())
            module.arm_control(owner,op,'hold_changes',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls,status=200,body=b'{"items":[],"cursor":7}'),owner,'owned')
            pending=asyncio.create_task(self.request(proxy,method='GET',path='/sync/v1/vaults/owned/changes',query=b'cursor=6'))
            marker=root/'fault-events'/(op+'.json')
            async with asyncio.timeout(3):
                while not marker.exists():await asyncio.sleep(0)
            self.assertFalse(pending.done());self.assertEqual(len(calls),1)
            module.arm_control(owner,str(uuid.uuid4()),'release',1)
            value=await asyncio.wait_for(pending,3)
            self.assertEqual(value[0]['status'],200);self.assertEqual(value[1]['body'],b'{"items":[],"cursor":7}')
            released=common.read_json(root/'fault-events'/(op+'.released.json'))
            self.assertTrue(released['ASGI_send_returned']);self.assertFalse(released['client_receipt_known'])
            self.assertEqual(calls[0][0]['query_string'],b'cursor=6')

    async def test_invalid_targets_modes_response_bounds_and_repeated_setup_fail_before_app_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory);module.initialize_control(owner);calls=[]
            for vault in ['../outside','owned/other','', 'x'*81]:
                with self.assertRaisesRegex(RuntimeError,'TARGET_INVALID'):
                    module.DeliveryProxy(self.app(calls),owner,vault)
            with self.assertRaisesRegex(RuntimeError,'CONTROL_INVALID'):
                module.arm_control(owner,str(uuid.uuid4()),'drop_login',0)
            with self.assertRaisesRegex(RuntimeError,'ALREADY_PREPARED'):module.initialize_control(owner)
            for bound in [True,0,65535,2*1024*1024+1]:
                with self.assertRaisesRegex(RuntimeError,'PROXY_INVALID'):
                    common.DeliveryProxy(self.app(calls),owner,response_limit=bound)
            self.assertEqual(calls,[])
