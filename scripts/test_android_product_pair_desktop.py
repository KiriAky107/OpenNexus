"""Hosted stage boundaries only; these cases do not start Windows or a pair."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from android_desktop_handoff import HandoffError
from android_desktop_pair_ui import OwnedDesktopUI
from android_product_pair_desktop import original_context,prepare,read_bodies


class DesktopStages(unittest.TestCase):
    def context(self):return {'run_id':1234,'tools_commit':'a'*40,'android_commit':'18b6abbbf8bc8dae249469f7bcd9e5139cb39fee','desktop_commit':'931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'}

    def test_local_stage_refuses_before_downloading_or_generating_a_receiving_key(self):
        env={'GITHUB_RUN_ID':'1234','GITHUB_SHA':'a'*40,'ANDROID_COMPILED_SOURCE':'18b6abbbf8bc8dae249469f7bcd9e5139cb39fee','ANDROID_BUILD_RUN_ID':'39054475515'}
        with patch.dict(os.environ,env,clear=True),patch('android_product_pair_desktop.prepare_receiving_key') as key,patch('urllib.request.urlopen') as download:
            with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_HOST_REFUSED'):original_context(os.environ)
            with self.assertRaisesRegex(HandoffError,'DESKTOP_PAIR_HOST_REFUSED'):prepare(self.context())
            key.assert_not_called();download.assert_not_called()

    def make_ui(self,root):
        (root/'vault').mkdir();(root/'stage-1').mkdir()
        with patch('android_desktop_pair_ui.owned_root',return_value=root),patch('android_desktop_pair_ui.original_marker'):
            ui=OwnedDesktopUI(self.context(),Mock(),root/'vault',root/'stage-1',1)
        ui.panel=Mock();ui.wait_status=Mock(return_value={'binding':{'id':'b'},'credential_state':'ready',
            'paused':False,'running':False,'pending':0,'conflicts':[],'last_cycle_success_at':1})
        return ui

    def test_two_explicit_cycles_have_distinct_original_intents_in_the_same_client(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.make_ui(Path(folder));ui.wait_status.return_value['binding']['cursor']=3
            for phase in ('initial','outbound'):ui.finish_actual_cycle(cycle=phase,force=True)
            records=[json.loads(p.read_bytes()) for p in ui.attempt.glob('sync-now-*-original-intent.json')]
            self.assertEqual({r['original']['explicit_cycle'] for r in records},{'initial','outbound'})
            self.assertEqual(len({r['driver_intent_id'] for r in records}),2)
            self.assertEqual(ui.panel.return_value.get_by_role.return_value.click.call_count,2)

    def test_unknown_cycle_input_is_not_resent_and_cold_client_cannot_use_a_new_initial_phase(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.make_ui(Path(folder));ui.panel.return_value.get_by_role.return_value.click.side_effect=RuntimeError('Synthetic lost completion')
            with self.assertRaises(RuntimeError):ui.finish_actual_cycle(cycle='initial',force=True)
            original=(ui.attempt/'sync-now-initial-original-intent.json').read_bytes()
            with self.assertRaises(FileExistsError):ui.finish_actual_cycle(cycle='initial',force=True)
            self.assertEqual((ui.attempt/'sync-now-initial-original-intent.json').read_bytes(),original)
            ui.panel.return_value.get_by_role.return_value.click.assert_called_once()
            ui.stage=2;ui.panel.reset_mock()
            with self.assertRaises(HandoffError):ui.finish_actual_cycle(cycle='initial',force=True)
            ui.panel.assert_not_called()

    def test_media_body_reader_rejects_symlinks_instead_of_exporting_outside_original_vault(self):
        from android_product_pair_stages import IMAGE_PATH
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);vault=root/'vault';vault.mkdir()
            (vault/'runtime-note.md').write_bytes(b'note');(vault/'peer.canvas').write_bytes(b'{}')
            image=vault/IMAGE_PATH;image.parent.mkdir(parents=True);image.write_bytes(b'image')
            self.assertEqual(len(read_bodies(vault)),3)
            with patch('pathlib.Path.is_symlink',lambda path:path==image):
                with self.assertRaisesRegex(HandoffError,'FILE_SCOPE_INVALID'):read_bodies(vault)


if __name__=='__main__':unittest.main()
