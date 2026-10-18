import io,json,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import android_canvas_view_input as original
from android_runtime_probe import APP
from test_android_canvas_view_geometry import native,observed

NONCE='278eccde-7cda-42a4-8a3b-132c7bbba901'
IDENTITY=dict(pid=321,start_ticks=450,uid=10216,package=APP)

class ViewOriginalInput(unittest.TestCase):
    def test_one_original_driver_accepts_two_ordered_read_only_checkpoints_without_touch_or_product_commands(self):
        for mode in ('normal','wrong_nonce','reversed','duplicate','unknown_read','ended','native_failed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                probe=SimpleNamespace(args=SimpleNamespace(scope='canvas-view',serial='emulator-5562',commit='a'*40,tools_commit='b'*40),
                    output=Path(folder),adb=Path('owned-adb'),activity=APP+'/.MainActivity',shell=Mock(),device=Mock())
                stages=('after','before') if mode=='reversed' else ('before','before') if mode=='duplicate' else ('before','after')
                nonce='a'*36 if mode=='wrong_nonce' else NONCE
                stream=b''.join(('INSTRUMENTATION_STATUS: owned_canvas_view_ready='+nonce+':view-pan:'+stage+'\n').encode() for stage in stages)
                stream+=b'OPENNEXUS_RUNTIME_FAILED\n' if mode=='native_failed' else b'OPENNEXUS_RUNTIME_PHASE_OK view-pan\n'
                process=Mock();process.stdout=io.BytesIO(stream);process.returncode=None;process.poll.return_value=0 if mode=='ended' else None
                def wait(timeout):process.returncode=0;return 0
                process.wait.side_effect=wait
                with patch.object(original,'require_hosted_emulator'),patch.object(original.uuid,'uuid4',return_value=NONCE), \
                    patch.object(original,'original_identity',return_value=IDENTITY),patch.object(original.subprocess,'Popen',return_value=process) as launch, \
                    patch.object(original,'read_original_geometry',side_effect=RuntimeError('private error') if mode=='unknown_read' else None) as read, \
                    patch.object(original,'read_original_frame_check',return_value={'stage':'unit-only'}):
                    result=original.instrument_original_view(probe,'view-pan')
                launch.assert_called_once();probe.shell.assert_not_called();probe.device.assert_not_called()
                self.assertLessEqual(read.call_count,2)
                self.assertEqual(result.returncode,0 if mode=='normal' else 1)
                if mode=='normal':self.assertEqual([call.args[3] for call in read.call_args_list],['before','after'])
                if mode in ('wrong_nonce','reversed','ended'):read.assert_not_called()
                if mode in ('unknown_read','duplicate'):self.assertEqual(read.call_count,1)
                report=json.loads((Path(folder)/'view-pan-observation-stream.json').read_bytes())
                self.assertNotIn('private error',json.dumps(report));self.assertFalse(report['automatic_retry'])
                process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_remaining_scope_refuses_accepted_gestures_before_any_process_or_device_read(self):
        probe=SimpleNamespace(args=SimpleNamespace(scope='canvas-view-remaining',serial='emulator-5562'))
        with patch.object(original,'require_hosted_emulator'),patch.object(original,'original_identity') as identity, \
            patch.object(original.subprocess,'Popen') as launch:
            for phase in ('view-pan','view-pinch','view-cancel','view-takeover'):
                with self.assertRaises(RuntimeError):original.instrument_original_view(probe,phase)
        identity.assert_not_called();launch.assert_not_called()

    def test_changed_process_refuses_before_forward_ack_or_renderer_read(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=SimpleNamespace(output=Path(folder),device=Mock(),command=Mock())
            with patch.object(original,'original_identity',return_value=dict(IDENTITY,pid=322)):
                with self.assertRaises(RuntimeError):original.read_original_geometry(probe,NONCE,'view-pan','before',IDENTITY)
            probe.device.assert_not_called();probe.command.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_foreign_native_observation_is_refused_before_forward_or_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=SimpleNamespace(output=Path(folder),device=Mock(return_value=SimpleNamespace(stdout=b'{"scope":"private"}')),command=Mock())
            with patch.object(original,'original_identity',return_value=IDENTITY):
                with self.assertRaises(RuntimeError):original.read_original_geometry(probe,NONCE,'view-pan','before',IDENTITY)
            self.assertEqual(probe.device.call_count,1);probe.command.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_metadata_bytes_and_hashes_are_preserved_and_one_driver_ack_follows_forward_cleanup(self):
        for invalid in (False,True):
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as folder:
                value=observed()
                if invalid:value['private_content']='must not reach driver'
                native_raw=(json.dumps(native(),indent=2)+'\n').encode()
                renderer_raw=(json.dumps(value,indent=1)+'\n').encode();calls=[]
                def device(*args,**kwargs):
                    calls.append((args,kwargs))
                    return SimpleNamespace(returncode=0,stdout=native_raw if args[0]=='exec-out' else b'12345' if args==('forward','tcp:0','localabstract:webview_devtools_remote_321') else b'')
                probe=SimpleNamespace(output=Path(folder),device=Mock(side_effect=device),command=Mock(return_value=SimpleNamespace(returncode=0,stdout=renderer_raw)))
                connection=Mock();response=connection.getresponse.return_value;response.status=200
                response.read.return_value=json.dumps([dict(type='page',title='OpenNexus',webSocketDebuggerUrl='ws://localhost:12345/devtools/page/owned')]).encode()
                with patch.object(original,'original_identity',return_value=IDENTITY),patch.object(original.http.client,'HTTPConnection',return_value=connection):
                    if invalid:
                        with self.assertRaises(RuntimeError):original.read_original_geometry(probe,NONCE,'view-pan','before',IDENTITY)
                    else:original.read_original_geometry(probe,NONCE,'view-pan','before',IDENTITY)
                acknowledgements=[v for v in calls if v[0][0]=='shell']
                self.assertEqual(len(acknowledgements),0 if invalid else 1)
                if not invalid:
                    ack=json.loads(acknowledgements[0][1]['input'])
                    self.assertEqual(ack['native_sha256'],original.hashlib.sha256(native_raw).hexdigest())
                    self.assertEqual(ack['renderer_sha256'],original.hashlib.sha256(renderer_raw).hexdigest())
                    self.assertEqual((Path(folder)/'view-pan-before-native-view.json').read_bytes(),native_raw)
                    self.assertEqual((Path(folder)/'view-pan-before-renderer-view.json').read_bytes(),renderer_raw)
                    self.assertLess(next(i for i,v in enumerate(calls) if v[0][:2]==('forward','--remove')),
                        next(i for i,v in enumerate(calls) if v[0][0]=='shell'))

if __name__=='__main__':unittest.main()
