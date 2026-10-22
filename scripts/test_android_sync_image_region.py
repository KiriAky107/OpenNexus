"""Small actual API36 compile/JVM crop admission; no device or image capture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent


class SyncImageRegionChecks(unittest.TestCase):
    def test_actual_API36_and_JVM_region_pipeline_and_main_argv_privacy(self):
        suffix='.exe' if os.name=='nt' else ''
        jdk=Path(os.environ.get('JAVA_HOME','E:/java21'))/'bin'
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        javac,java=jdk/('javac'+suffix),jdk/('java'+suffix)
        self.assertTrue(javac.is_file() and sdk.is_file(),'actual SDK/JDK required')
        sources=sorted((ROOT/'android-runtime').glob('*.java'))
        inventory={p.relative_to(ROOT.parent).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources}
        commands=[]
        def run(argv,expected=0):
            value=subprocess.run(argv,capture_output=True,timeout=60)
            commands.append(dict(argv=argv,returncode=value.returncode,stdout=value.stdout.decode('utf-8','replace'),stderr=value.stderr.decode('utf-8','replace')))
            self.assertEqual(value.returncode,expected,commands[-1]['stderr']);return value
        with tempfile.TemporaryDirectory(prefix='opennexus-crop-region-') as directory:
            classes=Path(directory)/'classes';classes.mkdir()
            run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(classes),*map(str,sources),str(ROOT/'android-runtime-tests/OriginalSyncImageRegionCheck.java')])
            result=run([str(java),'-Xmx64m','-cp',str(classes)+os.pathsep+str(sdk),'cc.kronecker.opennexus.testdriver.OriginalSyncImageRegionCheck'])
            self.assertIn(b'ORIGINAL_SYNC_IMAGE_REGION_OK refused=25 flow_checks=8',result.stdout)
            main=run([str(java),'-Xmx64m','-cp',str(classes)+os.pathsep+str(sdk),'cc.kronecker.opennexus.testdriver.OwnedImageCrop','SECRET_FORBIDDEN_ARGV'],1)
            self.assertEqual(main.stdout,b'')
            self.assertEqual(main.stderr.strip(),b'OWNED_IMAGE_CROP_FAILED stage=request no_fallback=1')
            for path in sources:self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),inventory[path.relative_to(ROOT.parent).as_posix()]['sha256'])
            print('SYNC_IMAGE_REGION_API36_PROOF '+json.dumps(dict(passed=True,actual_API36_driver_inputs=len(sources),actual_compile_inputs=inventory,
                SDK_jar=str(sdk),SDK_jar_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(),commands=commands,compiler_max_heap_MiB=128,
                real_JVM_admission_refusals=25,real_JVM_flow_checks=8,actual_Android_pixels_verified=False)))

    def test_helper_is_not_registered_as_a_product_or_driver_GUI_entry(self):
        manifest=ET.parse(ROOT/'android-runtime/AndroidManifest.xml').getroot()
        self.assertNotIn('OwnedImageCrop',ET.tostring(manifest,encoding='unicode'))
        source=(ROOT/'android-runtime/OwnedImageCrop.java').read_text('utf-8')
        for forbidden in ('takeScreenshot(', 'adoptShellPermissionIdentity(', 'setHiddenApiExemptions(', 'grantRuntimePermission(', 'createBitmap('):
            self.assertNotIn(forbidden,source)
        self.assertIn('getMethod("setSourceCrop",Rect.class)',source)
        self.assertIn('getField("mSourceCrop")',source)
        self.assertIn('getField("mUid")',source)
        self.assertIn('getMethod("captureDisplay",int.class',source)


if __name__=='__main__':unittest.main()
