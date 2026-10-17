"""Synthetic source boundaries only; none of these cases starts a client."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from android_desktop_handoff import HandoffError
from android_desktop_pair_ui import OwnedDesktopUI,bound_identity


class PairUIBoundaries(unittest.TestCase):
    def setUp(self):
        self.context={'run_id':1234,'tools_commit':'a'*40,'android_commit':'b'*40,
                      'desktop_commit':'931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'}
        self.payload={'endpoint':'https://owned-example.trycloudflare.com/','username':'android-owned-a-'+'c'*12,
                      'password':'d'*48,'vault_id':'e'*32,'vault_name':'Owned Android Sync '+'e'*32}
        self.status={'vault_id':'11111111-1111-4111-8111-111111111111','credential_state':'ready',
                     'binding':{'id':'22222222-2222-4222-8222-222222222222','remote_vault':'e'*32,
                                'endpoint':self.payload['endpoint'],'account':self.payload['username'],'cursor':2}}

    def test_local_or_user_host_refuses_before_page_or_profile_access(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            page=Mock();root=Path(folder)
            with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_HOST_REFUSED'):
                OwnedDesktopUI(self.context,page,root/'vault',root/'stage-1',1)
            self.assertEqual(page.mock_calls,[]);self.assertEqual(list(root.iterdir()),[])

    def test_bound_scope_and_cold_identity_refuse_other_vault_account_or_noncanonical_ids(self):
        original=bound_identity(self.status,self.payload)
        self.assertEqual(bound_identity(self.status,self.payload,original),original)
        for key,value in [('remote_vault','f'*32),('account','other-account'),('endpoint','http://owned-example.trycloudflare.com/'),
                          ('id','not-a-uuid'),('cursor',True),('cursor',-1)]:
            changed=deepcopy(self.status);changed['binding'][key]=value
            with self.subTest(key=key),self.assertRaises(HandoffError):bound_identity(changed,self.payload)
        changed=deepcopy(self.status);changed['vault_id']='33333333-3333-4333-8333-333333333333'
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_COLD_IDENTITY_CHANGED'):
            bound_identity(changed,self.payload,original)

    def test_readonly_account_identity_keeps_the_same_original_device_after_cold_restart(self):
        ui=object.__new__(OwnedDesktopUI);ui.stage=1;ui.status=Mock(return_value=self.status);ui.page=Mock()
        public=bound_identity(self.status,self.payload);device='f'*32
        details={'vault_id':public['local_vault_id'],'binding_id':public['binding_id'],
                 'vault':{'id':public['remote_vault_id']},'current_device_id':device,
                 'devices':[{'id':device,'revoked':False}]}
        ui.page.evaluate.return_value=details
        first=ui.account_identity(self.payload);ui.stage=2
        self.assertEqual(ui.account_identity(self.payload,first),first)
        details=dict(details,current_device_id='0'*32,devices=[{'id':'0'*32,'revoked':False}])
        ui.page.evaluate.return_value=details
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_COLD_DEVICE_CHANGED'):ui.account_identity(self.payload,first)

    def test_cold_stage_requires_original_identity_before_observing_a_new_device(self):
        ui=object.__new__(OwnedDesktopUI);ui.stage=2;ui.status=Mock();ui.page=Mock()
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_COLD_ORIGINAL_REQUIRED'):ui.account_identity(self.payload)
        ui.status.assert_not_called();self.assertEqual(ui.page.mock_calls,[])

    def test_unknown_source_input_keeps_original_intent_and_never_sends_the_edit_again(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'vault').mkdir();(root/'stage-1').mkdir()
            note=root/'vault/runtime-note.md';note.write_bytes(b'original')
            with patch('android_desktop_pair_ui.owned_root',return_value=root),patch('android_desktop_pair_ui.original_marker'):
                page=Mock();page.evaluate.side_effect=RuntimeError('synthetic observation timeout')
                ui=OwnedDesktopUI(self.context,page,root/'vault',root/'stage-1',1)
                with self.assertRaises(RuntimeError):ui.edit_original_note(b'original',b'new exact source\n')
                self.assertTrue((root/'stage-1/source-edit-original-intent.json').exists())
                with self.assertRaises(FileExistsError):ui.edit_original_note(b'original',b'new exact source\n')
                page.evaluate.assert_called_once();self.assertEqual(note.read_bytes(),b'original')

    def test_saved_original_note_survives_a_transient_read_without_replaying_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'vault').mkdir();(root/'stage-1').mkdir()
            note=root/'vault/runtime-note.md';note.write_bytes(b'original');after=b'new exact source\n'
            page=Mock();save=Mock();page.locator.side_effect=lambda selector:save if selector=='.editor-header .save-button' else Mock()
            state={'saved':False,'blocked':False}
            def saved():note.write_bytes(after);state['saved']=True
            save.click.side_effect=saved;original_read=Path.read_bytes
            def read(path):
                if path==note and state['saved'] and not state['blocked']:
                    state['blocked']=True;raise PermissionError('private Windows read sharing detail')
                return original_read(path)
            with patch('android_desktop_pair_ui.owned_root',return_value=root),patch('android_desktop_pair_ui.original_marker'),\
                 patch.object(Path,'read_bytes',autospec=True,side_effect=read),patch('android_desktop_pair_ui.time.sleep'):
                ui=OwnedDesktopUI(self.context,page,root/'vault',root/'stage-1',1)
                result=ui.edit_original_note(b'original',after)
            self.assertEqual(result['bytes'],len(after));self.assertEqual(note.read_bytes(),after)
            save.click.assert_called_once();page.get_by_label.return_value.fill.assert_called_once()

    def test_persistently_unavailable_original_read_keeps_an_unconfirmed_observation_without_UI_input(self):
        import json
        for failure in (PermissionError,FileNotFoundError):
            with self.subTest(failure=failure.__name__),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'vault').mkdir();(root/'stage-1').mkdir()
                ui=object.__new__(OwnedDesktopUI);ui.vault=root/'vault';ui.attempt=root/'stage-1';ui.page=Mock()
                note=ui.vault/'runtime-note.md';note.write_bytes(b'original')
                with patch.object(Path,'read_bytes',side_effect=failure('PRIVATE READ DETAIL')),\
                     patch('android_desktop_pair_ui.time.monotonic',side_effect=[0,61]):
                    with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_ORIGINAL_SAVE_UNKNOWN'):
                        ui.wait_original_note_save(note,b'unconfirmed desired bytes')
                path=ui.attempt/'source-save-observation.json';value=json.loads(path.read_text('utf-8'))
                self.assertEqual(value['read_attempts'],1);self.assertEqual(value['transient_read_failures'],{failure.__name__:1})
                self.assertFalse(value['matched_requested_bytes']);self.assertFalse(value['Save_or_edit_replayed'])
                self.assertNotIn('PRIVATE',path.read_text('utf-8'));self.assertEqual(ui.page.mock_calls,[])
                self.assertEqual(note.read_bytes(),b'original')

    def test_readable_but_different_original_content_is_not_a_save_acknowledgement(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'vault').mkdir();(root/'stage-1').mkdir()
            ui=object.__new__(OwnedDesktopUI);ui.vault=root/'vault';ui.attempt=root/'stage-1';ui.page=Mock()
            note=ui.vault/'runtime-note.md';note.write_bytes(b'original')
            with patch('android_desktop_pair_ui.time.monotonic',side_effect=[0,61]):
                with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_ORIGINAL_SAVE_UNKNOWN'):
                    ui.wait_original_note_save(note,b'new exact source')
            value=json.loads((ui.attempt/'source-save-observation.json').read_text('utf-8'))
            self.assertFalse(value['matched_requested_bytes']);self.assertEqual(value['transient_read_failures'],{})
            self.assertEqual(value['last_read_bytes'],len(b'original'));self.assertEqual(ui.page.mock_calls,[])

    def test_unknown_Canvas_input_keeps_original_bytes_and_does_not_repeat_any_input(self):
        import json
        original=json.dumps({'nodes':[{'id':'owned-text','type':'text','x':0,'y':0,'width':300,'height':180,'text':'original','unknown':{'keep':True}}],
                             'edges':[],'vendor':{'keep':True}}).encode()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'vault').mkdir();(root/'stage-1').mkdir();(root/'vault/peer.canvas').write_bytes(original)
            with patch('android_desktop_pair_ui.owned_root',return_value=root),patch('android_desktop_pair_ui.original_marker'):
                page=Mock();page.evaluate.side_effect=RuntimeError('synthetic observation timeout')
                ui=OwnedDesktopUI(self.context,page,root/'vault',root/'stage-1',1);ui.status=Mock(return_value={'paused':True})
                with self.assertRaises(RuntimeError):ui.edit_original_canvas(original,'owned-text','new own text',48,64)
                with self.assertRaises(FileExistsError):ui.edit_original_canvas(original,'owned-text','new own text',48,64)
                page.evaluate.assert_called_once();self.assertEqual((root/'vault/peer.canvas').read_bytes(),original)

    def test_Canvas_mutation_refuses_before_UI_when_original_client_is_not_paused(self):
        ui=object.__new__(OwnedDesktopUI);ui.stage=1;ui.page=Mock();ui.status=Mock(return_value={'paused':False})
        with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_CANVAS_BATCH_NOT_PAUSED'):
            ui.edit_original_canvas(b'{}','owned-text','new own text',48,64)
        self.assertEqual(ui.page.mock_calls,[])

    def test_unknown_pause_is_readonly_reconciled_without_repeating_the_original_control(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=object.__new__(OwnedDesktopUI);ui.stage=1;ui.context=self.context;ui.attempt=Path(folder)
            panel=Mock();ui.panel=Mock(return_value=panel);ui.status=Mock(return_value={'paused':False,'binding':{'id':self.status['binding']['id']}})
            panel.get_by_role.return_value.click.side_effect=RuntimeError('synthetic original input outcome unknown')
            with self.assertRaises(RuntimeError):ui.set_paused(True)
            with self.assertRaises(FileExistsError):ui.set_paused(True)
            panel.get_by_role.return_value.click.assert_called_once()
            ui.status.return_value={'paused':True,'binding':{'id':self.status['binding']['id']}}
            reconciled=ui.set_paused(True)
            self.assertFalse(reconciled['new_UI_input']);self.assertTrue(reconciled['original_intent_readonly_reconciled'])
            panel.get_by_role.return_value.click.assert_called_once()


if __name__=='__main__':unittest.main(verbosity=2)
