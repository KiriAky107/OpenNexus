"""Workflow parser checks run with other workflow tooling, not in the AVD probe."""
from pathlib import Path
import unittest
import yaml

from test_android_desktop_workflow import admitted


class FileActionWorkflow(unittest.TestCase):
    def test_partial_file_scope_reuses_a_fixed_package_and_cannot_start_a_debug_build(self):
        workflow = yaml.load((Path(__file__).resolve().parents[1] / '.github/workflows/android.yml').read_text('utf-8'), Loader=yaml.BaseLoader)
        for scope in ('file-actions','file-actions-remaining'):
            self.assertIn(scope, workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
            for reuse in ('','39078195102'):
                self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'],scope,reuse=reuse))
            self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'],scope,reuse='39078195102',build='skipped'))
            self.assertFalse(admitted(workflow['jobs']['emulator-runtime']['if'],scope,reuse='',build='skipped'))

        for reuse in ('', '39078195102'):
            self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'], 'file-actions', reuse=reuse))
        self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'], 'file-actions', reuse='39078195102', build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['emulator-runtime']['if'], 'file-actions', reuse='', build='skipped'))


if __name__ == '__main__': unittest.main()
