"""Execute the actual signal publication shell on small owned local files only."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock

from android_release_auth_background import ReleaseAuthBackground


class OriginalFinishSignal(unittest.TestCase):
    def shell_path(self):
        path = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/sh')
        self.assertTrue(path.is_file(), 'A local POSIX shell is required for the actual publication protocol check')
        return path

    def subject(self, root):
        subject = object.__new__(ReleaseAuthBackground)
        subject.output = root / 'evidence'; subject.output.mkdir()
        (root / 'no_backup').mkdir()
        subject.shell = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, (root / args[-1]).read_bytes(), b'')
        return subject

    def actual_script(self, args):
        self.assertEqual(args[:2], ('shell', '-T'))
        words = shlex.split(args[2])
        self.assertEqual(words[:4], ['run-as', 'cc.kronecker.opennexus.testdriver', 'sh', '-c'])
        self.assertEqual(len(words), 5)
        return words[4]

    def test_no_partial_signal_visible_during_actual_stdin_write_and_original_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); subject = self.subject(root); calls = []
            def device(*args, **kwargs):
                calls.append(args); script = self.actual_script(args)
                process = subprocess.Popen([str(self.shell_path()), '-c', script], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                process.stdin.write(b'f'); process.stdin.flush()
                deadline = time.monotonic() + 3
                while not (root / 'no_backup/owned-auth-held.finish.tmp').exists():
                    self.assertLess(time.monotonic(), deadline); time.sleep(.01)
                # The old direct publication made its final file visible here.
                self.assertFalse((root / 'no_backup/owned-auth-held.finish').exists())
                process.stdin.write(kwargs['input'][1:]); process.stdin.close(); process.stdin = None
                stdout, stderr = process.communicate(timeout=5)
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            subject.device = device; subject.finish_held_driver()
            final = root / 'no_backup/owned-auth-held.finish'
            self.assertEqual(final.read_bytes(), b'finish\n'); self.assertEqual(len(calls), 1)
            self.assertFalse((root / 'no_backup/owned-auth-held.finish.tmp').exists())
            script = self.actual_script(calls[0])
            again = subprocess.run([str(self.shell_path()), '-c', script], cwd=root, input=b'finish\n', capture_output=True, timeout=5)
            self.assertNotEqual(again.returncode, 0); self.assertEqual(final.read_bytes(), b'finish\n')

    def test_wrong_same_length_staging_bytes_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); subject = self.subject(root)
            subject.device = lambda *args, **kwargs: subprocess.run([str(self.shell_path()), '-c', self.actual_script(args)],
                cwd=root, input=b'finishx', capture_output=True, timeout=5)
            with self.assertRaisesRegex(RuntimeError, 'FINISH_PUBLISH_FAILED'): subject.finish_held_driver()
            self.assertFalse((root / 'no_backup/owned-auth-held.finish').exists())
            self.assertEqual((root / 'no_backup/owned-auth-held.finish.tmp').read_bytes(), b'finishx')

    def test_timeout_preserves_unknown_original_without_retry_or_false_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); subject = self.subject(root)
            subject.device = Mock(side_effect=subprocess.TimeoutExpired('original command', 15)); subject.shell = Mock()
            with self.assertRaises(subprocess.TimeoutExpired): subject.finish_held_driver()
            subject.device.assert_called_once(); subject.shell.assert_not_called()
            import json
            receipt = json.loads((subject.output / 'held-finish-result.json').read_bytes())
            self.assertIsNone(receipt['returncode']); self.assertTrue(receipt['unknown_result']); self.assertFalse(receipt['automatic_retry'])


if __name__ == '__main__': unittest.main()
