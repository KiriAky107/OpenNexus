"""Extract a final NSIS installer and verify its actual Host/Core/SDK payload.

Requires 7-Zip. The report stays in .build/windows; no installed user data is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def microsoft_signature(path: Path) -> None:
    environment = dict(os.environ, OPENNEXUS_TASK_SIGNED_FILE=str(path),
        PSModulePath=str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules'))
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "$ErrorActionPreference='Stop'; $s=Get-AuthenticodeSignature -LiteralPath $env:OPENNEXUS_TASK_SIGNED_FILE; "
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation,') {throw 'Invalid Microsoft signature'}"],
        env=environment, check=True, capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default='')
    parser.add_argument('--installer', type=Path)
    args = parser.parse_args()
    release = ROOT / 'frontend/src-tauri/target' / args.target / 'release'
    candidates = [args.installer] if args.installer else list((release / 'bundle/nsis').glob('*.exe'))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise ValueError('Expected exactly one final NSIS installer')
    installer = candidates[0].resolve()
    if not 1_000_000 < installer.stat().st_size < 300 * 1024 * 1024:
        raise ValueError('Installer size is outside the expected bounds')
    seven_zip = shutil.which('7z') or 'C:/Program Files/7-Zip/7z.exe'
    staging = ROOT / '.build/windows'
    staging.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('windows_loader', ROOT / 'scripts/verify-windows-loader.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest_bytes = (ROOT / '.build/sidecar/manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    with tempfile.TemporaryDirectory(prefix='package-', dir=staging) as directory:
        payload = Path(directory)
        subprocess.run([seven_zip, 'x', str(installer), '-o' + directory, '-y', '-bb0'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        host = payload / 'OpenNexus.exe'
        if manifest_bytes not in host.read_bytes():
            raise ValueError('Installer Host does not embed this exact Core manifest')
        core = payload / 'core'
        actual = {p.relative_to(core).as_posix() for p in core.rglob('*') if p.is_file()}
        if actual != set(manifest['files']):
            raise ValueError('Installer Core file list differs from its manifest')
        for name, expected in manifest['files'].items():
            if digest(core / name) != expected:
                raise ValueError('Core hash mismatch: ' + name)
        if manifest['lock_sha256'] != digest(ROOT / 'backend/uv.lock'):
            raise ValueError('Core was built with a different dependency lock')
        loader = module.verify(payload)
        dynamic = any(name.lower() == 'webview2loader.dll' for name in loader['host_imports'])
        if args.target.endswith('-gnu') and not dynamic:
            raise ValueError('GNU target did not import its expected dynamic Loader')
        bootstrapper = payload / '$TEMP/MicrosoftEdgeWebview2Setup.exe'
        if not bootstrapper.is_file():
            raise ValueError('Installer is missing the embedded Runtime bootstrapper')
        if os.name == 'nt':
            microsoft_signature(payload / 'WebView2Loader.dll')
            microsoft_signature(bootstrapper)
        report = {'installer': installer.name, 'installer_sha256': digest(installer),
            'target': args.target or 'host-default', 'core_files_verified': len(actual),
            'host_version': manifest['host_version'], 'core_version': manifest['core_version'],
            'dynamic_loader_dependency': dynamic, 'loader': loader,
            'microsoft_signatures_verified': os.name == 'nt', 'runtime_bootstrapper_embedded': True}
    (staging / 'package-verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
