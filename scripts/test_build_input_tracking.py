"""Exercise the actual Host build script with Cargo and absent desktop resources."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BuildInputTrackingTests(unittest.TestCase):
    def test_unchanged_debug_build_does_not_rebuild_for_absent_release_resources(self):
        with tempfile.TemporaryDirectory(prefix='opennexus-build-input-') as temporary:
            base = Path(temporary)
            project = base / 'frontend/src-tauri'
            (project / 'src').mkdir(parents=True)
            shutil.copyfile(ROOT / 'frontend/src-tauri/build.rs', project / 'build.rs')
            (project / 'Cargo.toml').write_text(
                '[package]\nname="build-input-probe"\nversion="0.0.0"\nedition="2021"\n'
                '[features]\ndesktop=[]\n', 'utf-8')
            (project / 'src/lib.rs').write_text(
                'pub const CORE: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/core-manifest.json"));\n'
                'pub const RUNTIME: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/experiment-runtime.json"));\n', 'utf-8')
            def build():
                result = subprocess.run(
                    ['cargo', 'build', '--offline', '--message-format=json', '--target-dir', str(base / 'target')],
                    cwd=project, env=dict(os.environ, CARGO_TERM_COLOR='never', CARGO_INCREMENTAL='0'),
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr[-5000:])
                events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
                library = [event for event in events if event.get('reason') == 'compiler-artifact'
                           and event['target']['kind'] == ['lib']]
                self.assertEqual(len(library), 1)
                output = next(event['out_dir'] for event in events if event.get('reason') == 'build-script-executed')
                self.assertEqual((Path(output) / 'core-manifest.json').read_bytes(), b'{}')
                self.assertEqual((Path(output) / 'experiment-runtime.json').read_bytes(), b'{}')
                return library[0]['fresh']
            self.assertFalse(build())
            self.assertFalse((base / '.build/sidecar/manifest.json').exists())
            self.assertFalse((base / '.build/experiment-runtime').exists())
            self.assertTrue(build(), 'Unchanged debug input must reuse the existing Host artifact')


if __name__ == '__main__':
    unittest.main()
