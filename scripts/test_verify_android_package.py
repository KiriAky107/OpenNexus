import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('verify_android_package', Path(__file__).with_name('verify_android_package.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AndroidPackageBindingTests(unittest.TestCase):
    def test_rejects_a_different_package_or_version_despite_valid_badging(self):
        badging = "package: name='cc.kronecker.opennexus.dev' versionCode='1' versionName='0.1.0-dev1'\nsdkVersion:'24'\ntargetSdkVersion:'36'\n"
        module.package_identity(badging, package='cc.kronecker.opennexus.dev', version='0.1.0-dev1', version_code=1)
        for change in [('package', 'some.other.app'), ('version', '0.1.0-dev2'), ('version_code', 2)]:
            arguments = dict(package='cc.kronecker.opennexus.dev', version='0.1.0-dev1', version_code=1)
            arguments[change[0]] = change[1]
            with self.assertRaisesRegex(Exception, 'ANDROID_PACKAGE_IDENTITY_MISMATCH'):
                module.package_identity(badging, **arguments)

    def test_rejects_missing_abi_and_replaced_native_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / 'x86_64-linux-android/debug/libopennexus_mobile.so'
            native.parent.mkdir(parents=True)
            data = bytearray(96)
            data[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<HH', data, 16, 3, 62)
            native.write_bytes(data)
            apk = root / 'synthetic-package.zip'
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('lib/x86_64/libopennexus_mobile.so', data)
            module.verify_libraries(apk, root, ['x86_64'])
            with self.assertRaisesRegex(Exception, 'ANDROID_ABI_SET_MISMATCH'):
                module.verify_libraries(apk, root, ['arm64-v8a', 'x86_64'])
            native.write_bytes(data + b'changed native input')
            with self.assertRaisesRegex(Exception, 'ANDROID_COMPILED_LIBRARY_MISMATCH'):
                module.verify_libraries(apk, root, ['x86_64'])


if __name__ == '__main__':
    unittest.main()
