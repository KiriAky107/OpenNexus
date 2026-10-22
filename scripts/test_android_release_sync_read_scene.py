"""Actual API36 entry compilation and JVM admission; no device, network or product build."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_android_release_auth_scene import STUBS
from android_release_sync import PEER_NOTE, PEER_CANVAS
from android_release_sync_snapshot import ASSET

ROOT = Path(__file__).resolve().parent


class ReleaseSyncReadEntry(unittest.TestCase):
    def test_actual_API36_entry_and_fixture_admission(self):
        suffix = '.exe' if os.name == 'nt' else ''
        jdk = Path(os.environ.get('JAVA_HOME', 'E:/java21')) / 'bin'
        sdk = Path(os.environ.get('ANDROID_HOME', 'C:/Users/KiriAky/AppData/Local/Android/Sdk')) / 'platforms/android-36/android.jar'
        javac, java = jdk / ('javac' + suffix), jdk / ('java' + suffix)
        self.assertTrue(sdk.is_file() and javac.is_file(), 'API36 and JDK required for this source-only check')
        sources = sorted((ROOT / 'android-runtime').glob('*.java'))
        inventory = {p.relative_to(ROOT.parent).as_posix(): dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources}
        commands = []
        def run(argv, timeout=60):
            value = subprocess.run(argv, capture_output=True, timeout=timeout)
            commands.append(dict(argv=argv, returncode=value.returncode, stdout=value.stdout.decode('utf-8','replace'), stderr=value.stderr.decode('utf-8','replace')))
            self.assertEqual(value.returncode, 0, commands[-1]['stderr'])
            return value
        with tempfile.TemporaryDirectory(prefix='opennexus-release-sync-read-') as folder:
            root = Path(folder); actual=root/'actual'; actual.mkdir(); harness=root/'harness'; harness.mkdir()
            run([str(javac), '-J-Xmx128m', '-encoding', 'UTF-8', '-classpath', str(sdk), '-d', str(actual), *map(str,sources)])
            paths=[]
            for name, value in STUBS.items():
                if name == 'android/app/Instrumentation.java':
                    value=value.replace('No UI in local admission check', 'SECRET_UI_PASSWORD_SENTINEL_NEVER_EXPORT')
                path=root/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
            canvas=json.loads(PEER_CANVAS)
            fixture=root/'ReleaseSyncReadFixtures.java'
            fixture.write_text('package cc.kronecker.opennexus.testdriver; final class ReleaseSyncReadFixtures {\n'+
                ''.join('static final String '+name+'='+json.dumps(value, ensure_ascii=True)+';\n' for name,value in
                    [('MARKDOWN',PEER_NOTE.decode()),('CANVAS_TEXT',canvas['nodes'][0]['text']),('ASSET',ASSET)])+'}\n', 'utf-8')
            run([str(javac), '-J-Xmx128m', '-encoding', 'UTF-8', '-classpath', str(actual)+os.pathsep+str(sdk), '-d', str(harness),
                *paths, str(fixture), str(ROOT/'android-runtime-tests/ReleaseSyncReadEntryCheck.java')])
            result=run([str(java), '-Xmx64m', '-cp', os.pathsep.join(map(str,(harness,actual,sdk))),
                'cc.kronecker.opennexus.testdriver.ReleaseSyncReadEntryCheck'], 15)
            self.assertIn(b'RELEASE_SYNC_READ_ENTRY_OK accepted=2 refused=20 body_checks=10', result.stdout)
            for path in sources:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), inventory[path.relative_to(ROOT.parent).as_posix()]['sha256'])
            # Every command here is local compilation/JVM. No Android UI values
            # or credentials exist; retain this complete public-source evidence.
            print('RELEASE_SYNC_READ_API36_SOURCE_PROOF '+json.dumps(dict(passed=True, actual_compile_inputs=inventory,
                SDK_jar=str(sdk), SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(), commands=commands,
                compiler_max_heap_MiB=128, actual_API36_driver_inputs=len(sources),
                actual_Android_editor_verified=False, decoded_image_verified=False)))

    def test_new_entry_targets_only_driver_and_keeps_old_scope(self):
        ns='{http://schemas.android.com/apk/res/android}'
        manifest=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot()
        matches=[e for e in manifest.findall('instrumentation') if e.get(ns+'name')=='cc.kronecker.opennexus.testdriver.ReleaseSyncReadRuntimeProbe']
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0].get(ns+'targetPackage'),'cc.kronecker.opennexus.testdriver')
        self.assertEqual(matches[0].get(ns+'functionalTest'),'false')


if __name__ == '__main__': unittest.main()
