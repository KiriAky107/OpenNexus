"""Affected real host records and new pure ready binding; controlled device only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from android_sync_timeout_preinput import decode_wire
from test_android_sync_timeout_host import Harness, host, parent, ready
from test_android_release_sync_timeout_snapshot_stop import SnapshotStop


class PreinputHost(unittest.TestCase):
    def test_JVM_original_writer_envelopes_have_cross_language_lineage(self):
        data = json.loads((Path(os.environ['ONX_PREINPUT_FLOW_ROOT'])/'flow-vectors.json').read_bytes())
        self.assertTrue(data['passed']); self.assertFalse(data['actual_Android_or_Native'])
        for wire in data['vectors']:
            r = wire['original_ready']
            p = dict(schema=1, phase=host.TIMEOUT, parent_intent_id=r['parent_intent_id'],
                     input_intent_id=r['driver_intent_id'], native_invocation_id_observed=False)
            self.assertEqual(decode_wire(json.dumps(wire).encode(), p, r['uid']), wire)
            self.assertFalse(wire['preinput_current_fit']['future_warning_visibility_guaranteed'])

    def test_TIMEOUT_single_original_ready_buffer_and_narrow_projection(self):
        with tempfile.TemporaryDirectory(prefix='onx-preinput-host-') as folder:
            h = Harness(folder); original = h.subject.shell; queries = []
            def shell(*args, **kwargs):
                if args[:4] == ('run-as', host.DRIVER, 'sh', '-c'):
                    queries.append(args[4].rsplit('cat ', 1)[1])
                return original(*args, **kwargs)
            h.subject.shell = shell; h.execute()
            private = 'no_backup/owned-sync-timeout-'+host.TIMEOUT+'-cycle-start-ready.json'
            self.assertEqual(queries.count(private), 1)
            self.assertFalse(any('preinput-current-fit' in path or 'viewport-' in path for path in queries))
            original_ready = json.loads((h.subject.output/'original-ready.json').read_bytes())
            current = json.loads((h.subject.output/'original-preinput-current-fit.json').read_bytes())
            self.assertEqual(original_ready, ready()); self.assertTrue(current['current_geometry_only'])
            self.assertFalse(current['network_admission']); self.assertEqual(h.events.count('tap'), 1)

    def test_complete_invalid_wire_rejects_before_input_without_repair_read(self):
        for mode in ('legacy', 'extra', 'hash', 'native', 'wrong-inner', 'duplicate'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='onx-preinput-host-') as folder:
                h = Harness(folder); original = h.subject.shell; queries = []
                def shell(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if args[:4] == ('run-as', host.DRIVER, 'sh', '-c') and args[4].endswith('cycle-start-ready.json'):
                        queries.append(args)
                        wire = json.loads(result.stdout)
                        if mode == 'legacy': wire = wire['original_ready']
                        if mode == 'extra': wire['private_unknown'] = 'synthetic-private-sentinel'
                        if mode == 'hash': wire['preinput_current_fit']['ready_metadata_canonical_sha256'] = '0'*64
                        if mode == 'native': wire['preinput_current_fit']['native_timeout_verified'] = True
                        if mode == 'wrong-inner': wire['original_ready']['window'] += 1
                        raw = json.dumps(wire).encode()
                        if mode == 'duplicate': raw = raw[:-1]+b',"schema":2}'
                        result.stdout = raw
                    return result
                h.subject.shell = shell
                with self.assertRaises(RuntimeError): h.execute()
                self.assertEqual(len(queries), 1); self.assertIsNone(h.tap_at)
                self.assertFalse(h.held.release_attempted); self.assertEqual(h.events, ['launch'])
                self.assertFalse(any(b'synthetic-private-sentinel' in path.read_bytes() for path in h.subject.output.glob('*.json')))

    def test_missing_or_changed_new_record_blocks_completed_driver_snapshot_stop(self):
        for mode in ('missing', 'changed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='onx-preinput-stop-') as folder:
                stop = SnapshotStop(folder, phase=host.TIMEOUT)
                path = stop.subject.output/(stop.label+'-preinput-current-fit.json')
                if mode == 'missing': path.unlink()
                else:
                    value = json.loads(path.read_bytes()); value['current_geometry_only'] = False; path.write_text(json.dumps(value), 'utf-8')
                with self.assertRaises(RuntimeError): stop.execute()
                self.assertEqual(stop.commands, [])

    def test_existing_local_projection_is_preserved_and_touch_not_retried(self):
        with tempfile.TemporaryDirectory(prefix='onx-preinput-host-') as folder:
            h = Harness(folder); path = h.subject.output/'original-preinput-current-fit.json'
            original = b'synthetic-existing-original\n'; path.write_bytes(original)
            with self.assertRaises(FileExistsError): h.execute()
            self.assertEqual(path.read_bytes(), original); self.assertIsNone(h.tap_at)
            self.assertEqual(h.events, ['launch']); self.assertFalse(h.held.release_attempted)
        with tempfile.TemporaryDirectory(prefix='onx-preinput-host-') as folder:
            h = Harness(folder); original_record = host.record
            def slow_record(path, value):
                original_record(path, value)
                if path.name == 'original-preinput-current-fit.json': h.now += 2.1
            with patch.object(host, 'record', side_effect=slow_record), self.assertRaises(RuntimeError): h.execute()
            self.assertIsNone(h.tap_at); self.assertFalse(h.held.release_attempted)


if __name__ == '__main__':
    unittest.main()
