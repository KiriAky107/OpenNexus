"""Pure ownership/input checks; fake controls do not prove a native selection."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from android_desktop_handoff import HandoffError
from android_desktop_picker import TITLE, WindowsDialog, choose_controls, choose_owned_vault, read_original_choice, native_input_types, unicode_input
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
    def keyboard_api(self):
        import ctypes
        api = WindowsDialog.__new__(WindowsDialog); api.ctypes = ctypes; api.api = Mock()
        api.Input, api.Gui = native_input_types()
        api.owner = lambda _: 42; api.child_of = lambda parent, child: parent == 10 and child == 11
        api.kind = lambda handle: '#32770' if handle == 10 else 'Edit'
        api.control_id = lambda _: 1152; api.title = lambda _: TITLE
        api.focus_input = Mock(); api.input_focused = Mock(return_value=True)
        api.api.GetAsyncKeyState.return_value = 0
        api.api.SendInput.side_effect = lambda count, events, size: count
        api.text = Mock(return_value=r'D:\owned')
        return api

    def test_keyboard_batch_has_exact_win32_abi_and_original_utf16_down_up_pairs(self):
        import ctypes
        Input, Gui = native_input_types(); value = 'D:\\owned\\' + chr(0x4E2D) + chr(0x1F600)
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)
        self.assertEqual(ctypes.sizeof(Gui), 72 if ctypes.sizeof(ctypes.c_void_p)==8 else 48)
        events = unicode_input(value, Input); raw = value.encode('utf-16-le')
        self.assertEqual(len(events), len(raw))
        for index, event in enumerate(events):
            self.assertEqual(event.type, 1); self.assertEqual(event.payload.keyboard.vk, 0)
            self.assertEqual(event.payload.keyboard.scan, int.from_bytes(raw[(index//2)*2:(index//2)*2+2], 'little'))
            self.assertEqual(event.payload.keyboard.flags, 6 if index%2 else 4)
        with self.assertRaises(HandoffError): unicode_input('private\nvalue', Input)

    def test_complete_original_keyboard_stream_is_sent_once_and_readback_has_no_raw_values(self):
        api = self.keyboard_api()
        self.assertTrue(api.set_text(11, r'D:\owned', 10, 42))
        api.focus_input.assert_called_once_with(11, 10, 42); api.api.SendInput.assert_called_once()
        self.assertEqual(api.input_result['inserted_events'], api.input_result['expected_events'])
        self.assertTrue(api.input_result['original_focus_verified']); self.assertTrue(api.input_result['readback_matches'])
        self.assertNotIn('D:', json.dumps(api.input_result)); self.assertFalse(api.input_result['automatic_retry'])
        api.api.SendMessageW.assert_called_once_with(11, 0x00B1, 0, -1)

    def test_partial_keyboard_delivery_is_never_replayed_or_confirmed(self):
        api = self.keyboard_api(); api.api.SendInput.side_effect = lambda count, events, size: count-1
        self.assertFalse(api.set_text(11, r'D:\owned', 10, 42))
        api.api.SendInput.assert_called_once(); api.text.assert_not_called()
        self.assertFalse(api.input_result['readback_matches']); self.assertFalse(api.input_result['automatic_retry'])

    def test_foreign_foreground_is_refused_before_any_global_keyboard_input(self):
        api = self.keyboard_api(); api.focus_input = WindowsDialog.focus_input.__get__(api)
        api.api.GetForegroundWindow.return_value = 77
        with self.assertRaisesRegex(HandoffError, '^DESKTOP_PAIR_PICKER_INPUT_REFUSED$'):
            api.set_text(11, r'D:\owned', 10, 42)
        api.api.SetForegroundWindow.assert_called_once_with(10)
        api.api.SendInput.assert_not_called(); api.api.SendMessageTimeoutW.assert_not_called()

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
