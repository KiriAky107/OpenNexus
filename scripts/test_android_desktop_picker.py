"""Pure ownership/input checks; fake controls do not prove a native selection."""
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from android_desktop_handoff import HandoffError
from android_desktop_picker import TITLE, choose_controls, choose_owned_vault
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
    def set_text(self, h, value):
        self.writes.append((h, value)); self.value = value
        if self.change_owner: self.pids[12] = 77
        return not self.refuse
    def text(self, h): return self.value
    def click(self, h): self.clicks.append(h)


class OwnedPicker(unittest.TestCase):
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
