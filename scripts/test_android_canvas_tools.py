"""Proof admission and cleanup for the new fixed-APK Canvas-only UI scope."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

import yaml

from android_runtime_probe import NODE, Probe, validate_canvas_tools
from test_android_desktop_workflow import admitted


def view(width=390, *, expanded=True, menu=False):
    box = {'visible': True, 'left': 4, 'right': width-4, 'top': 60, 'bottom': 300, 'width': width-8, 'height': 240}
    return {'available': True, 'styles': {'canvasTools': {'width': width, 'height': 800,
        'rootWidth': width, 'rootScrollWidth': width, 'host': box,
        'trigger': box if width <= 640 else None,
        'area': dict(box, visible=expanded, position='absolute' if width <= 640 else 'static'),
        'menu': box if menu else None,
        'targets': [{'visible': True, 'width': 44, 'height': 44}], 'colors': [{'color': 'rgb(1, 2, 3)'}]}}}


class CanvasTools(unittest.TestCase):
    def tablet_probe(self, directory):
        probe = Probe.__new__(Probe); probe.output = Path(directory)
        probe.args = SimpleNamespace(commit='fixed-apk', tools_commit='fixed-tools', build_origin='original-build')
        probe.binding = {'sha256':'a'*64}
        probe.enable_test_ime = Mock(); probe.force_stop = Mock(); probe.restore_tablet_display = Mock()
        original = json.dumps({'nodes':[{'text':NODE}]}).encode()
        probe.original = Mock(return_value=original); probe.draft_rows = Mock(return_value=[('original', 3, 3)])
        probe.shell = Mock(return_value=SimpleNamespace(stdout=b'123'))
        events = []
        probe.tablet_display = lambda w, h: events.append(('display', w, h))
        def phase(name, **kwargs):
            events.append(('phase', name, kwargs))
            if name != 'canvas-tools-seed':
                width = 800 if name.endswith('800') else 1280
                (probe.output/(name+'-computed-styles.json')).write_text(json.dumps(view(width)), 'utf-8')
        probe.phase = phase
        return probe, events, original

    def test_remaining_tablet_scope_opens_after_initial_configuration_without_phone_replay(self):
        workflow = yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'), Loader=yaml.BaseLoader)
        self.assertIn('canvas-tablet', workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        self.assertEqual(tuple(admitted(workflow['jobs'][name]['if'], 'canvas-tablet', reuse='39044670347')
            for name in ('debug-apk','emulator-runtime','desktop-startup')), (False, True, False))
        probe = Probe.__new__(Probe); probe.args = SimpleNamespace(scope='canvas-tablet')
        probe.binding = {'native_tests': {'present':True}}; probe.device = Mock()
        probe.native_keystore_tests(); probe.device.assert_not_called()
        probe.boot = Mock(); probe.run_canvas_tablet = Mock(); probe.phase = Mock()
        probe.run(); probe.boot.assert_called_once(); probe.run_canvas_tablet.assert_called_once(); probe.phase.assert_not_called()
        with tempfile.TemporaryDirectory() as work:
            probe, events, _ = self.tablet_probe(work); probe.run_canvas_tablet()
            self.assertEqual(events, [('phase','canvas-tools-seed',{}), ('display',1600,2400),
                ('phase','canvas-tools-tablet-open-800',{'keep_running':True}), ('display',2560,1600),
                ('phase','canvas-tools-tablet-1280',{'continue_process':True,'keep_running':True})])
            proof = json.loads((probe.output/'canvas-tablet-proof.json').read_text('utf-8'))
            self.assertTrue(proof['initial_800px_cold_open_explicit']); self.assertEqual(proof['same_PID_800_to_1280'],123)
            self.assertFalse(proof['phone_Back_menu_theme_cases_replayed']); probe.restore_tablet_display.assert_called_once()

    def test_remaining_tablet_rejects_new_process_or_changed_originals_and_always_restores_display(self):
        for fault in ('PID','file','draft'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as work:
                probe, _, original = self.tablet_probe(work)
                if fault == 'PID': probe.shell.side_effect = [SimpleNamespace(stdout=b'123'),SimpleNamespace(stdout=b'456')]
                elif fault == 'file': probe.original.side_effect = [original,original+b' ']
                else: probe.draft_rows.side_effect = [[('original',3,3)],[('changed',4,3)]]
                with self.assertRaises(RuntimeError): probe.run_canvas_tablet()
                probe.restore_tablet_display.assert_called_once()
                self.assertFalse((probe.output/'canvas-tablet-proof.json').exists())

    def test_measurements_require_visible_bounded_44_pixel_controls_and_actual_menu_state(self):
        for width in (390, 800, 1280):
            report = view(width)
            self.assertIs(validate_canvas_tools(report, width=width if width > 640 else None), report['styles']['canvasTools'])
        validate_canvas_tools(view(expanded=False), expanded=False)
        validate_canvas_tools(view(menu=True), menu=True)
        variants = []
        for path, value in [(('targets', 0, 'width'), 43), (('targets', 0, 'height'), 43),
                (('rootScrollWidth',), 400), (('area', 'right'), 400), (('area', 'bottom'), 802),
                (('trigger', 'visible'), False), (('area', 'position'), 'static'), (('menu',), {'visible': True})]:
            report = view(); target = report['styles']['canvasTools']
            for part in path[:-1]: target = target[part]
            target[path[-1]] = value; variants.append(report)
        for report in variants:
            with self.subTest(report=report), self.assertRaises(RuntimeError): validate_canvas_tools(report)
        with self.assertRaises(RuntimeError): validate_canvas_tools({'available': False})

    def test_narrow_scope_does_not_launch_auth_sync_or_replay_old_editor_and_keystore_cases(self):
        workflow = yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'), Loader=yaml.BaseLoader)
        self.assertIn('canvas-tools', workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])
        actual = tuple(admitted(workflow['jobs'][name]['if'], 'canvas-tools', reuse='39035076129')
            for name in ('debug-apk','emulator-runtime','desktop-startup'))
        self.assertEqual(actual, (False, True, False))
        probe = Probe.__new__(Probe); probe.args = SimpleNamespace(scope='canvas-tools')
        probe.binding = {'native_tests': {'present': True}}; probe.device = Mock()
        probe.native_keystore_tests(); probe.device.assert_not_called()
        probe.boot = Mock(); probe.run_canvas_tools = Mock(); probe.phase = Mock()
        probe.run(); probe.boot.assert_called_once(); probe.run_canvas_tools.assert_called_once(); probe.phase.assert_not_called()

    def test_owned_display_is_restored_when_new_tablet_geometry_fails_without_repeating_seed(self):
        with tempfile.TemporaryDirectory() as work:
            probe = Probe.__new__(Probe); probe.output = Path(work)
            probe.enable_test_ime = Mock(); probe.force_stop = Mock(); probe.tablet_display = Mock(); probe.restore_tablet_display = Mock()
            probe.original = Mock(return_value=json.dumps({'nodes':[{'text':NODE}]}).encode())
            probe.draft_rows = Mock(return_value=[('original', 2, 2)])
            probe.shell = Mock(return_value=SimpleNamespace(stdout=b'123'))
            phases = []
            def phase(name, **kwargs):
                phases.append(name)
                report = view(800 if name.startswith('canvas-tools-tablet-') else 390,
                    expanded=name != 'canvas-tools-phone-back-panel', menu=name == 'canvas-tools-phone-menu')
                report['styles']['canvasTools']['colors'] = [name]
                if name.startswith('canvas-tools-tablet-'): report['available'] = False
                (probe.output/(name+'-computed-styles.json')).write_text(json.dumps(report), 'utf-8')
            probe.phase = phase
            with self.assertRaises(RuntimeError): probe.run_canvas_tools()
            self.assertEqual(phases.count('canvas-tools-seed'), 1)
            self.assertFalse(any(name in ('markdown','rich-markdown','canvas','canvas-drag','export') for name in phases))
            probe.tablet_display.assert_called_once_with(1600, 2400); probe.restore_tablet_display.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
