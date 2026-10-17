"""Source/run/hash admission only; these cases never start a client or ADB."""
import unittest
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from android_product_pair_clients import CLIENTS, FixedClientError, fixed_android_client
from android_product_pair_desktop import original_context


class FixedClients(unittest.TestCase):
    def test_workflow_selection_entry_runs_without_site_packages_or_crypto_overlay(self):
        script=Path(__file__).with_name('android_product_pair_clients.py')
        for client in CLIENTS:
            result=subprocess.run([sys.executable,'-S',str(script),client.source,client.build_run],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))

    def test_original_and_new_compiled_clients_keep_separate_run_and_hash(self):
        self.assertEqual(len(CLIENTS), 2)
        self.assertEqual(len({c.source for c in CLIENTS}), 2)
        self.assertEqual(len({c.build_run for c in CLIENTS}), 2)
        self.assertEqual(len({c.apk_sha256 for c in CLIENTS}), 2)
        for client in CLIENTS:
            self.assertIs(fixed_android_client(client.source, client.build_run), client)
            self.assertIs(fixed_android_client(client.source), client)
            with patch('android_product_pair_desktop.owned_root'):
                context = original_context({'GITHUB_RUN_ID': '1234', 'GITHUB_SHA': 'a'*40,
                    'ANDROID_COMPILED_SOURCE': client.source, 'ANDROID_BUILD_RUN_ID': client.build_run})
            self.assertEqual(context['android_commit'], client.source)

    def test_mixed_run_foreign_commit_and_nonstring_do_not_admit_any_client(self):
        for client in CLIENTS:
            for source, run in [(client.source, '1'), (client.source, int(client.build_run)),
                    (client.source, CLIENTS[1 if client==CLIENTS[0] else 0].build_run),
                    ('a'*40, client.build_run), (True, client.build_run)]:
                with self.subTest(source=source, run=run), self.assertRaisesRegex(FixedClientError, 'FIXED_ANDROID_CLIENT_REFUSED'):
                    fixed_android_client(source, run)


if __name__ == '__main__':
    unittest.main()
