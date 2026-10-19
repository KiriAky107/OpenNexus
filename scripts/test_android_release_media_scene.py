"""Execute the closed stable-GUI phase contract, never an Android device."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent


class ReleaseMediaScene(unittest.TestCase):
    def test_actual_java_admits_only_fixed_stable_media_phases(self):
        home=Path(os.environ.get('JAVA_HOME','E:/java21'))
        suffix='.exe' if os.name=='nt' else ''
        javac=home/('bin/javac'+suffix);java=home/('bin/java'+suffix)
        if not javac.is_file() or not java.is_file():self.skipTest('JDK required for actual phase contract')
        with tempfile.TemporaryDirectory(prefix='opennexus-release-media-') as folder:
            subprocess.run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-d',folder,
                str(ROOT/'android-runtime/ReleaseScene.java'),str(ROOT/'android-runtime-tests/ReleaseSceneCheck.java')],
                capture_output=True,check=True,timeout=30)
            result=subprocess.run([str(java),'-Xmx64m','-cp',folder,'cc.kronecker.opennexus.testdriver.ReleaseSceneCheck'],
                capture_output=True,check=True,timeout=15)
            self.assertIn('RELEASE_SCENE_CONTRACT_OK phases=12 actual_Android_GUI_or_upgrade_verified=0',result.stdout.decode('ascii'))


if __name__=='__main__':unittest.main()
