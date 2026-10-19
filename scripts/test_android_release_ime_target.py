"""Execute the actual CI IME target policy and InputConnection method on the JVM."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent


class ReleaseImeTargetBinding(unittest.TestCase):
    def test_actual_java_refuses_cross_target_password_and_unbound_input(self):
        sdk=Path(os.environ.get('ANDROID_HOME','C:/Users/KiriAky/AppData/Local/Android/Sdk'))/'platforms/android-36/android.jar'
        java_home=Path(os.environ.get('JAVA_HOME','E:/java21'))
        javac=java_home/('bin/javac.exe' if os.name=='nt' else 'bin/javac')
        java=java_home/('bin/java.exe' if os.name=='nt' else 'bin/java')
        if not(sdk.is_file() and javac.is_file() and java.is_file()):self.skipTest('API36 and JDK required for actual Java admission check')
        actual=(ROOT/'android-runtime/ime/CompositionInputMethod.java').read_text('utf-8')
        methods=actual[actual.index('    boolean execute('):actual.rfind('\n}')]
        source=(ROOT/'android-runtime-tests/ActualInputConnectionCheck.java').read_text('utf-8').replace('// ACTUAL_METHODS',methods)
        with tempfile.TemporaryDirectory(prefix='opennexus-ime-target-') as folder:
            folder=Path(folder);harness=folder/'ActualInputConnectionCheck.java';harness.write_text(source,'utf-8')
            sources=[ROOT/'android-runtime/ImeTargetPolicy.java',ROOT/'android-runtime-tests/ImeTargetPolicyCheck.java',harness]
            subprocess.run([str(javac),'-J-Xmx128m','-encoding','UTF-8','-classpath',str(sdk),'-d',str(folder),*map(str,sources)],capture_output=True,check=True,timeout=60)
            for cls,line in [('ImeTargetPolicyCheck','IME_TARGET_POLICY_OK 20'),('ActualInputConnectionCheck','ACTUAL_IME_CONNECTION_OK 34')]:
                result=subprocess.run([str(java),'-Xmx64m','-cp',str(folder),'cc.kronecker.opennexus.testcontract.'+cls],capture_output=True,check=True,timeout=20)
                self.assertIn(line,result.stdout.decode('utf-8'))


if __name__=='__main__':unittest.main()
