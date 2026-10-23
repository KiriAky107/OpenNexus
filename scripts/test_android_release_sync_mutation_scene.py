"""Actual closed mutation-entry/old-entry admission; no device, network or product build."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_android_release_auth_scene import STUBS

ROOT=Path(__file__).resolve().parent


class ReleaseSyncMutationEntry(unittest.TestCase):
    def test_actual_API36_and_closed_entry_policy(self):
        suffix='.exe' if os.name=='nt' else ''
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        self.assertTrue(javac.is_file() and java.is_file() and sdk.is_file(),'actual JDK/API36 required')
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={p.relative_to(ROOT.parent).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources}
        commands=[]
        def run(argv):
            r=subprocess.run(argv,capture_output=True,timeout=60)
            commands.append(dict(argv=argv,returncode=r.returncode,stdout=r.stdout.decode('utf-8','replace'),stderr=r.stderr.decode('utf-8','replace')))
            self.assertEqual(r.returncode,0,commands[-1]['stderr']);return r
        with tempfile.TemporaryDirectory(prefix='opennexus-mutation-entry-') as folder:
            root=Path(folder);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
            run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
            paths=[]
            for name,value in STUBS.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
            checks=('ReleaseSyncMutationEntryCheck','ReleaseSyncEntryCheck')
            run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),
                *paths,*(str(ROOT/('android-runtime-tests/'+name+'.java')) for name in checks)])
            mutation=run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseSyncMutationEntryCheck'])
            self.assertIn(b'RELEASE_SYNC_MUTATION_ENTRY_OK accepted=2 refused=26 policies=38 actual_Android_mutations=0',mutation.stdout)
            old=run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseSyncEntryCheck'])
            self.assertIn(b'RELEASE_SYNC_ENTRY_OK accepted=6 refused=24 package_checks=12 actual_Android_sync=0',old.stdout)
            for path in sources:self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),inventory[path.relative_to(ROOT.parent).as_posix()]['sha256'])
            print('RELEASE_SYNC_MUTATION_API36_PROOF '+json.dumps(dict(passed=True,actual_API36_driver_inputs=len(sources),actual_compile_inputs=inventory,
                commands=commands,SDK_jar=str(sdk),SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),compiler_max_heap_MiB=128,JVM_max_heap_MiB=64,
                actual_Android_mutation_or_image_or_desktop_verified=False)))

    def test_separate_self_target_entry_and_existing_permissions(self):
        root=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot();ns='{http://schemas.android.com/apk/res/android}'
        entries=[v for v in root.findall('instrumentation') if v.get(ns+'name')=='cc.kronecker.opennexus.testdriver.ReleaseSyncMutationRuntimeProbe']
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0].get(ns+'targetPackage'),'cc.kronecker.opennexus.testdriver')
        self.assertEqual(entries[0].get(ns+'functionalTest'),'false')
        self.assertEqual([v.get(ns+'name') for v in root.findall('uses-permission')],['cc.kronecker.opennexus.testime.permission.CONTROL'])


if __name__=='__main__':unittest.main()
