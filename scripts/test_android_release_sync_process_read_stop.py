"""Actual completed-read host/recorders and stopped observer; controlled device."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
from subprocess import CompletedProcess,TimeoutExpired
import tempfile
import unittest
from unittest.mock import patch

import android_release_sync_process_lifecycle as policy
from test_android_sync_process_host import Harness,host
from test_android_release_observation import Device


class ReadStop:
    def __init__(self,root,mode='normal'):
        self.h=Harness(root,host.RESUME);self.h.execute();self.subject=s=self.h.subject
        self.mode=mode;self.commands=[];self.device=Device();self.device.rooted=True
        self.device.identity=deepcopy(s.identity);self.device.active=True;self.device.stopped=False
        for path in list(s.output.glob('original-*')):
            path.rename(path.with_name(path.name.replace('original-','process-original-reconciled-',1)))
        s.phases[-1]['label']='process-original-reconciled';s.process_raw_killed_preserved=True
        s.process_stage='explicit-original-changes-reconcile';s.process_control=None
        s.device=self.call

    def call(self,*args,**kwargs):
        tokens=shlex.split(args[2]) if args[:2]==('shell','-T') else []
        if tokens==['am','force-stop','--user','0',host.APP]:
            assert kwargs['timeout']==2 and self.h.process.poll()==0
            assert (self.subject.output/'process-completed-read-snapshot-stop-intent.json').is_file()
            self.commands.append(args)
            if self.mode=='unknown':raise TimeoutExpired(args,2,stderr=b'private-snapshot-stop-sentinel')
            self.h.gone=True
            self.device.active=self.mode=='still-active';self.device.stopped=self.mode!='not-stopped'
            if self.mode=='root-changed':self.device.identity['boot_id']='30000000-0000-4000-8000-000000000003'
            return CompletedProcess(args,False if self.mode=='boolean-code' else 0,b'private-snapshot-stop-sentinel' if self.mode=='stream' else b'',b'')
        return self.device(*args,**kwargs)

    def execute(self,*,guarded=False):
        with patch.dict(os.environ,dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1')), \
             patch('android_runtime_probe.sys.platform','linux'), \
             patch.object(host.ReleaseAuthBackground,'app_is_resumed',side_effect=lambda subject:not self.h.gone):
            action=lambda:policy.stop_completed_read(self.subject)
            return policy.run_guarded(self.subject,action) if guarded else action()


class CompletedReadStop(unittest.TestCase):
    def test_actual_completed_host_then_one_explicit_snapshot_stop_and_stopped_observer(self):
        with tempfile.TemporaryDirectory() as folder:
            h=ReadStop(folder);h.execute();self.assertEqual(len(h.commands),1)
            value=json.loads((h.subject.output/'process-completed-read-snapshot-stop-confirmed.json').read_bytes())
            self.assertTrue(value['user0_force_stopped']);self.assertTrue(value['snapshot_stop_is_not_tested_process_loss'])
            self.assertFalse(value['native_apply_ACK']);self.assertFalse(h.device.active)
            h.subject.stop.assert_called_once();self.assertEqual(h.h.events,['launch','tap','input-finish'])
            original=(h.subject.output/'process-completed-read-snapshot-stop-intent.json').read_bytes()
            with self.assertRaises(FileExistsError):h.execute()
            self.assertEqual(len(h.commands),1);self.assertEqual((h.subject.output/'process-completed-read-snapshot-stop-intent.json').read_bytes(),original)

    def test_unknown_or_unapproved_completed_driver_cannot_authorize_snapshot_stop(self):
        for mode in ('unknown','boolean-code','bad-stream','wrong-phase','raw-not-saved','wrong-stage'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                h=ReadStop(folder);s=h.subject
                path=s.output/'process-original-reconciled-instrument-result.json'
                result=json.loads(path.read_bytes())
                if mode=='unknown':result['unknown_result']=True;path.write_text(json.dumps(result),'utf-8')
                elif mode=='boolean-code':result['returncode']=False;path.write_text(json.dumps(result),'utf-8')
                elif mode=='bad-stream':(s.output/'process-original-reconciled-instrument.stdout').write_bytes(b'private-not-approved')
                elif mode=='wrong-phase':s.phases[-1]['phase']=host.LOSS
                elif mode=='raw-not-saved':s.process_raw_killed_preserved=False
                else:s.process_stage='killed-original-projection'
                with self.assertRaises(RuntimeError):h.execute()
                self.assertFalse(h.commands);self.assertFalse((s.output/'process-completed-read-snapshot-stop-intent.json').exists())

    def test_changed_owner_before_or_after_exclusive_intent_never_issues_command(self):
        for after in (False,True):
            with self.subTest(after=after),tempfile.TemporaryDirectory() as folder:
                h=ReadStop(folder);s=h.subject
                def identity():
                    changed=not after or (s.output/'process-completed-read-snapshot-stop-intent.json').exists()
                    return dict(s.identity,boot_id='30000000-0000-4000-8000-000000000003') if changed else s.identity
                s.observer.observe_identity.side_effect=identity
                with self.assertRaisesRegex(RuntimeError,'OWNER_CHANGED'):h.execute()
                self.assertFalse(h.commands)

    def test_unknown_snapshot_stop_preserves_private_result_and_wholefalse_without_another_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            h=ReadStop(folder,'unknown')
            with self.assertRaises(TimeoutExpired):h.execute(guarded=True)
            self.assertEqual(len(h.commands),1)
            s=h.subject;result=json.loads((s.output/'process-completed-read-snapshot-stop-result.json').read_bytes())
            self.assertTrue(result['unknown_result']);self.assertIsNone(result['returncode'])
            self.assertFalse(json.loads((s.output/'process-component-failure.json').read_bytes())['passed'])
            self.assertFalse(json.loads((s.output/'process-failure-original-unavailable.json').read_bytes())['product_stop_or_reopen_issued'])
            self.assertEqual((s.tools/'process-completed-read-snapshot-stop/once-snapshot-stop.stderr').read_bytes(),b'private-snapshot-stop-sentinel')
            self.assertNotIn(b'private-snapshot-stop-sentinel',b''.join(p.read_bytes() for p in s.output.rglob('*') if p.is_file()))
            with self.assertRaises(FileExistsError):h.execute()
            self.assertEqual(len(h.commands),1)

    def test_existing_intent_is_preserved_before_command_or_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            h=ReadStop(folder);path=h.subject.output/'process-completed-read-snapshot-stop-intent.json';path.write_bytes(b'original unknown')
            with self.assertRaises(FileExistsError):h.execute()
            self.assertFalse(h.commands);self.assertEqual(path.read_bytes(),b'original unknown')

    def test_unapproved_stream_or_unconfirmed_stopped_state_is_never_success_or_retried(self):
        for mode in ('boolean-code','stream','still-active','not-stopped','root-changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                h=ReadStop(folder,mode)
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(len(h.commands),1);self.assertFalse((h.subject.output/'process-completed-read-snapshot-stop-confirmed.json').exists())
                self.assertNotIn(b'private-snapshot-stop-sentinel',b''.join(p.read_bytes() for p in h.subject.output.rglob('*') if p.is_file()))


if __name__=='__main__':unittest.main()
