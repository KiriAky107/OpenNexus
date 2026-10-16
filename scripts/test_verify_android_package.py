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
    def test_requires_packaged_native_files_class_and_rejects_broad_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / 'synthetic-dex-container.zip'
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('classes.dex', b'synthetic DEX descriptor table Lcc/kronecker/opennexus/files/MobileFilesPlugin;')
            module.verify_mobile_files(apk,"uses-permission: name='android.permission.INTERNET'\n")
            for permission in ['MANAGE_EXTERNAL_STORAGE','READ_MEDIA_IMAGES','WRITE_EXTERNAL_STORAGE','READ_EXTERNAL_STORAGE','CAMERA']:
                with self.assertRaisesRegex(Exception,'ANDROID_BROAD_FILE_PERMISSION'):
                    module.verify_mobile_files(apk,f"uses-permission: name='android.permission.{permission}'\n")
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('classes.dex', b'no native plugin class')
            with self.assertRaisesRegex(Exception,'ANDROID_FILES_PLUGIN_CLASS_MISSING'):
                module.verify_mobile_files(apk,'')

    def test_accepts_current_aapt2_sdk_label_and_rejects_ambiguous_or_changed_levels(self):
        identity = "package: name='cc.kronecker.opennexus.dev' versionCode='1' versionName='0.1.0-dev1'\n"
        arguments = dict(package='cc.kronecker.opennexus.dev', version='0.1.0-dev1', version_code=1)
        current = identity + "minSdkVersion:'24'\ntargetSdkVersion:'36'\n"
        module.package_identity(current, **arguments)
        for invalid in [current.replace("minSdkVersion:'24'", "minSdkVersion:'26'"),
                        current + "sdkVersion:'24'\n", current.replace("minSdkVersion:'24'\n", '')]:
            with self.assertRaisesRegex(Exception, 'ANDROID_MIN_SDK_MISMATCH'):
                module.package_identity(invalid, **arguments)
        for invalid in [current.replace("targetSdkVersion:'36'", "targetSdkVersion:'35'"),
                        current + "targetSdkVersion:'36'\n"]:
            with self.assertRaisesRegex(Exception, 'ANDROID_TARGET_SDK_MISMATCH'):
                module.package_identity(invalid, **arguments)

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
