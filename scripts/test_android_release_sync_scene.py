"""Actual signed Sync entry on a small SDK/JVM harness, with no UI or network."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

import test_android_release_auth_scene as auth_entry

ROOT=Path(__file__).resolve().parent


class ReleaseSyncEntry(unittest.TestCase):
    def compile(self,args):
        result=subprocess.run(args,capture_output=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))

    def test_actual_fixed_target_entry_and_frozen_review_owner(self):
        suffix='.exe' if os.name=='nt' else ''
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac=jdk/('javac'+suffix);java=jdk/('java'+suffix)
        if not sdk.is_file() or not javac.is_file():self.skipTest('JDK and API36 required')
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={path.relative_to(ROOT.parent).as_posix():dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in sources}
        with tempfile.TemporaryDirectory(prefix='opennexus-release-sync-entry-') as folder:
            root=Path(folder);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
            self.compile([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
            paths=[]
            for name,value in auth_entry.STUBS.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
            self.compile([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),
                '-d',str(harness),*paths,str(ROOT/'android-runtime-tests/ReleaseSyncEntryCheck.java'),
                str(ROOT/'android-runtime-tests/OriginalSyncPreviewCheck.java'),
                str(ROOT/'android-runtime-tests/OriginalCycleInputCheck.java'),
                str(ROOT/'android-runtime-tests/OriginalSyncSettleCheck.java')])
            result=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseSyncEntryCheck'],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            self.assertIn(b'RELEASE_SYNC_ENTRY_OK accepted=6 refused=24 package_checks=12 actual_Android_sync=0',result.stdout)
            preview=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.OriginalSyncPreviewCheck'],capture_output=True,timeout=15)
            self.assertEqual(preview.returncode,0,preview.stderr.decode('utf-8','replace'))
            self.assertIn(b'ORIGINAL_SYNC_PREVIEW_OK checks=7 actual_Android_preview=0 input_replayed=0',preview.stdout)
            cycle=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.OriginalCycleInputCheck'],capture_output=True,timeout=15)
            self.assertEqual(cycle.returncode,0,cycle.stderr.decode('utf-8','replace'))
            self.assertIn(b'ORIGINAL_CYCLE_INPUT_OK checks=7 actual_Android_or_Native_cycle=0',cycle.stdout)
            settle=subprocess.run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.OriginalSyncSettleCheck'],capture_output=True,timeout=15)
            self.assertEqual(settle.returncode,0,settle.stderr.decode('utf-8','replace'))
            self.assertIn(b'ORIGINAL_SYNC_SETTLE_OK checks=6 default_ms=150 sync_ms=600 actual_Android_cause_confirmed=0',settle.stdout)
            for path in sources:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),inventory[path.relative_to(ROOT.parent).as_posix()]['sha256'])
            # A caller may retain this public source/compile-only record from
            # unittest stdout; no credentials or device values are present.
            print('RELEASE_SYNC_API36_SOURCE_PROOF '+json.dumps(dict(passed=True,SDK_jar=str(sdk),
                SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),actual_compile_inputs=inventory,
                compiler_max_heap_MiB=128,actual_JVM_entry_stdout=result.stdout.decode('ascii').strip(),
                actual_JVM_preview_wait_stdout=preview.stdout.decode('ascii').strip(),
                actual_JVM_cycle_input_stdout=cycle.stdout.decode('ascii').strip(),
                actual_JVM_settle_stdout=settle.stdout.decode('ascii').strip(),
                actual_API36_driver_inputs=len(sources),actual_Android_sync_verified=False)))

    def test_separate_driver_manifest_only_and_NO_product_permission_change(self):
        root=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot();ns='{http://schemas.android.com/apk/res/android}'
        matches=[e for e in root.findall('instrumentation') if e.get(ns+'name')=='cc.kronecker.opennexus.testdriver.ReleaseSyncRuntimeProbe']
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0].get(ns+'targetPackage'),'cc.kronecker.opennexus.testdriver')
        self.assertEqual(matches[0].get(ns+'functionalTest'),'false')


if __name__=='__main__':unittest.main()
