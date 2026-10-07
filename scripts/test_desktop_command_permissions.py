"""Registered native commands must be reachable through the local main window.

Checking all three declarations catches handlers that compile but are blocked
only when a real WebView invokes them. Host-side operation guards still apply.
"""
from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]/'frontend/src-tauri'


class DesktopCommandPermissionsTests(unittest.TestCase):
    def test_registered_handlers_have_manifest_and_main_window_permissions(self):
        main = (ROOT/'src/main.rs').read_text('utf-8')
        build = (ROOT/'build.rs').read_text('utf-8')
        handlers_text = re.search(r'generate_handler!\[([\s\S]*?)\]', main)
        manifest_text = re.search(r'AppManifest::new\(\)\s*\.commands\(&\[([\s\S]*?)\]', build)
        self.assertIsNotNone(handlers_text, 'No native handler registration was found')
        self.assertIsNotNone(manifest_text, 'No native command manifest was found')
        handlers = set(re.findall(r'\b[a-z][a-z0-9_]+\b', handlers_text.group(1)))
        manifest = set(re.findall(r'"([a-z][a-z0-9_]+)"', manifest_text.group(1)))
        capability = json.loads((ROOT/'capabilities/main.json').read_text('utf-8'))
        allowed = {value.removeprefix('allow-').replace('-', '_')
                   for value in capability['permissions'] if isinstance(value, str) and value.startswith('allow-')}
        self.assertTrue(handlers, 'An empty list cannot verify native access')
        self.assertEqual(handlers, manifest, 'Runtime registration and the generated permission manifest differ')
        self.assertEqual(manifest, allowed, 'A native command is missing its main-window grant or a grant is stale')

    def test_app_commands_remain_local_and_limited_to_main_window(self):
        capability = json.loads((ROOT/'capabilities/main.json').read_text('utf-8'))
        self.assertEqual(capability['windows'], ['main'])
        self.assertTrue(capability.get('local', True))
        self.assertNotIn('remote', capability)
        self.assertFalse(capability.get('webviews', []))


if __name__ == '__main__':
    unittest.main()
