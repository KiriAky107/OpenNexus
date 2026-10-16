"""Unit-only fixture/control semantics; these are not Android or HTTPS results."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import android_auth_fixture as module


def operation():return str(uuid.uuid4())


class OwnedAuthControlTests(unittest.TestCase):
    def test_supported_manual_run_derives_an_exclusive_path_without_starting_or_creating_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            environment={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux',
                'GITHUB_REPOSITORY':'KiriAky107/OpenNexus','GITHUB_EVENT_NAME':'workflow_dispatch',
                'GITHUB_RUN_ID':'1234','GITHUB_RUN_ATTEMPT':'1','GITHUB_WORKSPACE':str(root)}
            with patch.dict(os.environ,environment,clear=True),patch.object(module.sys,'platform','linux'),patch.object(module.subprocess,'check_output') as process:
                target=module.owned_root();self.assertEqual(target,root/'.build/android-runtime/owned-auth-1234-1')
                self.assertFalse(target.exists());process.assert_not_called()

    def test_developer_self_hosted_and_other_repository_are_refused_before_provider_access(self):
        candidates=[{}, {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted','RUNNER_OS':'Linux','GITHUB_REPOSITORY':'KiriAky107/OpenNexus'},
            {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux','GITHUB_REPOSITORY':'other/repository'}]
        for environment in candidates:
            with patch.dict(os.environ,environment,clear=True),patch.object(module.sys,'platform','linux'),patch.object(module.subprocess,'check_output') as process:
                with self.assertRaisesRegex(RuntimeError,'OWNED_AUTH_HOST_REQUIRED'):module.owned_root()
                process.assert_not_called()
        with patch.object(module.sys,'platform','win32'):
            with self.assertRaisesRegex(RuntimeError,'OWNED_AUTH_HOST_REQUIRED'):module.owned_root()

    def test_original_control_receipt_replay_never_rearms_a_consumed_or_later_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);first,second=operation(),operation()
            receipt=module.arm_control(root,first,'drop_refresh',0)
            module.arm_control(root,second,'release',1)
            before=(root/'control.json').read_bytes()
            self.assertEqual(module.arm_control(root,first,'drop_refresh',0),receipt)
            self.assertEqual((root/'control.json').read_bytes(),before)
            with self.assertRaisesRegex(RuntimeError,'CONTROL_MISMATCH'):module.arm_control(root,first,'drop_login',0)
            stale=operation()
            with self.assertRaisesRegex(RuntimeError,'CONTROL_CHANGED'):module.arm_control(root,stale,'hold_login',0)
            self.assertFalse((root/'control-receipts'/(stale+'.json')).exists())

    def test_missing_receipt_is_recovered_from_the_original_applied_control_without_reapplying(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);first=operation();actual=module.atomic_json
            def fail_receipt(path,value,**kwargs):
                if path.parent.name=='control-receipts':raise OSError('owned interrupted receipt')
                return actual(path,value,**kwargs)
            with patch.object(module,'atomic_json',side_effect=fail_receipt):
                with self.assertRaises(OSError):module.arm_control(root,first,'drop_logout',0)
            original=(root/'control.json').read_bytes()
            with self.assertRaisesRegex(RuntimeError,'CONTROL_UNCERTAIN'):module.arm_control(root,operation(),'normal',1)
            recovered=module.arm_control(root,first,'drop_logout',0)
            self.assertEqual(recovered['result_revision'],1)
            self.assertEqual((root/'control.json').read_bytes(),original)

    def test_prepared_unapplied_control_requires_explicit_same_body_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);first=operation();actual=module.atomic_json
            def fail_control(path,value,**kwargs):
                if path.name=='control.json':raise OSError('owned before apply')
                return actual(path,value,**kwargs)
            with patch.object(module,'atomic_json',side_effect=fail_control):
                with self.assertRaises(OSError):module.arm_control(root,first,'hold_login',0)
            with self.assertRaisesRegex(RuntimeError,'RETRY_REQUIRES_EXPLICIT'):module.arm_control(root,first,'hold_login',0)
            self.assertEqual(module.read_json(root/'control.json')['revision'],0)
            self.assertEqual(module.arm_control(root,first,'hold_login',0,retry=True)['result_revision'],1)

    def test_symlinked_fixture_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.json';source.write_text('{}')
            try:(root/'alias.json').symlink_to(source)
            except OSError:self.skipTest('Symlink creation unavailable on this host')
            with self.assertRaisesRegex(RuntimeError,'FILE_INVALID'):module.read_json(root/'alias.json')
            with self.assertRaisesRegex(RuntimeError,'FILE_INVALID'):module.atomic_json(root/'alias.json',{})


class OwnedAuthProxyTests(unittest.IsolatedAsyncioTestCase):
    async def request(self,proxy,action='login'):
        method,path=next(route for route,name in module.ROUTES.items() if name==action)
        result=[]
        async def receive():return {'type':'http.request','body':b'{}','more_body':False}
        async def send(message):result.append(message)
        await proxy({'type':'http','method':method,'path':path},receive,send)
        return result

    def app(self,calls,status=200,body=b'{"access_token":"unit-only-private-token","refresh_token":"unit-only-refresh"}'):
        async def run(scope,receive,send):
            calls.append(scope['path'])
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':body})
        return run

    async def test_drop_occurs_after_one_original_app_response_and_is_consumed_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);op=operation();module.arm_control(root,op,'drop_login',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls),root)
            first=await self.request(proxy);second=await self.request(proxy)
            self.assertEqual([first[0]['status'],second[0]['status']],[503,200]);self.assertEqual(len(calls),2)
            self.assertNotIn(b'unit-only-private-token',first[1]['body'])
            event=module.read_json(root/'fault-events'/(op+'.json'))
            self.assertTrue(event['service_response_complete']);self.assertFalse(event['raw_body_recorded'])
            counts=module.read_json(root/'request-counts.json');self.assertEqual(counts['counts'],{'login:200':2})
            for path in root.rglob('*.json'):
                self.assertNotIn('unit-only-private-token',path.read_text());self.assertNotIn('unit-only-refresh',path.read_text())

    async def test_rejected_auth_does_not_consume_a_successful_response_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);op=operation();module.arm_control(root,op,'drop_refresh',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls,401,b'{"error":{"code":"SESSION_EXPIRED"}}'),root)
            failed=await self.request(proxy,'refresh');self.assertEqual(failed[0]['status'],401)
            self.assertFalse((root/'fault-events'/(op+'.json')).exists())
            proxy.app=self.app(calls)
            dropped=await self.request(proxy,'refresh');self.assertEqual(dropped[0]['status'],503)
            self.assertEqual(len(calls),2)

    async def test_hold_release_sends_the_original_response_without_request_replay_or_client_receipt_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);op=operation();module.arm_control(root,op,'hold_login',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls),root);task=asyncio.create_task(self.request(proxy))
            for _ in range(100):
                if (root/'fault-events'/(op+'.json')).exists():break
                await asyncio.sleep(.001)
            self.assertEqual(len(calls),1);self.assertFalse(task.done())
            module.arm_control(root,operation(),'release',1)
            result=await asyncio.wait_for(task,2)
            self.assertEqual(result[0]['status'],200);self.assertEqual(len(calls),1)
            event=module.read_json(root/'fault-events'/(op+'.released.json'))
            self.assertTrue(event['original_response_send_attempted']);self.assertFalse(event['client_receipt_known'])
            counts=module.read_json(root/'request-counts.json');self.assertIsNone(counts['events'][0]['delivery_status'])

    async def test_oversized_auth_response_is_bounded_without_recording_or_replaying_the_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module.initialize_control(root);op=operation();module.arm_control(root,op,'drop_login',0)
            calls=[];proxy=module.DeliveryProxy(self.app(calls,body=b'x'*(64*1024+1)),root)
            with self.assertRaisesRegex(RuntimeError,'RESPONSE_TOO_LARGE'):await self.request(proxy)
            self.assertEqual(len(calls),1);self.assertFalse((root/'fault-events'/(op+'.json')).exists())


if __name__=='__main__':unittest.main()
