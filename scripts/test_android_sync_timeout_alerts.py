"""Closed channel diagnostics; all UI/device/host data is controlled."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_sync_timeout_alerts as alerts
import android_sync_timeout_failure as failure
from test_android_sync_timeout_failure import observation
from test_android_sync_timeout_host import parent,ready,Harness


def diagnostics():
    value=dict(schema=2,catalog_sha256=alerts.CATALOG_SHA256,scan_elapsed_ms=32000,
        text_counts=[0]*50,description_counts=[0]*50,strict_counts=[0]*50,known_channel_conflicts=0,
        fragment_catalog_sha256=alerts.FRAGMENT_CATALOG_SHA256,fragment_body_text_counts=[0]*50,
        fragment_body_description_counts=[0]*50,fragment_header_counts=[0,0],fragment_detail_counts=[0,0],
        unassociated_source_fragments_only=True,complete_alert_inferred=False,
        raw_UI_recorded=False,native_timeout_verified=False)
    # A different complete source-bound UI warning remains diagnostic, not a timeout pass.
    value['text_counts'][0]=value['strict_counts'][0]=1
    return value


class AlertDiagnostics(unittest.TestCase):
    def test_exact_owned_same_scan_or_explicit_unavailable_preserves_false_scope(self):
        value=observation();value['alert_diagnostics']=diagnostics()
        self.assertEqual(failure.validate_observation(value,parent(),ready()),value)
        self.assertFalse(value['native_timeout_verified']);self.assertEqual(value['label_counts'][3],0)
        self.assertEqual(len(alerts.CODES),50);self.assertEqual(alerts.CODES[alerts.NETWORK_INDEX],'SYNC_NETWORK_ERROR')
        value['alert_diagnostics']=None;self.assertEqual(failure.validate_observation(value,parent(),ready()),value)
        absent=dict(value,last_scene_scan_finished=False,label_counts=None,observed_elapsed_ms=None)
        self.assertEqual(failure.validate_observation(absent,parent(),ready()),absent)

    def test_private_extra_partial_boolean_counts_catalog_or_clock_mismatch_are_refused(self):
        for key,new in [('schema',True),('catalog_sha256','0'*64),('scan_elapsed_ms',31999),
            ('text_counts',[True]*50),('description_counts',[3]*50),('strict_counts',[0]*49),
            ('known_channel_conflicts',False),('native_timeout_verified',True),('raw_UI_recorded',True)]:
            value=observation();value['alert_diagnostics']=dict(diagnostics(),**{key:new})
            with self.subTest(key=key),self.assertRaises(RuntimeError):failure.validate_observation(value,parent(),ready())
        for diag in [{},dict(diagnostics(),private='never-export')]:
            with self.assertRaises(RuntimeError):failure.validate_observation(dict(observation(),alert_diagnostics=diag),parent(),ready())
        for value in [dict(observation(),schema=2),{k:v for k,v in observation().items() if k!='alert_diagnostics'}]:
            with self.assertRaises(RuntimeError):failure.validate_observation(value,parent(),ready())

    def test_network_scene_and_channel_subset_must_match_no_partial_or_absent_scan_diagnostics(self):
        for mode in ['network-count','subset','absent']:
            value=observation();diag=diagnostics();value['alert_diagnostics']=diag
            if mode=='network-count':diag['strict_counts'][2]=diag['text_counts'][2]=1
            if mode=='subset':diag['strict_counts'][1]=1
            if mode=='absent':value.update(last_scene_scan_finished=False,label_counts=None,observed_elapsed_ms=None)
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):failure.validate_observation(value,parent(),ready())
        self.assertFalse(alerts.validate(diagnostics(),[False]*8,32000));self.assertFalse(alerts.validate(diagnostics(),[0]*8,True))

    def test_maximum_original_record_fits_one_read_and_bad_nested_data_never_leaks_or_retries(self):
        value=observation();value['label_counts']=[2]*8;diag=diagnostics()
        for name in ['text_counts','description_counts','strict_counts','fragment_body_text_counts','fragment_body_description_counts']:diag[name]=[2]*50
        diag['fragment_header_counts']=diag['fragment_detail_counts']=[2,2]
        diag['known_channel_conflicts']=2;value['alert_diagnostics']=diag
        raw=json.dumps(value).encode();self.assertLess(len(raw),4096)
        for invalid in [None,raw.replace(b'"schema": 2,',b'"schema": 2, "schema": 2,',1),json.dumps(dict(value,alert_diagnostics=dict(diag,private='private payload'))).encode()]:
            with tempfile.TemporaryDirectory(prefix='onx-timeout-alert-schema-') as folder:
                shell=Mock(return_value=SimpleNamespace(returncode=0,stdout=raw if invalid is None else invalid,stderr=b''))
                subject=SimpleNamespace(shell=shell,output=Path(folder));failure.retain_failure(subject,'original',parent(),ready())
                shell.assert_called_once();stored=(Path(folder)/'original-driver-failure-observation.json').read_bytes()
                result=json.loads(stored);self.assertEqual(result['available'],invalid is None);self.assertNotIn(b'private payload',stored)
                if invalid is None:self.assertEqual(result['alert_kind_order'],list(alerts.CODES));self.assertTrue(result['alert_diagnostics_available'])

    def test_actual_host_freezes_failure_before_same_one_original_read_and_no_release_retry(self):
        with tempfile.TemporaryDirectory(prefix='onx-timeout-alert-host-') as folder:
            h=Harness(folder);h.variant='driver-exit-before-release';original=h.subject.shell;reads=[]
            def shell(*args,**kwargs):
                if args[:2]==('run-as',h.subject.DRIVER if hasattr(h.subject,'DRIVER') else failure.DRIVER) and args[-1].endswith('-failure-observation.json'):
                    self.assertTrue((h.subject.output/'original-host-failure.json').exists());reads.append(args)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(dict(observation(),alert_diagnostics=diagnostics())).encode(),stderr=b'')
                return original(*args,**kwargs)
            h.subject.shell=shell
            with self.assertRaises(RuntimeError):h.execute()
            self.assertEqual(len(reads),1);self.assertEqual(h.events,['launch','tap','held','input-finish'])
            self.assertFalse(h.held.release_attempted);self.assertFalse(h.subject.phases)
            stored=json.loads((h.subject.output/'original-driver-failure-observation.json').read_bytes())
            self.assertTrue(stored['available']);self.assertTrue(stored['alert_diagnostics_available']);self.assertFalse(stored['native_timeout_verified'])

    def test_source_fragments_are_unassociated_and_never_change_complete_NETWORK_scene(self):
        value=observation();diag=diagnostics();value['alert_diagnostics']=diag
        diag['fragment_body_text_counts'][alerts.NETWORK_INDEX]=1
        diag['fragment_header_counts']=[1,0];diag['fragment_detail_counts']=[0,1]
        self.assertEqual(failure.validate_observation(value,parent(),ready()),value)
        self.assertEqual(value['label_counts'][3],0);self.assertEqual(diag['strict_counts'][alerts.NETWORK_INDEX],0)
        self.assertTrue(diag['unassociated_source_fragments_only']);self.assertFalse(diag['complete_alert_inferred'])
        for key,new in [('fragment_catalog_sha256','0'*64),('fragment_body_text_counts',[False]*50),
                        ('fragment_body_description_counts',[3]*50),('fragment_header_counts',[1]),
                        ('fragment_detail_counts',[0,True]),('unassociated_source_fragments_only',False),('complete_alert_inferred',True)]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                failure.validate_observation(dict(value,alert_diagnostics=dict(diag,**{key:new})),parent(),ready())


if __name__=='__main__':unittest.main()
