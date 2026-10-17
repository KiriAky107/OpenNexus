"""Owned observer admission and one original UI call, without a device or network."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from android_sync_runtime import SyncProbe

ID = '11111111-2222-4333-8444-555555555555'


class ObserverBoundary(unittest.TestCase):
    def test_local_or_unowned_emulator_refused_before_any_connection_or_ui(self):
        probe = object.__new__(SyncProbe); probe.args = SimpleNamespace(serial='unowned')
        probe.wait_owned_connection = Mock()
        with self.assertRaises(RuntimeError): probe.association_observer('install', ID)
        probe.wait_owned_connection.assert_not_called()

    def test_unrelated_websocket_target_is_refused_and_only_original_forward_removed(self):
        with tempfile.TemporaryDirectory() as work:
            probe = object.__new__(SyncProbe); probe.args = SimpleNamespace(serial='emulator-5562'); probe.output = Path(work)
            probe.wait_owned_connection = Mock(); probe.shell = Mock(return_value=SimpleNamespace(stdout=b'1234'))
            probe.device = Mock(side_effect=[SimpleNamespace(stdout=b'39111'), SimpleNamespace(returncode=0)])
            probe.command = Mock()
            connection = Mock(); connection.getresponse.return_value = SimpleNamespace(status=200, read=lambda _: json.dumps([
                {'type':'page','title':'OpenNexus','webSocketDebuggerUrl':'ws://unrelated.example:39111/devtools/page/a'}]).encode())
            with patch('android_sync_runtime.require_hosted_emulator'), patch('android_sync_runtime.http.client.HTTPConnection',return_value=connection):
                with self.assertRaisesRegex(RuntimeError,'^OWNED_ASSOCIATION_OBSERVER_UNAVAILABLE$'):
                    probe.association_observer('install',ID)
            probe.command.assert_not_called()
            self.assertEqual(probe.device.call_args.args,('forward','--remove','tcp:39111'))
            saved=json.loads((Path(work)/'association-observer-install.json').read_text('utf-8'))
            self.assertTrue(saved['own_forward_removed']); self.assertNotIn('unrelated.example',json.dumps(saved))

    def test_passive_read_follows_the_single_original_input_even_when_ui_fails(self):
        for succeeded in (False,True):
            with self.subTest(succeeded=succeeded), tempfile.TemporaryDirectory() as work:
                probe=object.__new__(SyncProbe);probe.output=Path(work);probe.phases=[];calls=[]
                probe.start_app=lambda:calls.append('start')
                probe.association_observer=lambda mode,_:calls.append(mode)
                text='OPENNEXUS_SYNC_PHASE_OK associate' if succeeded else 'OPENNEXUS_SYNC_PHASE_FAILED original'
                def instrument(*args,**kwargs):
                    calls.append('original-input');return SimpleNamespace(stdout=text.encode(),returncode=0)
                probe.shell=instrument
                if succeeded:probe.sync_phase('associate')
                else:
                    with self.assertRaises(RuntimeError):probe.sync_phase('associate')
                self.assertEqual(calls,['start','install','original-input','read'])
                self.assertEqual(len(list(Path(work).glob('sync-dispatch-*.json'))),1)
                self.assertEqual(len(probe.phases),int(succeeded))


if __name__=='__main__':unittest.main(verbosity=2)
