"""Closed coordinates, one original OS command, and cleanup on unknown results."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import android_sync_host_touch as runtime
from android_release_upgrade import ReleaseUpgrade


def ready():
    return dict(driver_intent_id='12345678-1234-1234-1234-123456789abc', phase='cycle', package=runtime.APP,
        window=4, original=[100,200,500,600], visible=[100,200,500,600], x=300,y=400,elapsed_ms=1000)


class Process:
    def __init__(self): self.returncode=None; self.terminate=Mock(side_effect=self.terminated); self.kill=self.terminate
    def poll(self): return self.returncode
    def terminated(self): self.returncode=-15
    def communicate(self, timeout):
        if self.returncode is None:self.returncode=0
        return b'OPENNEXUS_RELEASE_SYNC_PHASE_OK cycle\n',b''


class HostTouch(unittest.TestCase):
    def subject(self, folder, value=None, *, partial=False, unknown=False):
        value=value or ready(); reads=0;commands=[]
        subject=SimpleNamespace(output=Path(folder),args=SimpleNamespace(serial='emulator-5580'),adb=Path('not-executed-adb'),
            activity=runtime.APP+'/.MainActivity',driver_entry='ReleaseSyncRuntimeProbe',identity={'owner':'original'},phases=[],stop=Mock())
        subject.observer=SimpleNamespace(observe_identity=Mock(return_value=subject.identity))
        def shell(*args,**kwargs):
            nonlocal reads
            if args[:1]==('pidof',):return SimpleNamespace(stdout=b'1234\n',returncode=0)
            if args==('cat','/proc/uptime'):return SimpleNamespace(stdout=b'1.10 0.5\n',returncode=0)
            if args[:3]==('run-as',runtime.DRIVER,'sh'):
                reads+=1
                return SimpleNamespace(stdout=b'{"driver' if partial and reads==1 else json.dumps(value).encode(),returncode=0)
            if args[:3]==('run-as',runtime.DRIVER,'cat'):return SimpleNamespace(stdout=b'finish\n',returncode=0)
            raise AssertionError('Unexpected read-only observation')
        def command(label,args,**kwargs):
            commands.append((label,args))
            if unknown and label.endswith('-host-touch'):raise TimeoutError('original system input result unknown')
            return SimpleNamespace(stdout=b'',stderr=b'',returncode=0)
        subject.shell=shell;subject.original_command=command;subject.device=Mock(return_value=SimpleNamespace(returncode=0,stdout=b'',stderr=b''))
        return subject,commands

    def execute(self,subject,process):
        with patch.object(runtime,'require_hosted_emulator'),patch.object(runtime.subprocess,'Popen',return_value=process), \
                patch.object(runtime.ReleaseAuthBackground,'app_is_resumed',return_value=True),patch.object(runtime.time,'sleep'):
            runtime.run_hosted_cycle(subject,'cycle','first-cycle')

    def test_exact_fresh_ready_and_reject_foreign_scope_geometry_clock_or_extra_data(self):
        self.assertEqual(runtime.validate_ready(ready(),'cycle',1100),ready())
        for key,value in [('package','foreign'),('phase','cycle-incoming'),('window',True),('x',301),
                          ('visible',[0,0,20000,100]),('original',[100,200,100,600]),('driver_intent_id','other')]:
            bad=deepcopy(ready());bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):runtime.validate_ready(bad,'cycle',1100)
        for clock in (999,3001):
            with self.subTest(clock=clock),self.assertRaises(RuntimeError):runtime.validate_ready(ready(),'cycle',clock)
        bad=ready();bad['password']='not-permitted'
        with self.assertRaises(RuntimeError):runtime.validate_ready(bad,'cycle',1100)

    def test_partial_original_ready_waits_then_one_system_touch_and_no_second_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=self.subject(folder,partial=True);process=Process();self.execute(subject,process)
            touch=[args for label,args in commands if label.endswith('-host-touch')]
            self.assertEqual(touch,[('shell','-T','input touchscreen tap 300 400')])
            self.assertEqual(subject.phases[0]['input_method'],'hosted_adb_touchscreen_tap')
            finish=subject.device.call_args.args[-1]
            self.assertIn(ready()['driver_intent_id']+'.finish',finish)
            self.assertEqual(subject.device.call_args.kwargs['input'],b'finish\n')
            before=len(commands)
            with self.assertRaises(FileExistsError):self.execute(subject,Process())
            self.assertEqual(len(commands),before)

    def test_unknown_original_system_touch_never_retries_or_acknowledges_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=self.subject(folder,unknown=True);process=Process()
            with self.assertRaises(TimeoutError):self.execute(subject,process)
            self.assertEqual(sum(label.endswith('-host-touch') for label,args in commands),1)
            subject.device.assert_not_called();process.terminate.assert_called_once();self.assertEqual(subject.stop.call_count,2)
            self.assertTrue(json.loads((Path(folder)/'first-cycle-instrument-result.json').read_bytes())['unknown_result'])
            self.assertFalse(subject.phases)

    def test_invalid_ready_or_changed_owner_never_sends_system_input(self):
        with tempfile.TemporaryDirectory() as folder:
            bad=ready();bad['package']='foreign';subject,commands=self.subject(folder,bad);process=Process()
            with self.assertRaises(RuntimeError):self.execute(subject,process)
            self.assertFalse([v for v in commands if v[0].endswith('-host-touch')]);subject.device.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=self.subject(folder);subject.observer.observe_identity.return_value={'owner':'changed'}
            with self.assertRaises(RuntimeError):self.execute(subject,Process())
            self.assertFalse([v for v in commands if v[0].endswith('-host-touch')]);subject.device.assert_not_called()

    def test_foreign_phase_refused_before_profile_or_instrumentation_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,commands=self.subject(folder)
            with self.assertRaises(RuntimeError):runtime.run_hosted_cycle(subject,'logout','foreign')
            subject.stop.assert_not_called();self.assertFalse(commands)

    def test_real_original_command_recorder_keeps_phase_and_system_input_intents_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,_=self.subject(folder);subject.original_command=ReleaseUpgrade.original_command.__get__(subject)
            self.execute(subject,Process())
            actual=[call.args for call in subject.device.call_args_list if call.args[:2]==('shell','-T')
                    and call.args[2].startswith('input touchscreen')]
            self.assertEqual(actual,[('shell','-T','input touchscreen tap 300 400')])
            journal=json.loads((Path(folder)/'first-cycle-host-touch-phase-intent.json').read_bytes())
            command=json.loads((Path(folder)/'first-cycle-host-touch-intent.json').read_bytes())
            self.assertEqual(journal['phase'],'cycle');self.assertNotIn('arguments',journal)
            self.assertEqual(command['arguments'],list(actual[0]));self.assertNotIn('phase',command)
            before=subject.device.call_count
            with self.assertRaises(FileExistsError):self.execute(subject,Process())
            self.assertEqual(subject.device.call_count,before)

    def test_real_recorder_unknown_system_input_preserves_original_and_never_finishes_or_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            subject,_=self.subject(folder);subject.original_command=ReleaseUpgrade.original_command.__get__(subject)
            def device(*args,**kwargs):
                if args[:2]==('shell','-T') and args[2].startswith('input touchscreen'):
                    raise runtime.subprocess.TimeoutExpired(args,10,output=b'original-static',stderr=b'')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            subject.device.side_effect=device;process=Process()
            with self.assertRaises(runtime.subprocess.TimeoutExpired):self.execute(subject,process)
            touch=[call for call in subject.device.call_args_list if call.args[:2]==('shell','-T')
                   and call.args[2].startswith('input touchscreen')]
            self.assertEqual(len(touch),1);process.terminate.assert_called_once();self.assertFalse(subject.phases)
            original=json.loads((Path(folder)/'first-cycle-host-touch-result.json').read_bytes())
            self.assertTrue(original['unknown_result']);self.assertFalse(original['automatic_retry'])
            self.assertFalse((Path(folder)/'first-cycle-host-touch-finish-intent.json').exists())


if __name__=='__main__':unittest.main()
