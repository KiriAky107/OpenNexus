"""Check the installer's interpreter inventory and run only owned native probes."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('experiment_builder', ROOT / 'scripts/prepare-experiment-runtime.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def verify_payload(payload: Path, expected_bytes: bytes) -> dict:
    expected = json.loads(expected_bytes)
    lock = json.loads((ROOT / 'scripts/experiment-runtime-lock.json').read_text(encoding='utf-8'))
    if expected['schema_version'] != 1 or expected['lock'] != lock:
        raise ValueError('Package experiment inventory differs from the source lock')
    runtime = payload / 'runtimes' / lock['runtime_id']
    builder.checked_directory(runtime)
    receipt = runtime / 'runtime.json'
    if not builder.regular(receipt) or receipt.stat().st_size != len(expected_bytes):
        raise ValueError('Package experiment receipt is unsafe or incomplete')
    if receipt.read_bytes() != expected_bytes:
        raise ValueError('Package experiment receipt differs from the build inventory')
    if expected_bytes not in (payload / 'OpenNexus.exe').read_bytes():
        raise ValueError('Host does not embed this exact experiment inventory')
    builder.verify_tree(runtime, expected)
    return {'runtime_id': lock['runtime_id'], 'version': lock['version'],
            'files_verified': len(expected['files']), 'inventory_sha256': hashlib.sha256(expected_bytes).hexdigest(),
            'archive_sha256': lock['archive_sha256'], 'license_present': 'LICENSE.txt' in expected['files']}


def native_probe(payload: Path, target: str, evidence: Path) -> dict:
    """Build a test driver, then launch it with system-only PATH and package assets.

    The driver uses embedded synthetic fixtures and AppContainer-owned inputs.
    It never opens the installed application, changes a storage pointer or runs
    scripts from real vaults. Only cfg(test) accepts these override variables.
    """
    if os.name != 'nt':
        raise ValueError('Package native experiment probe requires Windows')
    command = ['cargo', 'test', '--lib', '--release', '--locked', '--no-run', '--message-format=json']
    if target:
        command.extend(['--target', target])
    output = subprocess.run(command, cwd=ROOT / 'frontend/src-tauri', check=True, text=True,
                            stdout=subprocess.PIPE).stdout
    drivers = []
    for line in output.splitlines():
        item = json.loads(line)
        if item.get('reason') == 'compiler-artifact' and item.get('executable') and item['target']['name'] == 'notesagent_host':
            drivers.append(item['executable'])
    if len(drivers) != 1:
        raise ValueError('Expected exactly one compiled native experiment test driver')
    lock = json.loads((ROOT / 'scripts/experiment-runtime-lock.json').read_text(encoding='utf-8'))
    runtime = (payload / 'runtimes' / lock['runtime_id']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    signature_env = dict(os.environ, OPENNEXUS_TASK_RUNTIME=str(runtime),
        PSModulePath=str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules'))
    signature_command = """$ErrorActionPreference='Stop';
        $items=@(Get-ChildItem -LiteralPath $env:OPENNEXUS_TASK_RUNTIME -File |
          Where-Object Extension -in '.exe','.dll','.pyd' | Get-AuthenticodeSignature);
        if ($items.Count -eq 0 -or @($items | Where-Object Status -ne Valid).Count -ne 0) {
          throw 'Extracted interpreter signature verification failed' };
        $items | Select-Object @{n='file';e={Split-Path $_.Path -Leaf}},
          @{n='status';e={$_.Status.ToString()}},@{n='signer';e={$_.SignerCertificate.Subject}} | ConvertTo-Json"""
    signatures = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', signature_command],
        env=signature_env, text=True, capture_output=True, check=True).stdout
    signature_items = json.loads(signatures)
    (evidence / 'signatures.json').write_text(json.dumps(signature_items, indent=2), encoding='utf-8')
    environment = dict(os.environ, PATH=str(Path(os.environ['SystemRoot']) / 'System32'),
                       OPENNEXUS_PROBE_RUNTIME_ROOT=str(runtime), OPENNEXUS_PROBE_RECEIPT_DIR=str(evidence.resolve()),
                       OPENNEXUS_PROBE_PARENT_TOKEN='synthetic-parent-token-never-inherited')
    for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        environment.pop(name, None)
    subprocess.run([str(Path(drivers[0]).resolve()), 'experiment_runtime_probe::', '--ignored',
                    '--test-threads=1', '--nocapture'], cwd=payload, env=environment, check=True, timeout=180)
    receipts = {}
    for name in ('probe-result.json', 'policy-probe-result.json'):
        result = json.loads((evidence / name).read_bytes())
        if not Path(result['basic']['executable']).samefile(runtime / lock['entrypoint']):
            raise ValueError('Native probe did not execute the extracted interpreter')
        receipts[name] = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
    return {'system_only_path': True, 'runtime_source': 'extracted-installer',
            'native_signatures_verified': len(signature_items),
            'test_driver_sha256': hashlib.sha256(Path(drivers[0]).read_bytes()).hexdigest(),
            'receipts': receipts}
