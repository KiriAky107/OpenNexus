"""Focused real API36 entry compilation, controlled create-getter chronology.

Compiler and controlled class output stay in RAM. No device or GUI is run.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest

from test_android_quota_private_gui_api import STUBS


ROOT = Path(__file__).resolve().parent
SOURCES = ('AuthRuntimeProbe CompositionIme ImeTargetPolicy OriginalAtomicWrite '
    'OriginalAuthSignal OriginalCanvasGraph OriginalCanvasMotion OriginalCanvasProperty '
    'OriginalConflictScenario OriginalExternalUri OriginalFileActions OriginalLongNoteScenario '
    'OriginalMarkdownAccessibility OriginalNoteSearch OriginalProductPairScenario '
    'OriginalStorageScenario OriginalSyncImageDiagnostic OriginalSyncImageGate '
    'OriginalSyncImageRegion ReleaseQuotaLoginRuntimeProbe ReleaseQuotaSeedRuntimeProbe '
    'ReleaseRuntimeProbe ReleaseScene ReleaseSyncImageRuntimeProbe ReleaseSyncReadRuntimeProbe '
    'ReleaseSyncReadScene RuntimeProbe TargetStability TouchViewport').split()
CASES = ('phase-getter-reentry', 'size-getter-reentry', 'context-getter-reentry',
         'target-getter-reentry', 'package-getter-reentry', 'private-getter-failure-aborts',
         'phase-captured-once-and-success')
LEGACY = ('strict-arguments', 'own-driver-context-and-target', 'argument-alias-isolated',
          'duplicate-create-and-early-start')


def replace_once(source, before, after):
    assert source.count(before) == 1
    return source.replace(before, after)


def controlled_sources():
    values = dict(STUBS)
    name = 'android/os/Bundle.java'
    values[name] = replace_once(values[name], 'public boolean fail;public Runnable hook;',
                               'public boolean fail;public Runnable hook,sizeHook;public int gets;')
    values[name] = replace_once(values[name], 'public int size(){',
        'public int size(){if(sizeHook!=null){Runnable once=sizeHook;sizeHook=null;once.run();}')
    values[name] = replace_once(values[name], 'public Object get(String k){',
                               'public Object get(String k){gets++;')
    values['android/content/Context.java'] = '''package android.content;
import cc.kronecker.opennexus.testdriver.Control;
public class Context {private final String name;public Context(String n){name=n;}
 public String getPackageName(){Control.packageReads++;
  if(Control.packageReads==Control.packageAt && Control.packageAction!=null){
   Runnable once=Control.packageAction;Control.packageAction=null;once.run();}return name;}}
'''
    name = 'android/app/Instrumentation.java'
    values[name] = replace_once(values[name], 'public android.content.Context getContext(){',
        'public android.content.Context getContext(){Control.contextReads++;'
        'if(Control.contextAction!=null){Runnable once=Control.contextAction;Control.contextAction=null;once.run();}')
    values[name] = replace_once(values[name], 'public android.content.Context getTargetContext(){',
        'public android.content.Context getTargetContext(){Control.targetReads++;'
        'if(Control.targetAction!=null){Runnable once=Control.targetAction;Control.targetAction=null;once.run();}')
    name = 'cc/kronecker/opennexus/testdriver/Control.java'
    values[name] = replace_once(values[name], 'public static void reset(){',
        'public static int contextReads,targetReads,packageReads,packageAt;'
        'public static Runnable contextAction,targetAction,packageAction;'
        'public static void reset(){contextReads=targetReads=packageReads=packageAt=0;'
        'contextAction=targetAction=packageAction=null;')
    for name in ('ReleaseQuotaPrivateGuiCheck', 'ReleaseQuotaPrivateCreateCheck'):
        values['cc/kronecker/opennexus/testdriver/' + name + '.java'] = (
            ROOT/'android-runtime-tests'/(name + '.java')).read_text('utf-8')
    return values


class PrivateCreateAPI(unittest.TestCase):
    case_names = CASES
    legacy_names = LEGACY

    def test_private_create_getters_on_actual_API36_entries(self):
        selected, legacy = tuple(self.case_names), tuple(self.legacy_names)
        self.assertTrue(selected and set(selected) <= set(CASES) and set(legacy) <= set(LEGACY))
        sdk = Path('C:/Users/KiriAky/AppData/Local/Android/Sdk/platforms/android-36/android.jar')
        java = Path('E:/java21/bin/java.exe')
        self.assertTrue(sdk.is_file() and java.is_file())
        sources = [ROOT/'android-runtime'/(name + '.java') for name in SOURCES]
        frozen = {p: p.read_bytes() for p in sources}
        payload = bytearray(struct.pack('>II', 0x4f4e5847, 9))
        for name, source in controlled_sources().items():
            for value in (name, source):
                raw = value.encode('utf-8');payload.extend(struct.pack('>I', len(raw)));payload.extend(raw)
        command = [java, '-Xmx192m', '-XX:-UsePerfData', '-Dfile.encoding=UTF-8',
            '-Duser.language=en', '-Duser.country=US',
            '-Donx.private_gui_check=ReleaseQuotaPrivateCreateCheck',
            '-Donx.private_create_checks=' + ','.join(selected)]
        if legacy:
            command.append('-Donx.private_gui_checks=' + ','.join(legacy))
        command += ['--source', '21', ROOT/'android-runtime-tests/ReleaseQuotaPrivateGuiMemoryCheck.java',
                    sdk, str(len(sources)), *sources]
        environment = os.environ.copy()
        for name in ('CLASSPATH', 'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS'):
            environment.pop(name, None)
        result = subprocess.run([str(p) for p in command], cwd=ROOT.parent, input=bytes(payload),
                                capture_output=True, timeout=120, env=environment)
        self.assertTrue(all(p.read_bytes() == raw for p, raw in frozen.items()))
        type(self).last_result = dict(returncode=result.returncode,
            stdout=result.stdout.decode('utf-8', 'replace'), stderr=result.stderr.decode('utf-8', 'replace')[:6000],
            selected=list(selected), legacy=list(legacy), API36_sources=[str(p) for p in sources])
        self.assertEqual(result.returncode, 0, type(self).last_result['stderr'])
        reports = [json.loads(line) for line in result.stdout.decode('utf-8').splitlines()]
        self.assertEqual(len(reports), 3 if legacy else 2)
        self.assertEqual(reports[0]['actual_bodies'], list(selected))
        if legacy:
            self.assertEqual(reports[1]['actual_bodies'], list(legacy))
        for value in reports[:-1]:
            self.assertTrue(value['passed'])
            self.assertEqual(value['checks'], len(value['actual_bodies']))
            self.assertFalse(value['actual_shared_UI_body_executed'])
            self.assertFalse(value['actual_device_or_Native'])
        self.assertEqual(reports[-1]['API36_source_count'], len(sources))
        self.assertTrue(reports[-1]['API36_new_entry_bytecode_reused'])
        self.assertEqual(reports[-1]['filesystem_outputs'], 0)
        type(self).summary = dict(reports=reports, Python_bodies=1, JVM_processes=1,
            actual_device_or_Native=False, filesystem_outputs=0, frozen_source_bytes_unchanged=True)


if __name__ == '__main__':
    unittest.main()
