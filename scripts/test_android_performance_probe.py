import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_performance_probe as perf
from android_runtime_probe import APP


def metrics(phase):
    return {'phase':phase,'physical_or_commercial_IME':False,
        'input_backend':'owned real InputConnection; ordered broadcast acknowledgement',
        'open_or_create_to_editable_ms':20.5,'after_input_to_draft_kept_label_ms':12,'save_to_saved_label_ms':10,
        'original_commit_ack_ms':[2.1]* (6 if phase=='performance-create' else 1),
        'timing_scope':'UI readiness and original IME acknowledgements, not frame-paint or physical keystroke latency'}


class PerformanceBoundaries(unittest.TestCase):
    def test_launch_retains_reported_times_without_exporting_other_output_or_inventing_readiness(self):
        actual=perf.parse_launch(b'Status: ok\nActivity: private.other.field\nTotalTime: 310\nWaitTime: 322\n')
        self.assertEqual(actual['TotalTime_ms'],310);self.assertEqual(actual['WaitTime_ms'],322)
        self.assertIsNone(actual['ThisTime_ms']);self.assertNotIn('private',json.dumps(actual))
        for bad in (b'Status: timeout\n',b'Status: ok\nTotalTime: 1\n',
            b'Status: ok\nTotalTime: 1\nTotalTime: 2\nWaitTime: 3\n',
            b'Status: ok\nTotalTime: 1\nWaitTime: 2\nWarning: Activity not started\n'):
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):perf.parse_launch(bad)

    def test_memory_is_single_original_pid_and_numeric_summary_only(self):
        raw=('private mapping path must not be exported\n** MEMINFO in pid 321 ['+APP+'] **\n'
            ' TOTAL PSS: 12345 TOTAL RSS: 23456 TOTAL SWAP PSS: 0\n').encode()
        result=perf.parse_memory(raw,321)
        self.assertEqual(result['total_pss_kB'],12345);self.assertEqual(result['total_rss_kB'],23456)
        self.assertNotIn('private',json.dumps(result));self.assertIn('not included',result['scope'])
        for bad in (raw.replace(b'pid 321',b'pid 123'),raw.replace(APP.encode(),b'another.app'),raw+raw,
            raw.replace(b'TOTAL PSS: 12345',b'NO PSS'),raw.replace(b'TOTAL PSS: 12345',b'TOTAL PSS: -5')):
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):perf.parse_memory(bad,321)

    def test_metrics_keep_native_ack_scope_and_refuse_nonfinite_or_wrong_counts(self):
        self.assertEqual(perf.validate_metrics(metrics('performance-create'),'performance-create')['save_to_saved_label_ms'],10)
        for changed in ({'original_commit_ack_ms':[]},{'open_or_create_to_editable_ms':float('nan')},
            {'save_to_saved_label_ms':True},{'save_to_saved_label_ms':-1},{'physical_or_commercial_IME':True}):
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):
                perf.validate_metrics(dict(metrics('performance-create'),**changed),'performance-create')

    def test_memory_requires_host_before_read_and_does_not_resample_changed_pid(self):
        probe=SimpleNamespace(args=SimpleNamespace(serial='emulator-5562'),shell=Mock(),wait_owned_connection=Mock())
        with patch.object(perf,'require_hosted_emulator',side_effect=RuntimeError('foreign host')):
            with self.assertRaises(RuntimeError):perf.sample_memory(probe)
            probe.shell.assert_not_called()
            probe.wait_owned_connection.assert_not_called()
        with patch.object(perf,'require_hosted_emulator'):
            probe.shell=Mock(side_effect=[SimpleNamespace(stdout=b'321'),SimpleNamespace(stdout=b'original'),SimpleNamespace(stdout=b'322')])
            with self.assertRaisesRegex(RuntimeError,'process changed'):perf.sample_memory(probe)
            self.assertEqual(probe.shell.call_count,3)

    def test_two_original_phases_compare_exact_bytes_and_never_replay_a_failed_save(self):
        for corrupt in (False,True):
            with self.subTest(corrupt=corrupt),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);current=perf.original_long_note();phase=''
                def execute(name,**kwargs):
                    nonlocal current,phase
                    phase=name
                    if name=='performance-reopen':current+=perf.MARKER.encode()
                    (root/(name+'-launch.json')).write_text(json.dumps({'TotalTime_ms':100}))
                probe=SimpleNamespace(args=SimpleNamespace(scope='performance',serial='emulator-5562',commit='a'*40,tools_commit='b'*40,build_origin={}),
                    output=root,binding={'sha256':'c'*64},enable_test_ime=Mock(),phase=Mock(side_effect=execute),
                    original=Mock(side_effect=lambda name:b'wrong' if corrupt else current),
                    draft_rows=Mock(side_effect=lambda:[[perf.PATH,current.decode(),3,3]]),
                    device=Mock(side_effect=lambda *args:SimpleNamespace(stdout=json.dumps(metrics(phase)).encode())),force_stop=Mock())
                with patch.object(perf,'require_hosted_emulator'),patch.object(perf,'sample_memory',side_effect=[{'pid':321,'total_pss_kB':123},{'pid':654,'total_pss_kB':133}]),patch.dict('os.environ',{'GITHUB_RUN_ID':'42'}):
                    if corrupt:
                        with self.assertRaisesRegex(RuntimeError,'no input replay'):perf.run_performance(probe)
                        self.assertEqual(probe.phase.call_count,1)
                        self.assertFalse((root/'long-note-performance-proof.json').exists())
                    else:
                        perf.run_performance(probe)
                        self.assertEqual([c.args[0] for c in probe.phase.call_args_list],['performance-create','performance-reopen'])
                        self.assertEqual(probe.original.call_count,2);probe.force_stop.assert_called_once()
                        report=json.loads((root/'long-note-performance-proof.json').read_text('utf-8'))
                        self.assertFalse(report['performance_threshold_or_optimization_added'])
                        self.assertFalse(report['physical_phone_or_commercial_IME_verified'])


if __name__=='__main__':unittest.main()
