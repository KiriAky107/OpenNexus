"""Original bridge parent uses synthetic pipes/metadata, never a socket or ADB."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from android_desktop_handoff import HandoffError
from android_runtime_probe import APP
import android_original_outcome_bridge_client as client
import test_android_original_outcome_process as process_fixture


class BridgeParent(unittest.TestCase):
    def setUp(self):
        self.owner = process_fixture.OriginalStop('test_one_owned_stop_and_original_observation_no_replay_on_second_call')
        self.owner.setUp(); self.addCleanup(self.owner.doCleanups)
        self.probe, self.context, self.manifest = self.owner.probe, self.owner.context, self.owner.manifest
        self.pages = [{'type':'page','title':'OpenNexus','url':'http://tauri.localhost/#/connection',
                       'webSocketDebuggerUrl':'ws://127.0.0.1:12345/devtools/page/owned'}]
        self.metadata = Mock(side_effect=lambda port: self.pages)
        def device(*args, **kwargs):
            return SimpleNamespace(returncode=0,stdout=b'12345\n' if args[0]=='forward' and args[1]=='tcp:0' else b'device\n',stderr=b'')
        self.probe.device = Mock(side_effect=device)
        common = {'nonce':self.manifest['nonce'],'original_pid':321}
        self.events = [dict(common,event='armed',armed=True,callback_binding_rewritten=False,UI_input_sent=False),
                       dict(common,event='paused',original_callback_consumed=False,callback_bindings_rewritten=False,frozen_fingerprint='d'*64),
                       dict(common,event='owned-stop-authorized',fingerprint='d'*64),
                       dict(common,event='original-exited',original_callback_consumed=False,callback_bindings_rewritten=False,frozen_fingerprint='d'*64)]
        self.factory = Mock()
        self.compiled_reader = Mock()

    def start(self):
        self.handle = SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(b''.join(json.dumps(x).encode()+b'\n' for x in self.events)),
                                      stderr=io.BytesIO(b''),wait=Mock(return_value=0))
        self.factory.return_value = self.handle
        return client.OriginalCompletionBridge(self.probe,self.context,self.manifest,'e'*64,{},factory=self.factory,
            metadata=self.metadata,compiled_reader=self.compiled_reader)

    def exit(self):
        return {'original_pid':321,'original_pid_before_stop':321,'force_stop_returncode':0,'original_pidof_returncode':1,
                'original_pidof_stdout_empty':True,'original_pidof_stderr_empty':True,
                'owned_emulator_still_connected':True,'same_original_immutable_installed_app':True}

    def test_actual_public_event_shapes_and_original_hashes_bind_one_parent_connection(self):
        value = self.start(); value.expect('armed'); value.expect('paused')
        value.send('native-before-stop',{'own':'read-only-observed-native'})
        value.expect('owned-stop-authorized'); value.send('original-exited',self.exit()); value.expect('original-exited')
        sent = [json.loads(x) for x in self.handle.stdin.getvalue().splitlines()]
        self.assertEqual(len(sent),3)
        self.assertEqual(sent[0]['configuration']['fingerprint'],'d'*64)
        self.assertEqual(sent[0]['configuration']['expected_hash'],'e'*64)
        self.owner.stopped = True
        value.close_after_original_exit(self.exit()); self.assertTrue(value.closed)
        self.factory.assert_called_once()
        self.compiled_reader.assert_called_once()
        self.assertTrue(any(c.args[:2]==('forward','--remove') for c in self.probe.device.call_args_list))

    def test_foreign_page_origin_does_not_start_bridge_or_send_UI_or_native_input(self):
        self.pages[0]['url']='https://external.invalid/'
        with self.assertRaisesRegex(HandoffError,'PAGE_REFUSED'): self.start()
        self.factory.assert_not_called()

    def test_unknown_parent_write_keeps_original_intent_and_cannot_be_sent_twice(self):
        value = self.start(); value.expect('armed'); value.expect('paused')
        class Lost(io.BytesIO):
            def write(self, body): raise OSError('PRIVATE_PIPE_ERROR')
        self.handle.stdin = Lost()
        with self.assertRaises(OSError): value.send('native-before-stop',{'one':'original'})
        intent = self.probe.output/'original-completion-command-native-before-stop-intent.json'
        original = intent.read_bytes()
        with self.assertRaises(HandoffError): value.send('native-before-stop',{'two':'replacement'})
        self.assertEqual(intent.read_bytes(),original); self.assertNotIn('PRIVATE_PIPE_ERROR',original.decode())

    def test_foreign_nonce_or_consumed_reply_cannot_grant_stop(self):
        self.events[0]['nonce']='00000000-0000-4000-8000-000000000099'
        value = self.start()
        with self.assertRaisesRegex(HandoffError,'EVENT_UNCONFIRMED'): value.expect('armed')
        with self.assertRaises(HandoffError): value.send('native-before-stop',{})

    def test_early_close_or_unconfirmed_exit_never_removes_owned_forward(self):
        value = self.start()
        with self.assertRaises(HandoffError): value.close_after_original_exit(self.exit())
        self.handle.wait.assert_not_called()
        self.assertFalse(any(c.args[:2]==('forward','--remove') for c in self.probe.device.call_args_list))


if __name__ == '__main__': unittest.main()
