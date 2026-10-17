"""Pure ownership/input checks; fake controls do not prove a native selection."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from android_desktop_handoff import HandoffError
from android_desktop_picker import TITLE, WindowsDialog, choose_controls, choose_owned_vault, read_original_choice
from android_desktop_product import DESKTOP_SOURCE


class Controls:
    def __init__(self):
        self.pids = {10: 42, 11: 42, 12: 42}
        self.writes = []
        self.clicks = []
        self.value = ''
        self.refuse = False
        self.change_owner = False

    def owner(self, h): return self.pids[h]
    def title(self, h): return TITLE
    def kind(self, h): return {10: '#32770', 11: 'Edit', 12: 'Button'}[h]
    def control_id(self, h): return {11: 1152, 12: 1}[h]
    def children(self, h): return [11, 12]
    def child_of(self, parent, child): return parent == 10 and child in (11, 12)
    def set_text(self, h, value, dialog, pid):
        self.writes.append((h, value)); self.value = value
        if self.change_owner: self.pids[12] = 77
        return not self.refuse
    def text(self, h): return self.value
    def click(self, h): self.clicks.append(h); return True


class OwnedPicker(unittest.TestCase):
    def character_api(self):
        import ctypes
        api = WindowsDialog.__new__(WindowsDialog); api.ctypes = ctypes; api.api = Mock()
        api.owner = lambda _: 42; api.child_of = lambda parent, child: parent == 10 and child == 11
        api.kind = lambda handle: '#32770' if handle == 10 else 'Edit'
        api.control_id = lambda _: 1152; api.title = lambda _: TITLE
        api.api.SendMessageTimeoutW.return_value = 1
        return api

    def test_path_character_input_preserves_utf16_units_and_uses_only_original_control(self):
        api = self.character_api(); value = 'D:\\owned\\中文😀'
        self.assertTrue(api.set_text(11, value, 10, 42))
        api.api.SendMessageW.assert_called_once_with(11, 0x00B1, 0, -1)
        raw = value.encode('utf-16-le')
        self.assertEqual([call.args[:5] for call in api.api.SendMessageTimeoutW.call_args_list],
            [(11, 0x0102, int.from_bytes(raw[i:i+2], 'little'), 1, 2) for i in range(0,len(raw),2)])
        self.assertTrue(all(1 <= call.args[5] <= 1000 for call in api.api.SendMessageTimeoutW.call_args_list))

    def test_partial_character_delivery_is_not_replayed_or_confirmed(self):
        api = self.character_api(); api.api.SendMessageTimeoutW.side_effect = [1, 0]
        self.assertFalse(api.set_text(11, 'D:\\owned', 10, 42))
        self.assertEqual(api.api.SendMessageTimeoutW.call_count, 2)
        self.assertFalse(any(call.args[1] == 0x00F5 for call in api.api.SendMessageTimeoutW.call_args_list))

    def test_changed_original_owner_stops_remaining_characters_without_global_input(self):
        api = self.character_api()
        def changed(*args):
            api.owner = lambda _: 77
            return 1
        api.api.SendMessageTimeoutW.side_effect = changed
        with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_PICKER_TARGET_INVALID$'):
            api.set_text(11, 'D:\\owned', 10, 42)
        api.api.SendMessageTimeoutW.assert_called_once()

    def test_native_message_timeout_is_unknown_delivery_not_a_selection_rejection(self):
        import ctypes
        api = WindowsDialog.__new__(WindowsDialog)
        api.ctypes = ctypes
        api.api = Mock()
        api.api.SendMessageTimeoutW.return_value = 0
        self.assertIs(api.click(12), False)
        api.api.SendMessageTimeoutW.assert_called_once()

    def test_unknown_click_reads_the_original_native_outcome_without_another_action(self):
        api = Controls()
        def unknown_click(handle):
            api.clicks.append(handle)
            return False
        api.click = unknown_click
        page = Mock()
        original = {'ok': True, 'actualPath': r'D:\owned\vault', 'vaultId': 'original-vault'}
        page.evaluate.return_value = original
        with tempfile.TemporaryDirectory() as work:
            acknowledged = choose_controls(api, 10, 42, r'D:\owned\vault')
            self.assertIs(acknowledged, False)
            self.assertIs(read_original_choice(page, acknowledged, Path(work)), original)
            receipt = json.loads((Path(work) / 'picker-click-delivery.json').read_text('utf-8'))
        self.assertEqual(api.writes, [(11, r'D:\owned\vault')]); self.assertEqual(api.clicks, [12])
        self.assertEqual(receipt['delivery'], 'unknown')
        self.assertFalse(receipt['selection_inferred_from_acknowledgement'])
        page.wait_for_function.assert_called_once_with('()=>window.ownedVaultChoiceResult!==null')
        page.evaluate.assert_called_once()
        self.assertNotIn('openVault(', page.evaluate.call_args.args[0])
        self.assertNotIn('D:', json.dumps(receipt)); self.assertNotIn('vaultId', receipt)

    def test_unsettled_original_remains_unknown_without_replacing_the_original_request(self):
        page = Mock(); page.wait_for_function.side_effect = TimeoutError('private callback argument')
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_PICKER_OUTCOME_UNKNOWN$'):
                read_original_choice(page, False, Path(work))
            receipt = (Path(work) / 'picker-click-delivery.json').read_text('utf-8')
        self.assertNotIn('private', receipt)
        page.wait_for_function.assert_called_once(); page.evaluate.assert_not_called()

    def test_exact_original_dialog_gets_one_path_input_and_one_confirm(self):
        api = Controls()
        choose_controls(api, 10, 42, r'D:\owned\vault')
        self.assertEqual(api.writes, [(11, r'D:\owned\vault')])
        self.assertEqual(api.clicks, [12])

    def test_foreign_process_or_ambiguous_controls_refused_before_input(self):
        api = Controls(); api.pids[10] = 77
        with self.assertRaises(HandoffError): choose_controls(api, 10, 42, r'D:\owned\vault')
        self.assertFalse(api.writes); self.assertFalse(api.clicks)
        api = Controls(); api.children = lambda _: [11, 11, 12]
        with self.assertRaises(HandoffError): choose_controls(api, 10, 42, r'D:\owned\vault')
        self.assertFalse(api.writes); self.assertFalse(api.clicks)

    def test_changed_owner_after_input_prevents_confirm_without_retyping(self):
        api = Controls(); api.change_owner = True
        with self.assertRaises(HandoffError): choose_controls(api, 10, 42, r'D:\owned\vault')
        self.assertEqual(len(api.writes), 1); self.assertFalse(api.clicks)

    def test_refused_path_input_never_confirms_or_retries(self):
        api = Controls(); api.refuse = True
        with self.assertRaises(HandoffError): choose_controls(api, 10, 42, r'D:\owned\vault')
        self.assertEqual(len(api.writes), 1); self.assertFalse(api.clicks)

    def test_local_entry_refused_before_windows_api_or_page_access(self):
        context = {'run_id': 123, 'tools_commit': 'a' * 40, 'android_commit': 'b' * 40, 'desktop_commit': DESKTOP_SOURCE}
        page = Mock()
        with patch.dict(os.environ, {}, clear=True), patch('android_desktop_picker.WindowsDialog') as windows:
            with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_HOST_REFUSED$'):
                choose_owned_vault(page, context, None, Path('unowned'), Path('unowned'))
            windows.assert_not_called()
        page.evaluate.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
