"""Actual API36/new closed-entry JVM and held-image gate checks, no device actions."""
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


class SyncImageGUIChecks(unittest.TestCase):
    def test_actual_API36_entry_and_held_geometry_finish_guards(self):
        suffix='.exe' if os.name=='nt' else ''
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        self.assertTrue(javac.is_file() and sdk.is_file(),'actual SDK/JDK required')
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={p.relative_to(ROOT.parent).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources}
        commands=[]
        def run(argv):
            value=subprocess.run(argv,capture_output=True,timeout=60)
            commands.append(dict(argv=argv,returncode=value.returncode,stdout=value.stdout.decode('utf-8','replace'),stderr=value.stderr.decode('utf-8','replace')))
            self.assertEqual(value.returncode,0,commands[-1]['stderr']);return value
        with tempfile.TemporaryDirectory(prefix='opennexus-image-gui-') as folder:
            root=Path(folder);actual=root/'actual';actual.mkdir();harness=root/'harness';harness.mkdir()
            run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(actual),*map(str,sources)])
            paths=[]
            for name,value in STUBS.items():
                if name=='android/app/Instrumentation.java':value=value.replace('No credential input in local admission check','SECRET_CONTEXT_UI_NEVER_EXPORT')
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,'utf-8');paths.append(str(path))
            checks=('ReleaseSyncImageEntryCheck','OriginalSyncImageGateCheck','OriginalSyncImageRegionCheck')
            run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(actual)+os.pathsep+str(sdk),'-d',str(harness),
                *paths,*(str(ROOT/('android-runtime-tests/'+name+'.java')) for name in checks)])
            for name in checks:
                result=run([str(java),'-Xmx64m','-cp',os.pathsep.join(map(str,(harness,actual,sdk))),'cc.kronecker.opennexus.testdriver.'+name])
                self.assertIn(b'_OK ',result.stdout)
            for path in sources:self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),inventory[path.relative_to(ROOT.parent).as_posix()]['sha256'])
            print('SYNC_IMAGE_GUI_API36_PROOF '+json.dumps(dict(passed=True,actual_API36_driver_inputs=len(sources),actual_compile_inputs=inventory,
                SDK_jar=str(sdk),SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),commands=commands,compiler_max_heap_MiB=128,
                actual_Android_GUI_or_compositor_or_pixels_verified=False)))

    def test_separate_entry_and_driver_stays_capture_free(self):
        ns='{http://schemas.android.com/apk/res/android}';doc=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot()
        values=[e for e in doc.findall('instrumentation') if e.get(ns+'name')=='cc.kronecker.opennexus.testdriver.ReleaseSyncImageRuntimeProbe']
        self.assertEqual(len(values),1);self.assertEqual(values[0].get(ns+'targetPackage'),'cc.kronecker.opennexus.testdriver')
        self.assertEqual(values[0].get(ns+'functionalTest'),'false')
        text=(ROOT/'android-runtime/RuntimeProbe.java').read_text('utf-8')
        method=text[text.index('protected final void holdSignedSyncImage()'):text.index('/** Read only these fixed peer documents')]
        for forbidden in ('takeScreenshot(', 'OwnedImageCrop.main(', 'setText(', 'inputText(', 'save(', 'adoptShellPermissionIdentity(', 'executeShellCommand('):
            self.assertNotIn(forbidden,method)
        self.assertIn('gate.hold(',method);self.assertIn('OriginalAuthSignal.complete(finish)',method)
        self.assertIn('file.createNewFile()',method)
        scan=method[method.index('private OriginalSyncImageGate.Frame ownedImageFrame'):]
        self.assertLess(scan.index('node.isPassword())return null'),scan.index('label(node,ReleaseSyncReadScene.path'))


if __name__=='__main__':unittest.main()
