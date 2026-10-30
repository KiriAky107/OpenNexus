"""Actual javac/vendor JVM with two source-bound SDK schema API surfaces."""
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
LEGACY = '''package com.android.repository.impl.meta;
import java.io.*;
import java.util.Collection;
import com.android.repository.api.*;
import javax.xml.bind.JAXBElement;
import org.w3c.dom.ls.LSResourceResolver;
/** Compile-only surface from the vendor pre-fileDescription API. Never executed. */
public final class SchemaModuleUtil {
 public static Object unmarshal(InputStream in, Collection<SchemaModule<?>> modules,
     boolean strict, ProgressIndicator progress) { return null; }
 public static void marshal(JAXBElement value, Collection<SchemaModule<?>> modules,
     OutputStream out, LSResourceResolver resolver, ProgressIndicator progress) {}
 public static LSResourceResolver createResourceResolver(java.util.Set<SchemaModule<?>> modules,
     ProgressIndicator progress) { return null; }
}
'''


class SchemaContract(unittest.TestCase):
    def test_actual_compiler_accepts_legacy_surface_and_current_vendor_closed_schema(self):
        sdk = Path(os.environ.get('ANDROID_HOME', 'C:/Users/KiriAky/AppData/Local/Android/Sdk')) / 'cmdline-tools/latest'
        home = Path(os.environ.get('JAVA_HOME', 'E:/java21'))
        suffix = '.exe' if os.name == 'nt' else ''
        java, javac = home / ('bin/java' + suffix), home / ('bin/javac' + suffix)
        classpath = sdk / 'lib/avdmanager-classpath.jar'
        helper = ROOT / 'scripts/android-sdk-tools/OwnedEmulatorRegistry.java'
        check = ROOT / 'scripts/android-sdk-tests/OwnedEmulatorSchemaContractCheck.java'
        inputs = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (java, javac, classpath, helper, check)}
        commands = []; scope = dict(legacy_surface_compiled=False, actual_vendor_contracts_executed=False)
        def run(argv):
            result = subprocess.run(argv, capture_output=True, timeout=40, check=False)
            commands.append(dict(argv=argv, returncode=result.returncode,
                stdout_base64=base64.b64encode(result.stdout).decode(), stderr_base64=base64.b64encode(result.stderr).decode()))
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf8', 'replace'))
            return result.stdout
        try:
            with tempfile.TemporaryDirectory(prefix='onx-sdk-schema-check-') as folder:
                TEMPORARIES.append(folder); temp = Path(folder).resolve()
                shim = temp / 'legacy/com/android/repository/impl/meta/SchemaModuleUtil.java'
                shim.parent.mkdir(parents=True); shim.write_text(LEGACY, encoding='utf8')
                legacy, compile_only, actual = [temp / n for n in ('legacy-classes', 'compile-only', 'actual')]
                for path in (legacy, compile_only, actual): path.mkdir()
                run([str(javac), '-J-Xmx128m', '-cp', str(classpath), '-d', str(legacy), str(shim)])
                run([str(javac), '-J-Xmx128m', '-encoding', 'UTF-8', '-cp', str(legacy) + os.pathsep + str(classpath),
                     '-d', str(compile_only), str(helper)])
                scope['legacy_surface_compiled'] = True
                run([str(javac), '-J-Xmx128m', '-encoding', 'UTF-8', '-cp', str(classpath), '-d', str(actual), str(helper), str(check)])
                output = run([str(java), '-Xmx64m', '-cp', str(actual) + os.pathsep + str(classpath),
                              'cc.kronecker.opennexus.sdkinternal.OwnedEmulatorSchemaContractCheck'])
                self.assertIn(b'SDK_SCHEMA_CONTRACTS_OK', output); scope['actual_vendor_contracts_executed'] = True
                for path, digest in inputs.items(): self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        finally:
            print('SDK_SCHEMA_CONTRACT_PROOF ' + json.dumps(dict(inputs=inputs, commands=commands, scope=scope,
                legacy_surface_is_compile_only_fixture=True, actual_old_SDK_or_hosted_diagnostic_verified=False,
                temporary_roots_remaining=[n for n in TEMPORARIES if Path(n).exists()])))


if __name__ == '__main__': unittest.main()
