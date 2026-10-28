"""Exact completed timeout driver and real stop recorder, controlled device only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
from subprocess import CompletedProcess,TimeoutExpired
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import android_release_sync_timeout_lifecycle as policy
from android_sync_timeout_cleanup import LABEL
from test_android_sync_timeout_host import Harness,host
from test_android_release_observation import Device


class SnapshotStop:
    def __init__(self,root,mode='normal',phase=host.RESUME):
        self.h=Harness(root,phase);self.h.execute();self.subject=s=self.h.subject
        self.mode=mode;self.commands=[];self.device=Device();self.device.rooted=True
        self.device.identity=deepcopy(s.identity);self.device.active=True;self.device.stopped=False
        self.label=LABEL if phase==host.TIMEOUT else 'timeout-original-reconciled'
        self.prefix=self.label+'-snapshot-stop'
        for path in list(s.output.glob('original-*')):
            path.rename(path.with_name(path.name.replace('original-',self.label+'-',1)))
        s.phases[-1]['label']=self.label;s.timeout_originals_preserved=True;s.timeout_read_verified=True
        s.timeout_stage='timed-out-snapshot-stop' if phase==host.TIMEOUT else 'reconciled-snapshot-stop'
        s.timeout_control=SimpleNamespace(held=self.h.held,operation=self.h.held.control['operation_id'])
        s.device=self.call

    def call(self,*args,**kwargs):
        tokens=shlex.split(args[2]) if args[:2]==('shell','-T') else []
        if tokens==['am','force-stop','--user','0',host.APP]:
            assert kwargs['timeout']==2 and self.h.process.poll()==0
            assert (self.subject.output/(self.prefix+'-intent.json')).is_file()
            self.commands.append(args)
            if self.mode=='unknown':raise TimeoutExpired(args,2,stderr=b'private-snapshot-stop-sentinel')
            self.h.gone=True;self.device.active=self.mode=='still-active';self.device.stopped=self.mode!='not-stopped'
            if self.mode=='root-changed':self.device.identity['boot_id']='30000000-0000-4000-8000-000000000003'
            return CompletedProcess(args,False if self.mode=='boolean-code' else 0,b'private-snapshot-stop-sentinel' if self.mode=='stream' else b'',b'')
        return self.device(*args,**kwargs)

    def execute(self,*,guarded=False):
        with patch.dict(os.environ,dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1')), \
             patch('android_runtime_probe.sys.platform','linux'), \
             patch.object(host.ReleaseAuthBackground,'app_is_resumed',side_effect=lambda subject:not self.h.gone):
            action=lambda:policy.stop_completed_phase(self.subject)
            return policy.run_guarded(self.subject,action) if guarded else action()


class CompletedTimeoutStop(unittest.TestCase):
    def test_both_exact_completed_phases_use_once_stop_and_stopped_observer(self):
        for phase in (host.TIMEOUT,host.RESUME):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as folder:
                h=SnapshotStop(folder,phase=phase);h.execute();self.assertEqual(len(h.commands),1)
                value=json.loads((h.subject.output/(h.prefix+'-confirmed.json')).read_bytes())
                self.assertTrue(value['user0_force_stopped']);self.assertFalse(value['native_apply_ACK'])
                self.assertTrue(value['snapshot_stop_not_transport_failure']);h.subject.stop.assert_called_once()
                with self.assertRaises(FileExistsError):h.execute()
                self.assertEqual(len(h.commands),1)

    def test_unknown_driver_wrong_phase_missing_originals_or_read_gate_never_stops(self):
        for mode in ('unknown','boolean-code','bad-stream','wrong-phase','wrong-entry','raw-not-saved','read-not-checked','wrong-stage','bad-held-clock'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                h=SnapshotStop(folder,phase=host.TIMEOUT if mode=='bad-held-clock' else host.RESUME);s=h.subject
                path=s.output/(h.label+'-instrument-result.json');result=json.loads(path.read_bytes())
                if mode=='unknown':result['unknown_result']=True;path.write_text(json.dumps(result),'utf-8')
                elif mode=='boolean-code':result['returncode']=False;path.write_text(json.dumps(result),'utf-8')
                elif mode=='bad-stream':(s.output/(h.label+'-instrument.stdout')).write_bytes(b'private-not-approved')
                elif mode=='wrong-phase':s.phases[-1]['phase']='cycle-after-process-loss-original'
                elif mode=='wrong-entry':
                    path=s.output/(h.label+'-host-phase-intent.json');value=json.loads(path.read_bytes());value['entry']='ReleaseSyncProcessRuntimeProbe';path.write_text(json.dumps(value),'utf-8')
                elif mode=='raw-not-saved':s.timeout_originals_preserved=False
                elif mode=='read-not-checked':s.timeout_read_verified=False
                elif mode=='bad-held-clock':
                    path=s.output/(h.label+'-failure-clock.json');value=json.loads(path.read_bytes());value['sample_start_monotonic_ms']=0;path.write_text(json.dumps(value),'utf-8')
                else:s.timeout_stage='original-timeout-GUI'
                with self.assertRaises(RuntimeError):h.execute()
                self.assertFalse(h.commands);self.assertFalse((s.output/(h.prefix+'-intent.json')).exists())

    def test_owner_changes_before_after_public_or_after_real_private_intent_refuse_actual_command(self):
        for when in ('before','after-public','after-private'):
            with self.subTest(when=when),tempfile.TemporaryDirectory() as folder:
                h=SnapshotStop(folder);s=h.subject
                def identity():
                    changed=when=='before' or (when=='after-public' and (s.output/(h.prefix+'-intent.json')).exists())
                    if when=='after-private':changed=(s.tools/h.prefix/'once-snapshot-stop-intent.json').exists()
                    return dict(s.identity,boot_id='30000000-0000-4000-8000-000000000003') if changed else s.identity
                s.observer.observe_identity.side_effect=identity
                with self.assertRaisesRegex(RuntimeError,'OWNER_CHANGED'):h.execute()
                self.assertFalse(h.commands)

    def test_unknown_stop_keeps_private_stream_false_and_never_automatic_second_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            h=SnapshotStop(folder,'unknown')
            with self.assertRaises(TimeoutExpired):h.execute(guarded=True)
            s=h.subject;self.assertEqual(len(h.commands),1)
            result=json.loads((s.output/(h.prefix+'-result.json')).read_bytes());self.assertTrue(result['unknown_result'])
            self.assertFalse(json.loads((s.output/'timeout-component-failure.json').read_bytes())['passed'])
            self.assertTrue((s.output/'timeout-failure-original-unavailable.json').is_file())
            self.assertEqual((s.tools/h.prefix/'once-snapshot-stop.stderr').read_bytes(),b'private-snapshot-stop-sentinel')
            self.assertNotIn(b'private-snapshot-stop-sentinel',b''.join(p.read_bytes() for p in s.output.rglob('*') if p.is_file()))
            with self.assertRaises(FileExistsError):h.execute()
            self.assertEqual(len(h.commands),1)

    def test_existing_snapshot_intent_preserved_and_never_replaced_or_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            h=SnapshotStop(folder);path=h.subject.output/(h.prefix+'-intent.json');path.write_bytes(b'original-unknown')
            with self.assertRaises(FileExistsError):h.execute()
            self.assertFalse(h.commands);self.assertEqual(path.read_bytes(),b'original-unknown')

    def test_unapproved_command_or_absence_owner_failure_never_returns_success(self):
        for mode in ('boolean-code','stream','still-active','not-stopped','root-changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                h=SnapshotStop(folder,mode)
                with self.assertRaises(RuntimeError):h.execute()
                self.assertEqual(len(h.commands),1);self.assertFalse((h.subject.output/(h.prefix+'-confirmed.json')).exists())
                self.assertNotIn(b'private-snapshot-stop-sentinel',b''.join(p.read_bytes() for p in h.subject.output.rglob('*') if p.is_file()))


if __name__=='__main__':unittest.main()
