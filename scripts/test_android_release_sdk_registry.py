"""Small real SDK vendor schema/reader check; no SDK install or device."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPORARIES = []


class SDKRegistryVendor(unittest.TestCase):
    def test_actual_vendor_writer_schema_and_local_reader_with_closed_inputs(self):
        sdk = Path(os.environ.get('ANDROID_HOME', 'C:/Users/KiriAky/AppData/Local/Android/Sdk')) / 'cmdline-tools/latest'
        jdk = Path(os.environ.get('JAVA_HOME', 'E:/java21')) / 'bin'
        suffix = '.exe' if os.name == 'nt' else ''
        javac, java = jdk / ('javac' + suffix), jdk / ('java' + suffix)
        classpath = sdk / 'lib/avdmanager-classpath.jar'
        self.assertTrue(javac.is_file() and java.is_file() and classpath.is_file(), 'Actual SDK vendor libraries required')
        sources = [ROOT / 'scripts/android-sdk-tools/OwnedEmulatorRegistry.java',
                   ROOT / 'scripts/android-sdk-tests/OwnedEmulatorRegistryCheck.java']
        files = sources + [classpath, sdk / 'source.properties', sdk / 'lib/repository/tools.repository.jar',
                          sdk / 'lib/repository/repository-core.jar', sdk / 'lib/repository/librepo-common-2.lib.jar',
                          sdk / 'lib/repository/libgeneric-2.lib.jar']
        inventory = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        commands = []
        scope = dict(compiled=False, vendor_writer_and_schema_passed=False, vendor_local_reader_passed=False)
        main = 'cc.kronecker.opennexus.sdkinternal.OwnedEmulatorRegistry'
        properties = (b'Pkg.UserSrc=false\nPkg.Revision=37.2.12\nPkg.Path=emulator\n'
                      b'Pkg.Desc=Android Emulator\nPkg.BuildId=16428233')
        self.assertEqual(len(properties), 103)
        self.assertEqual(hashlib.sha256(properties).hexdigest(),
                         'ba13085f647fb8389a4d06a1de0e4104ac4e841733e50f67223ac230e1ce685a')
        def run(argv, data=None, *, passed=True):
            value = subprocess.run(argv, input=data, capture_output=True, timeout=40, check=False)
            commands.append(dict(argv=argv, stdin_bytes=len(data) if data is not None else 0,
                stdin_sha256=hashlib.sha256(data or b'').hexdigest(), returncode=value.returncode,
                stdout_base64=base64.b64encode(value.stdout).decode(), stderr_base64=base64.b64encode(value.stderr).decode()))
            self.assertEqual(value.returncode == 0, passed, value.stderr.decode('utf8', 'replace'))
            if not passed:
                self.assertEqual(value.stdout, b''); self.assertEqual(value.stderr, b'SDK_REGISTRY_VENDOR_GUARD\n')
            return value.stdout
        try:
            with tempfile.TemporaryDirectory(prefix='onx-sdk-registry-check-') as folder:
                TEMPORARIES.append(folder); temp = Path(folder); classes = temp / 'classes'; classes.mkdir()
                run([str(javac), '-J-Xmx128m', '-encoding', 'UTF-8', '-cp', str(classpath), '-d', str(classes), *map(str, sources)])
                scope['compiled'] = True
                launch = [str(java), '-Xmx64m', '-cp', str(classes) + os.pathsep + str(classpath)]
                fixture = temp / 'fixture'; fixture.mkdir()
                result = run(launch + [main + 'Check', str(fixture)])
                self.assertIn(b'SDK_REGISTRY_VENDOR_READER_OK metadata_only=1 SDK_install_or_runtime=0', result)
                scope['vendor_local_reader_passed'] = True
                xml = run(launch + [main, 'derive'], properties)
                scope['vendor_writer_and_schema_passed'] = True
                self.assertIn(b'<localPackage', xml)
                self.assertEqual(run(launch + [main, 'validate'], xml), b'SDK_REGISTRY_SCHEMA_MATCH\n')
                for data in (properties.replace(b'37.2.12', b'37.2.13'), properties + b'private=secret\n',
                             properties.replace(b'Pkg.BuildId=16428233', b''), properties + b'\n', b'x' * 65537):
                    run(launch + [main, 'derive'], data, passed=False)
                start, end = xml.index(b'<localPackage'), xml.index(b'</localPackage>') + len(b'</localPackage>')
                bad = [xml.replace(b'<major>37</major>', b'<major>38</major>'),
                    xml.replace(b'path="emulator"', b'path="other"'),
                    xml.replace(b'Android Emulator', b'Android Emulator private'),
                    xml.replace(b'genericDetailsType', b'privateType'),
                    xml[:end] + xml[start:end] + xml[end:],
                    xml.replace(b'</localPackage>', b'<private>secret</private></localPackage>'),
                    xml.replace(b'<revision>', b'<!--private--><revision>'),
                    xml.replace(b'?>', b'?><!DOCTYPE repository [<!ENTITY private SYSTEM "https://invalid.test/private">]>', 1)]
                for data in bad: run(launch + [main, 'validate'], data, passed=False)
                for args in ([], ['derive', '--path', '/private'], ['register']):
                    run(launch + [main, *args], properties, passed=False)
                for path in files: self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), inventory[str(path)])
        finally:
            print('SDK_REGISTRY_VENDOR_PROOF ' + json.dumps(dict(inputs=inventory, commands=commands,
                compiler_heap_MiB=128, JVM_heap_MiB=64, execution_scope=scope,
                temporary_roots_remaining=[v for v in TEMPORARIES if Path(v).exists()],
                actual_SDK_install_or_emulator_Android_Native_or_network_verified=False)))


if __name__ == '__main__': unittest.main()
