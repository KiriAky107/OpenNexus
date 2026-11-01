"""Closed retained failure schema; host, UI and clocks are controlled."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import android_sync_timeout_failure as failure
import android_sync_timeout_wait as wait
from test_android_sync_timeout_failure import observation
from test_android_sync_timeout_host import parent,ready,Harness
from test_android_sync_timeout_alerts import diagnostics


def facts(point='AFTER_SCENE'):
    return dict(schema=1,point=point,ready_elapsed_ms=1000,previous_elapsed_ms=36999,sampled_elapsed_ms=37001,
        deadline_elapsed_ms=37000,state_observed=False,original_guard_rejected=True,diagnostic_only=True,native_timeout_verified=False)


class TimeoutWait(unittest.TestCase):
    def test_all_exact_original_rejected_comparisons_or_explicit_null_are_diagnostic(self):
        for point in wait.POINTS:
            value=facts(point);value['state_observed']=None if point in wait.POINTS[:2] else point=='AFTER_FINAL_OWNER'
            if point=='SCENE_FALSE_AT_DEADLINE':value['sampled_elapsed_ms']=37000
            self.assertTrue(wait.validate(value,1000));ob=dict(observation(),wait_guard_failure=value)
            if value['state_observed'] is True:ob['label_counts']=[1,1,1,1,0,0,0,0]
            self.assertEqual(failure.validate_observation(ob,parent(),ready()),ob)
            self.assertFalse(ob['native_timeout_verified'])
        self.assertEqual(failure.validate_observation(observation(),parent(),ready()),observation())

    def test_predicate_type_point_clock_scope_and_stage_mismatch_are_refused(self):
        changes=[('schema',True),('point','private'),('ready_elapsed_ms',1001),('previous_elapsed_ms',999),
            ('sampled_elapsed_ms',-1),('sampled_elapsed_ms',37000),('deadline_elapsed_ms',37001),
            ('state_observed',None),('original_guard_rejected',False),('diagnostic_only',False),('native_timeout_verified',True)]
        for key,new in changes:
            with self.subTest(key=key,new=new):self.assertFalse(wait.validate(dict(facts(),**{key:new}),1000))
        for key in ('ready_elapsed_ms','previous_elapsed_ms','sampled_elapsed_ms','deadline_elapsed_ms'):
            self.assertFalse(wait.validate(dict(facts(),**{key:True}),1000))
            self.assertFalse(wait.validate(dict(facts(),**{key:2**63}),1000))
        for point,state in [('BEFORE_OWNER',False),('AFTER_OWNER',True),('AFTER_FINAL_OWNER',False),('SCENE_FALSE_AT_DEADLINE',True)]:
            self.assertFalse(wait.validate(dict(facts(point),state_observed=state),1000))
        for bad in [None,{},dict(facts(),private='secret')]:self.assertFalse(wait.validate(bad,1000))
        self.assertFalse(wait.validate(facts(),True))
        for key,new in [('schema',3),('driver_error_kind','unclassified'),('phase_stage','waiting_release')]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                failure.validate_observation(dict(observation(),wait_guard_failure=facts(),**{key:new}),parent(),ready())
        with self.assertRaises(RuntimeError):failure.validate_observation({k:v for k,v in observation().items() if k!='wait_guard_failure'},parent(),ready())
        for changed in [dict(wait_guard_failure=dict(facts(),state_observed=True)),
                        dict(wait_guard_failure=facts(),last_scene_scan_finished=False,label_counts=None,observed_elapsed_ms=None)]:
            with self.assertRaises(RuntimeError):failure.validate_observation(dict(observation(),**changed),parent(),ready())

    def test_rollback_and_maximum_long_preserve_only_already_sampled_values(self):
        rollback=dict(facts(),previous_elapsed_ms=2000,sampled_elapsed_ms=1999)
        self.assertTrue(wait.validate(rollback,1000));self.assertFalse(wait.validate(dict(rollback,sampled_elapsed_ms=2000),1000))
        start=2**63-1-36000
        value=dict(facts('SCENE_FALSE_AT_DEADLINE'),ready_elapsed_ms=start,previous_elapsed_ms=2**63-2,
                   sampled_elapsed_ms=2**63-1,deadline_elapsed_ms=2**63-1,state_observed=False)
        self.assertTrue(wait.validate(value,start));self.assertFalse(wait.validate(value,start+1))

    def test_maximum_payload_and_one_original_private_read_do_not_export_partial_data(self):
        ob=observation();ob['label_counts']=[2]*8;diag=diagnostics()
        for key in ('text_counts','description_counts','strict_counts','fragment_body_text_counts','fragment_body_description_counts'):diag[key]=[2]*50
        diag['fragment_header_counts']=diag['fragment_detail_counts']=[2,2];diag['known_channel_conflicts']=2
        ob.update(alert_diagnostics=diag,wait_guard_failure=facts());raw=json.dumps(ob).encode();self.assertLess(len(raw),4096)
        for data in [raw,json.dumps(dict(ob,wait_guard_failure=dict(facts(),private='private body'))).encode(),
                     raw.replace(b'"schema": 1,',b'"schema": 1, "schema": 1,',1),b'{"schema":4']:
            with tempfile.TemporaryDirectory(prefix='onx-timeout-wait-read-') as folder:
                shell=Mock(return_value=SimpleNamespace(returncode=0,stdout=data,stderr=b''));subject=SimpleNamespace(shell=shell,output=Path(folder))
                failure.retain_failure(subject,'original',parent(),ready());shell.assert_called_once()
                self.assertEqual(shell.call_args.kwargs,dict(check=False,timeout=1))
                stored=(Path(folder)/'original-driver-failure-observation.json').read_bytes();value=json.loads(stored)
                self.assertEqual(value['available'],data is raw);self.assertNotIn(b'private body',stored)
                if data is raw:self.assertEqual(value['observation']['wait_guard_failure'],facts())

    def test_actual_host_wholefalse_precedes_same_failure_read_and_never_retries_actions(self):
        with tempfile.TemporaryDirectory(prefix='onx-timeout-wait-host-') as folder:
            h=Harness(folder);h.variant='driver-exit-before-release';original=h.subject.shell;reads=[]
            def shell(*args,**kwargs):
                if args[:2]==('run-as',failure.DRIVER) and args[-1].endswith('-failure-observation.json'):
                    self.assertTrue((h.subject.output/'original-host-failure.json').exists());reads.append(args)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(dict(observation(),wait_guard_failure=facts())).encode(),stderr=b'')
                return original(*args,**kwargs)
            h.subject.shell=shell
            with self.assertRaises(RuntimeError):h.execute()
            self.assertEqual(len(reads),1);self.assertEqual(h.events,['launch','tap','held','input-finish'])
            self.assertFalse(h.held.release_attempted);self.assertFalse(h.subject.phases)
            result=json.loads((h.subject.output/'original-driver-failure-observation.json').read_bytes())
            self.assertTrue(result['available']);self.assertFalse(result['native_timeout_verified'])


if __name__=='__main__':unittest.main()
