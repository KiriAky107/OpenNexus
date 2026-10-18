from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest,xml.etree.ElementTree as ET,yaml
from android_runtime_probe import Probe
from android_workflow_gate import admitted
import android_external_probe as subject


class ExternalWorkflow(unittest.TestCase):
    def test_scope_reuses_fixed_apk_and_never_builds_or_replays_old_components(self):
        flow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for reuse in ('','39148381613'):
            for job in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
                self.assertFalse(admitted(flow['jobs'][job]['if'],'external-links',reuse=reuse,build='skipped'))
        self.assertTrue(admitted(flow['jobs']['emulator-runtime']['if'],'external-links',reuse='39148381613',build='skipped'))
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='external-links');probe.device=Mock();probe.shell=Mock()
        probe.native_keystore_tests();probe.device.assert_not_called();probe.shell.assert_not_called()
        probe.boot=Mock();probe.phase=Mock()
        with patch.object(subject,'run_external') as run:probe.run()
        probe.boot.assert_called_once();run.assert_called_once_with(probe);probe.phase.assert_not_called()

    def test_test_receiver_is_only_reserved_https_with_no_auto_verification_or_network_permission(self):
        root=ET.parse(Path(__file__).parent/'android-runtime/AndroidManifest.xml').getroot();key='{http://schemas.android.com/apk/res/android}'
        receivers=root.findall('./application/activity');self.assertEqual(len(receivers),1)
        receiver=receivers[0];self.assertEqual(receiver.attrib[key+'name'],'.ExternalIntentReceiver')
        filters=receiver.findall('intent-filter');self.assertEqual(len(filters),1);filter=filters[0]
        self.assertEqual(filter.attrib[key+'autoVerify'],'false')
        self.assertEqual([n.attrib[key+'name'] for n in filter.findall('action')],['android.intent.action.VIEW'])
        self.assertEqual({n.attrib[key+'name'] for n in filter.findall('category')},{'android.intent.category.DEFAULT','android.intent.category.BROWSABLE'})
        self.assertEqual([n.attrib for n in filter.findall('data')],[{key+'scheme':'https',key+'host':'owned-android-reference.invalid'}])
        self.assertNotIn('android.permission.INTERNET',[n.attrib[key+'name'] for n in root.findall('uses-permission')])

if __name__=='__main__':unittest.main()
