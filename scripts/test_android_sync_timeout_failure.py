"""Closed failure diagnostics: actual file/host paths, controlled device/GUI."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_sync_timeout_failure as failure
import android_sync_timeout_host as host
from test_android_sync_timeout_host import Harness,parent,ready


def observation():
    p,r=parent(),ready()
    return dict(schema=2,phase=host.TIMEOUT,parent_intent_id=p['parent_intent_id'],input_intent_id=p['input_intent_id'],package=host.APP,
        uid=r['uid'],window=r['window'],phase_stage='waiting_failed_scene',original_ready_elapsed_ms=r['elapsed_ms'],input_finished=True,
        last_scene_scan_finished=True,label_counts=[1,1,1,0,0,0,0,0],observed_elapsed_ms=32000,count_saturated_at=2,
        driver_error_kind='sequence_guard',failure_only=True,native_timeout_verified=False,raw_UI_recorded=False,alert_diagnostics=None)


class TimeoutFailure(unittest.TestCase):
    def test_exact_static_host_code_and_error_kind_only_no_private_exception_text(self):
        for code in failure.SAFE_CODES:self.assertEqual(failure.failure_reason(RuntimeError(code)),code)
        for message in ('private password','SYNC_TIMEOUT_UNAPPROVED_SECRET','SYNC_PROCESS_WAIT_TIMEOUT private', 'SYNC_PROCESS_WAIT_TIMEOUT\n'):
            self.assertEqual(failure.failure_reason(RuntimeError(message)),'unclassified')
        secret=type('PrivateCredential',(RuntimeError,),{})('SYNC_PROCESS_WAIT_TIMEOUT')
        self.assertEqual(failure.failure_reason(secret),'unclassified');self.assertEqual(failure.failure_kind(secret),'Exception')

    def test_closed_owned_shape_accepts_only_bounded_counts_or_explicit_absence(self):
        value=observation();self.assertEqual(failure.validate_observation(value,parent(),ready()),value)
        absent=dict(value,last_scene_scan_finished=False,label_counts=None,observed_elapsed_ms=None)
        self.assertEqual(failure.validate_observation(absent,parent(),ready()),absent)
        for key,new in (('package','cc.kronecker.opennexus.dev'),('schema',True),('label_counts',[True]*8),('label_counts',[3]*8),
            ('observed_elapsed_ms',999),('input_finished',False),('raw_UI_recorded',True),('native_timeout_verified',True),
            ('parent_intent_id',value['input_intent_id']),('phase_stage','private text'),('driver_error_kind','private error')):
            bad=dict(value,**{key:new})
            with self.subTest(key=key,new=new),self.assertRaises(RuntimeError):failure.validate_observation(bad,parent(),ready())
        with self.assertRaises(RuntimeError):failure.validate_observation(dict(value,AX='private'),parent(),ready())

    def test_one_private_driver_read_exports_only_approved_closed_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            shell=Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps(observation()).encode(),stderr=b''))
            subject=SimpleNamespace(shell=shell,output=Path(folder))
            failure.retain_failure(subject,'original',parent(),ready());shell.assert_called_once()
            args,kwargs=shell.call_args;self.assertEqual(args[:2],('run-as',host.DRIVER));self.assertEqual(kwargs,dict(check=False,timeout=1))
            self.assertIn('owned-sync-timeout-'+host.TIMEOUT+'-failure-observation.json',args[4])
            result=json.loads((Path(folder)/'original-driver-failure-observation.json').read_bytes())
            self.assertTrue(result['available']);self.assertTrue(result['failure_only']);self.assertFalse(result['native_timeout_verified'])
            before=(Path(folder)/'original-driver-failure-observation.json').read_bytes()
            with self.assertRaises(FileExistsError):failure.retain_failure(subject,'original',parent(),ready())
            shell.assert_called_once()
            self.assertEqual((Path(folder)/'original-driver-failure-observation.json').read_bytes(),before)

    def test_unknown_partial_extra_private_or_late_read_is_unavailable_without_retry(self):
        for mode in ('private','partial','stderr','return-bool','extra-key','duplicate-key','late','backward'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                raw=json.dumps(observation()).encode();code=0;stderr=b''
                if mode=='private':raw=b'private original UI and credentials'
                if mode=='partial':raw=b'{"schema":'
                if mode=='stderr':stderr=b'private device error'
                if mode=='return-bool':code=False
                if mode=='extra-key':raw=json.dumps(dict(observation(),raw_UI='private')).encode()
                if mode=='duplicate-key':raw=raw[:-1]+b',"schema":1}'
                shell=Mock(return_value=SimpleNamespace(returncode=code,stdout=raw,stderr=stderr));subject=SimpleNamespace(shell=shell,output=Path(folder))
                stamps=[0,1.001] if mode=='late' else [1,.999] if mode=='backward' else [0,0]
                with patch.object(failure.time,'monotonic',side_effect=stamps):failure.retain_failure(subject,'original',parent(),ready())
                shell.assert_called_once();result=(Path(folder)/'original-driver-failure-observation.json').read_bytes()
                self.assertFalse(json.loads(result)['available']);self.assertNotIn(b'private',result)

    def test_actual_host_freezes_false_before_one_observation_and_never_retries_input_or_release(self):
        with tempfile.TemporaryDirectory() as folder:
            h=Harness(folder);h.variant='driver-exit-before-release';original=h.subject.shell;reads=[]
            def shell(*args,**kwargs):
                if args[:2]==('run-as',host.DRIVER) and args[-1].endswith('-failure-observation.json'):
                    self.assertTrue((h.subject.output/'original-host-failure.json').is_file());reads.append(args)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(observation()).encode(),stderr=b'')
                return original(*args,**kwargs)
            h.subject.shell=shell
            with self.assertRaises(RuntimeError):h.execute()
            self.assertEqual(len(reads),1);self.assertEqual(h.events,['launch','tap','held','input-finish'])
            value=json.loads((h.subject.output/'original-host-failure.json').read_bytes())
            self.assertIn(value['guard_code'],failure.SAFE_CODES);self.assertFalse(value['whole_timeout_component_passed'])
            retained=json.loads((h.subject.output/'original-driver-failure-observation.json').read_bytes());self.assertTrue(retained['available'])
            self.assertFalse(h.held.release_attempted);self.assertFalse(h.subject.phases)


if __name__=='__main__':unittest.main()
