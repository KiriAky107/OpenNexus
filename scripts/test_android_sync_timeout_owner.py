"""Actual original owner code and CAS, fixed identity replies and time are fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_sync_timeout_owner as owner
import android_sync_timeout_host as host
from android_auth_fixture import atomic_json,read_json
from android_sync_process_host import _owner as legacy_owner
from test_android_sync_timeout_host import Harness
from test_android_sync_process_host import Process,C,VAULT


class TimeoutReleaseOwner(unittest.TestCase):
    def environment(self):
        return patch.dict(owner.os.environ,dict(GITHUB_RUN_ID='17',GITHUB_RUN_ATTEMPT='1'))

    def test_exact_two_commands_same_identity_and_no_shared_observer_mutation(self):
        with tempfile.TemporaryDirectory() as folder,self.environment(),patch.object(owner,'require_hosted_emulator'):
            h=Harness(folder);commands=[];device=h.subject.device;shell=h.subject.shell;observer=h.subject.observer
            def command(*args,**kwargs):commands.append((args,kwargs));return device(*args,**kwargs)
            def query(*args,**kwargs):commands.append((args,kwargs));return shell(*args,**kwargs)
            h.subject.device=command;h.subject.shell=query
            self.assertEqual(owner.read_owned_identity(h.subject),h.subject.identity)
            self.assertEqual(commands,[(('emu','avd','name'),dict(check=False,timeout=1)),(('sh','-c',owner.PROPERTIES),dict(check=False,timeout=1))])
            self.assertEqual(owner.PROPERTIES,'getprop ro.kernel.qemu && getprop ro.build.version.sdk && getprop ro.build.type && getprop ro.debuggable && cat /proc/sys/kernel/random/boot_id')
            self.assertIs(h.subject.observer,observer);observer.observe_identity.assert_not_called()

    def test_unknown_extra_nonascii_or_wrong_owned_identity_refused_without_retry(self):
        for variant in ('avd-extra','avd-wrong','API','qemu','build','debug','boot','extra','empty','stderr','bool','nonascii'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder,self.environment(),patch.object(owner,'require_hosted_emulator'):
                h=Harness(folder);normal_device=h.subject.device;normal_shell=h.subject.shell;calls=[]
                def device(*args,**kwargs):
                    calls.append(args);value=normal_device(*args,**kwargs)
                    if variant=='avd-extra':value.stdout+=b'extra\n'
                    if variant=='avd-wrong':value.stdout=b'foreign\nOK\n'
                    return value
                def shell(*args,**kwargs):
                    calls.append(args);value=normal_shell(*args,**kwargs);fields=value.stdout.splitlines()
                    index={'qemu':0,'API':1,'build':2,'debug':3,'boot':4}.get(variant)
                    if index is not None:fields[index]=b'foreign';value.stdout=b'\n'.join(fields)+b'\n'
                    if variant=='extra':value.stdout+=b'private\n'
                    if variant=='empty':value.stdout=b''
                    if variant=='stderr':value.stderr=b'private error'
                    if variant=='bool':value.returncode=False
                    if variant=='nonascii':value.stdout=b'\xff\n'
                    return value
                h.subject.device=device;h.subject.shell=shell
                with self.assertRaises((RuntimeError,UnicodeDecodeError)):owner.read_owned_identity(h.subject)
                self.assertEqual(len(calls),2);self.assertFalse(h.events)

    def test_two_read_clock_total_stays_one_second_no_deadline_extension(self):
        for stamps in ([0,1.001],[1,.99]):
            with self.subTest(stamps=stamps),tempfile.TemporaryDirectory() as folder,self.environment(),patch.object(owner,'require_hosted_emulator'),patch.object(owner.time,'monotonic',side_effect=stamps):
                h=Harness(folder)
                with self.assertRaisesRegex(RuntimeError,'SYNC_TIMEOUT_RELEASE_IDENTITY_BOUND'):owner.read_owned_identity(h.subject)
                self.assertFalse(h.events)

    def test_original_root_PID_UID_cmdline_start_and_foreground_guards_remain(self):
        for variant in ('root','pid','UID','cmdline','start','boot','driver','foreground','entry'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder,self.environment(),patch.object(owner,'require_hosted_emulator'):
                h=Harness(folder);lifetime=dict(package=host.APP,pid=1234,uid=12345,start_ticks=4567)
                h.process=SimpleNamespace(poll=lambda:0 if variant=='driver' else None)
                original=h.subject.shell
                def shell(*args,**kwargs):
                    value=original(*args,**kwargs)
                    if variant=='root' and args==('id','-u'):value.stdout=b'2000\n'
                    if variant=='pid' and args==('pidof',host.APP):value.stdout=b'4321\n'
                    if variant=='UID' and args==('pm','list','packages','-U',host.APP):value.stdout=b'package:cc.kronecker.opennexus uid:12349\n'
                    if variant=='cmdline' and args==('cat','/proc/1234/cmdline'):value.stdout=b'cc.foreign\0'
                    if variant=='start' and args==('cat','/proc/1234/stat'):value.stdout=value.stdout.replace(b'4567',b'9876')
                    if variant=='boot' and args==('sh','-c',owner.PROPERTIES):value.stdout=value.stdout.replace(h.subject.identity['boot_id'].encode(),C.encode())
                    return value
                h.subject.shell=shell
                if variant=='entry':h.subject.driver_entry='ReleaseSyncProcessRuntimeProbe'
                observer=h.subject.observer;identity=deepcopy(h.subject.identity)
                with patch.object(host.ReleaseAuthBackground,'app_is_resumed',return_value=variant!='foreground'),self.assertRaises(RuntimeError):
                    owner.release_owner(h.subject,h.process,b'1234',12345,lifetime)
                self.assertIs(h.subject.observer,observer);self.assertEqual(h.subject.identity,identity);self.assertFalse(h.events)

    def test_original_4264ms_window_fixture_legacy_identity_overhead_refuses_batched_identity_releases_once(self):
        # 4264ms is only the retained sample-start window. Per-query cost below
        # is controlled; it is not a measurement of the original Android ADB.
        outcomes=[]
        for batched in (False,True):
            with tempfile.TemporaryDirectory() as folder,self.environment(),patch.object(owner,'require_hosted_emulator'):
                h=Harness(folder);h.failed_ready=True;h.now=100.;lifetime=dict(package=host.APP,pid=1234,uid=12345,start_ticks=4567)
                stream=(h.subject.tools/'fixture-stream').open('wb');h.process=Process(stream)
                marker=dict(operation_id=C,mode='hold_revision',revision=1,action='revision',service_status=200,service_response_complete=True,raw_body_recorded=False)
                atomic_json(h.control_root/'fault-events'/(C+'.json'),marker,exclusive=True);h.held.read_held()
                shell=h.subject.shell;device=h.subject.device
                def query(*args,**kwargs):h.now+=.04;return shell(*args,**kwargs)
                def command(*args,**kwargs):h.now+=.04;return device(*args,**kwargs)
                def old_identity():h.now+=.4;return h.subject.identity
                h.subject.shell=query;h.subject.device=command;h.subject.observer.observe_identity=Mock(side_effect=old_identity)
                try:
                    with patch.object(host.time,'monotonic',side_effect=lambda:h.now),patch.object(host,'arm_control',side_effect=h.release), \
                         patch.object(host.ReleaseAuthBackground,'app_is_resumed',return_value=True), \
                         patch.object(host,'_release_owner',side_effect=owner.release_owner if batched else legacy_owner):
                        if batched:host._release(h.subject,h.process,b'1234',12345,lifetime,h.held,'original',104.264)
                        else:
                            with self.assertRaisesRegex(RuntimeError,'SYNC_TIMEOUT_RELEASE_INTENT_FINISHED_TOO_LATE'):
                                host._release(h.subject,h.process,b'1234',12345,lifetime,h.held,'original',104.264)
                    outcomes.append((batched,list(h.events),h.held.release_attempted))
                    if batched:
                        self.assertEqual(read_json(h.control_root/'control.json')['revision'],2)
                        self.assertTrue((h.subject.output/'original-release-control-attempt.json').is_file())
                        self.assertLessEqual(h.now+3,104.264)
                    else:
                        self.assertEqual(read_json(h.control_root/'control.json')['revision'],1)
                        self.assertFalse((h.subject.output/'original-release-control-attempt.json').exists())
                finally:stream.close()
        self.assertEqual(outcomes,[(False,[],False),(True,['release-CAS'],True)])

    def test_batched_owner_unknown_after_release_intent_still_refuses_before_CAS(self):
        with tempfile.TemporaryDirectory() as folder,self.environment(),patch.object(owner,'require_hosted_emulator'):
            h=Harness(folder);query=h.subject.shell
            def shell(*args,**kwargs):
                value=query(*args,**kwargs)
                if args==('sh','-c',owner.PROPERTIES) and (h.subject.output/'original-release-control-intent.json').exists():value.stderr=b'private unknown'
                return value
            h.subject.shell=shell
            with self.assertRaises(RuntimeError):h.execute()
            self.assertNotIn('release-CAS',h.events);self.assertFalse(h.held.release_attempted)
            self.assertTrue((h.subject.output/'original-release-control-intent.json').exists())
            self.assertFalse((h.subject.output/'original-release-control-attempt.json').exists())
            public=b''.join(path.read_bytes() for path in h.subject.output.iterdir() if path.is_file())
            self.assertNotIn(b'private unknown',public)


if __name__=='__main__':unittest.main()
