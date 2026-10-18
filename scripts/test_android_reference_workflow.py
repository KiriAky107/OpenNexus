from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest,yaml
from android_runtime_probe import Probe
from android_workflow_gate import admitted
import android_reference_probe as subject

class ReferenceWorkflowBoundaries(unittest.TestCase):
    def test_reference_scope_reuses_only_the_original_fixed_build(self):
        flow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        scope='local-references'
        for reuse in ('','39148381613'):
            for job in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
                self.assertFalse(admitted(flow['jobs'][job]['if'],scope,reuse=reuse,build='skipped'))
        self.assertTrue(admitted(flow['jobs']['emulator-runtime']['if'],scope,reuse='39148381613',build='skipped'))

    def test_reference_mode_never_replays_native_suites_or_old_lifecycle_phases(self):
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='local-references');probe.device=Mock();probe.shell=Mock()
        probe.native_keystore_tests();probe.device.assert_not_called();probe.shell.assert_not_called()
        probe.boot=Mock();probe.phase=Mock()
        with patch.object(subject,'run_references') as run:probe.run()
        probe.boot.assert_called_once();run.assert_called_once_with(probe);probe.phase.assert_not_called()

if __name__=='__main__':unittest.main()
