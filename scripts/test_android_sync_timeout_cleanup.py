"""Original control writers/local ASGI, never an Android or PG/S3 run."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import android_sync_fixture as fixture
import android_sync_timeout_cleanup as cleanup
import android_sync_stop_host as held_host
from test_android_release_sync_stop_snapshot import VAULT,HOLD,RELEASE,providers


def setup(root):
    owner=root/'owned';owner.mkdir();control=fixture.initialize_control(owner);fixture.freeze_target(owner,VAULT)
    output=root/'evidence';output.mkdir();subject=cleanup.OriginalTimeoutControl(owner,output,VAULT)
    return subject,control


def failure(subject):
    held_host.record(subject.output/'timeout-component-failure.json',dict(passed=False,automatic_retry=False,whole_timeout_component_passed=False))


def retain(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


class TimeoutCleanup(unittest.TestCase):
    def test_aborted_release_intent_without_attempt_allows_one_distinct_explicit_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,root=setup(Path(folder));subject.arm();hold=subject.operation
            marker=dict(providers()[3],operation_id=hold)
            fixture.original.atomic_json(root/'fault-events'/(hold+'.json'),marker,exclusive=True)
            intended='99999999-9999-4999-8999-999999999999'
            held_host.record(subject.output/(cleanup.LABEL+'-release-control-intent.json'),dict(operation_id=intended,mode='release',
                expected_revision=1,held_operation_id=hold,automatic_retry=False,client_receipt_known=False))
            failure(subject);original=retain(subject.output);arm=fixture.arm_control
            def once(owner,operation,mode,revision):
                self.assertNotEqual(operation,intended);self.assertEqual((mode,revision),('release',1))
                self.assertTrue((subject.output/'timeout-failure-cleanup-release-control-attempt.json').is_file())
                result=arm(owner,operation,mode,revision)
                fixture.original.atomic_json(root/'fault-events'/(hold+'.released.json'),dict(providers()[5],operation_id=hold),exclusive=True)
                return result
            with patch.object(cleanup,'arm_control',side_effect=once) as call:result=subject.cleanup();call.assert_called_once()
            self.assertEqual(result['state'],'one_explicit_cleanup_release');self.assertFalse(result['whole_timeout_component_passed'])
            for name,raw in original.items():self.assertEqual((subject.output/name).read_bytes(),raw)
            self.assertFalse((root/'control-intents'/(intended+'.json')).exists())

    def test_attempt_or_internal_control_evidence_is_readonly_even_if_memory_says_not_attempted(self):
        for mode in ('attempt-only','internal-release'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,root=setup(Path(folder));subject.arm();hold=subject.operation
                held_host.record(subject.output/(cleanup.LABEL+'-release-control-intent.json'),dict(operation_id=RELEASE,mode='release',
                    expected_revision=1,held_operation_id=hold,automatic_retry=False,client_receipt_known=False))
                if mode=='attempt-only':held_host.record(subject.output/(cleanup.LABEL+'-release-control-attempt.json'),dict(operation_id=RELEASE,
                    mode='release',expected_revision=1,held_operation_id=hold,automatic_retry=False,receipt_repair_allowed=False))
                else:
                    fixture.arm_control(subject.owner,RELEASE,'release',1)
                    fixture.original.atomic_json(root/'fault-events'/(hold+'.released.json'),dict(providers()[5],operation_id=hold),exclusive=True)
                before=retain(root);failure(subject)
                with patch.object(cleanup,'arm_control') as call:result=subject.cleanup();call.assert_not_called()
                self.assertEqual(before,retain(root));self.assertFalse(result['whole_timeout_component_passed'])
                self.assertFalse((subject.output/(cleanup.LABEL+'-release-control-receipt.json')).exists())

    def test_original_hold_intent_precedes_CAS_and_reentry_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,root=setup(Path(folder));arm=fixture.arm_control
            def once(owner,operation,mode,revision):
                self.assertEqual(json.loads((subject.output/'timeout-hold-control-intent.json').read_bytes())['operation_id'],operation)
                self.assertEqual((mode,revision),('hold_revision',0));return arm(owner,operation,mode,revision)
            with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(HOLD))),patch.object(cleanup,'arm_control',side_effect=once) as call:
                receipt=subject.arm();self.assertEqual(receipt['result_revision'],1)
                before=retain(root)|retain(subject.output)
                with self.assertRaises(RuntimeError):subject.arm()
                self.assertEqual(before,retain(root)|retain(subject.output));call.assert_called_once()
            self.assertFalse(any('stop-' in p.name for p in subject.output.iterdir()))

    def test_existing_original_intent_refuses_before_control_and_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,root=setup(Path(folder));path=subject.output/'timeout-hold-control-intent.json';path.write_bytes(b'original-unknown')
            before=retain(root)
            with patch.object(cleanup,'arm_control') as arm:
                with self.assertRaises(FileExistsError):subject.arm()
                arm.assert_not_called()
            self.assertEqual(path.read_bytes(),b'original-unknown');self.assertEqual(retain(root),before)

    def test_unknown_original_hold_before_or_after_CAS_never_repaired_released_or_retried(self):
        for mode in ('before','after','missing-receipt'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,root=setup(Path(folder))
                def unknown(owner,operation,kind,revision):
                    if mode!='before':
                        fixture.arm_control(owner,operation,kind,revision)
                        if mode=='missing-receipt':(root/'control-receipts'/(operation+'.json')).unlink()
                    raise TimeoutError('private original result not available')
                with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(HOLD))),patch.object(cleanup,'arm_control',side_effect=unknown) as call:
                    with self.assertRaises(TimeoutError):subject.arm()
                    failure(subject);before=retain(root)
                    with patch.object(held_host,'arm_control') as release:
                        result=subject.cleanup();release.assert_not_called()
                    self.assertEqual(result['state'],'original_hold_result_unconfirmed');call.assert_called_once()
                    self.assertEqual(before,retain(root));self.assertFalse((subject.output/'timeout-hold-control-receipt.json').exists())
                    with self.assertRaises(RuntimeError):subject.cleanup()
                    with self.assertRaises(RuntimeError):subject.arm()

    def test_unknown_original_release_is_only_read_reconciled_including_missing_receipt(self):
        for mode in ('before','after','missing-receipt'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,root=setup(Path(folder))
                with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(HOLD))):subject.arm()
                marker=providers()[3];fixture.original.atomic_json(root/'fault-events'/(HOLD+'.json'),marker,exclusive=True)
                subject.held.read_held();released=providers()[5]
                def unknown(owner,operation,kind,revision):
                    if mode!='before':
                        fixture.arm_control(owner,operation,kind,revision)
                        fixture.original.atomic_json(root/'fault-events'/(HOLD+'.released.json'),released,exclusive=True)
                        if mode=='missing-receipt':(root/'control-receipts'/(operation+'.json')).unlink()
                    raise TimeoutError('private control response')
                with patch.object(held_host,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(RELEASE))),patch.object(held_host,'arm_control',side_effect=unknown) as call:
                    with self.assertRaises(TimeoutError):subject.held.begin_release(subject.output,cleanup.LABEL)
                    before=retain(root);failure(subject);result=subject.cleanup();call.assert_called_once()
                self.assertEqual(before,retain(root));self.assertFalse(result['whole_timeout_component_passed'])
                self.assertEqual(result['original_response_release_observed'],mode!='before')
                self.assertFalse((subject.output/(cleanup.LABEL+'-release-control-receipt.json')).exists())
                if mode=='missing-receipt':self.assertFalse((root/'control-receipts'/(RELEASE+'.json')).exists())

    def test_unknown_cleanup_release_is_only_read_reconciled_and_never_reentered(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,root=setup(Path(folder))
            with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(HOLD))):subject.arm()
            fixture.original.atomic_json(root/'fault-events'/(HOLD+'.json'),providers()[3],exclusive=True);failure(subject)
            def unknown(owner,operation,kind,revision):
                fixture.arm_control(owner,operation,kind,revision)
                fixture.original.atomic_json(root/'fault-events'/(HOLD+'.released.json'),providers()[5],exclusive=True)
                raise TimeoutError('private cleanup response')
            with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(RELEASE))),patch.object(cleanup,'arm_control',side_effect=unknown) as call:
                result=subject.cleanup();call.assert_called_once()
                before=retain(root)|retain(subject.output)
                with self.assertRaises(RuntimeError):subject.cleanup()
                self.assertEqual(before,retain(root)|retain(subject.output))
            self.assertEqual(result['state'],'cleanup_release_readonly_reconciled');self.assertFalse(result['whole_timeout_component_passed'])

    def test_foreign_owner_vault_alias_missing_failure_or_marker_refuses_cleanup_without_writes(self):
        for mode in ('alias','vault','no-failure','foreign-control','missing-marker','stop-failure-only'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                subject,root=setup(Path(folder))
                if mode=='alias':
                    with self.assertRaises(RuntimeError):cleanup.OriginalTimeoutControl(subject.owner/'..'/'owned',subject.output,VAULT)
                    continue
                with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(HOLD))):subject.arm()
                if mode=='stop-failure-only':held_host.record(subject.output/'stop-component-failure.json',dict(passed=False,automatic_retry=False))
                elif mode!='no-failure':failure(subject)
                if mode=='vault':fixture.original.atomic_json(root/'target.json',dict(schema=1,vault_id='f'*32))
                if mode=='foreign-control':fixture.original.atomic_json(root/'control.json',dict(revision=2,operation_id=RELEASE,mode='release'))
                before=retain(root)
                with patch.object(held_host,'arm_control') as arm,patch.object(cleanup,'wait_marker',side_effect=TimeoutError):
                    with self.assertRaises((RuntimeError,TimeoutError)):subject.cleanup()
                    arm.assert_not_called()
                self.assertEqual(before,retain(root))

    def test_marker_reader_post_observation_deadline_and_backward_clock_are_rejected(self):
        for stamps in ([0,0,2.001],[1,1,.9]):
            with self.subTest(stamps=stamps),patch.object(cleanup.time,'monotonic',side_effect=stamps):
                with self.assertRaises(RuntimeError):cleanup.wait_marker(lambda:dict(observed=True))


class TimeoutCleanupProtocol(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,fail_send):
        with tempfile.TemporaryDirectory() as folder:
            subject,root=setup(Path(folder));calls=[];sent=[];private=b'private original response'
            with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(HOLD))):subject.arm()
            async def app(scope,receive,send):
                calls.append(scope['path']);await receive()
                await send(dict(type='http.response.start',status=200,headers=[]))
                await send(dict(type='http.response.body',body=private))
            async def receive():return dict(type='http.request',body=b'private original request',more_body=False)
            async def send(message):
                if fail_send:raise RuntimeError('private disconnected ASGI transport')
                sent.append(message)
            pending=asyncio.create_task(fixture.TargetedDeliveryProxy(app,subject.owner)(dict(type='http',method='POST',
                path='/sync/v1/vaults/'+VAULT+'/revisions',query_string=b''),receive,send))
            try:
                async with asyncio.timeout(3):
                    while subject.held.read_held() is None:await asyncio.sleep(0)
                self.assertFalse(pending.done());failure(subject)
                # Cleanup uses synchronous bounded reads while the original ASGI
                # response remains on this loop; run it on one helper thread.
                with patch.object(cleanup,'uuid',SimpleNamespace(uuid4=lambda:uuid.UUID(RELEASE))):result=await asyncio.to_thread(subject.cleanup)
                if fail_send:
                    with self.assertRaises(RuntimeError):await pending
                else:await pending
                self.assertEqual(len(calls),1);self.assertEqual(len(sent),0 if fail_send else 2)
                self.assertEqual(result['state'],'one_explicit_cleanup_release');self.assertFalse(result['whole_timeout_component_passed'])
                self.assertFalse(result['client_receipt_known'])
                marker=json.loads((subject.output/'timeout-failure-cleanup-released-marker.json').read_bytes())
                self.assertEqual(marker['ASGI_send_returned'],not fail_send);self.assertFalse(marker['client_receipt_known'])
                self.assertEqual(fixture.original.read_json(root/'request-counts.json')['counts'],{'revision:200':1})
                self.assertEqual(fixture.original.read_json(root/'control.json')['revision'],2)
                self.assertFalse(any(private in p.read_bytes() or b'private original request' in p.read_bytes() for p in subject.output.rglob('*') if p.is_file()))
            finally:
                if not pending.done():
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):await pending

    async def test_actual_local_service200_hold_then_one_cleanup_release_remains_wholefalse(self):await self.exercise(False)
    async def test_failed_original_ASGI_send_is_not_client_receipt_or_cleanup_success(self):await self.exercise(True)


if __name__=='__main__':unittest.main()
