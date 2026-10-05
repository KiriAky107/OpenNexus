"""Check the installer's interpreter inventory and run only owned native probes."""
from __future__ import annotations
import hashlib
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import zlib

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


def verify_storage_receipt(result: dict, runtime: Path, lock: dict) -> None:
    report = result['report']
    if (result['runtime_id'] != lock['runtime_id'] or report['runtime'] != lock['version']
            or type(report['isolated']) is not int or report['isolated'] != 1
            or not Path(report['executable']).samefile(runtime / lock['entrypoint'])
            or type(result['remaining_processes']) is not int or result['remaining_processes'] != 0
            or report['registry_profile_verified'] is not True):
        raise ValueError('Storage audit did not verify the extracted interpreter and empty Job')
    for key in ('registry_write', 'registry_child_write', 'registry_acl', 'registry_parent_write'):
        if report[key]['denied'] is not True or report[key]['winerror'] != 5:
            raise ValueError('Storage audit did not verify access denied: ' + key)
    if (report['input_acl_error'] != 5 or report['private_acl_error'] != 5
            or report['input_write']['denied'] is not True or report['private_read']['denied'] is not True):
        raise ValueError('Storage audit did not verify synthetic file permissions')


def verify_write_receipt(result: dict, runtime: Path, lock: dict) -> None:
    report = result['report']
    if (result['runtime_id'] != lock['runtime_id'] or report['runtime'] != lock['version']
            or type(report['isolated']) is not int or report['isolated'] != 1
            or not Path(report['executable']).samefile(runtime / lock['entrypoint'])):
        raise ValueError('Write audit did not execute the extracted interpreter')
    for mode in ('logs', 'write-truncate', 'write-children', 'write-delete'):
        item = result['resources'][mode]
        for field in ('remaining_processes', 'write_io_bytes', 'write_io_limit_bytes',
                      'filesystem_bytes', 'disk_limit_bytes', 'stdout_bytes', 'stderr_bytes'):
            if type(item[field]) is not int or item[field] < 0:
                raise ValueError('Write audit has invalid accounting: ' + mode)
        if (item['remaining_processes'] != 0 or item['disk_limit_bytes'] <= 0
                or item['write_io_limit_bytes'] <= item['disk_limit_bytes']
                or item['filesystem_bytes'] >= item['disk_limit_bytes']):
            raise ValueError('Write audit did not verify bounded allocation and empty Job: ' + mode)
        if mode == 'logs':
            if (item['error'] != 'completed' or item['stdout_bytes'] < 2 * 1024 * 1024
                    or item['stderr_bytes'] < 2 * 1024 * 1024
                    or not item['stdout_bytes'] + item['stderr_bytes'] <= item['write_io_bytes'] < item['write_io_limit_bytes']):
                raise ValueError('Write audit did not retain the log positive control')
        elif mode == 'write-delete' and item['error'] == 'EXTENSION_RESOURCE_MONITOR_FAILED':
            continue  # Namespace churn can race inspection; it must fail closed.
        elif (item['error'] != 'EXPERIMENT_WRITE_IO_LIMIT_EXCEEDED'
              or item['write_io_bytes'] < item['write_io_limit_bytes']):
            raise ValueError('Write audit did not stop cumulative writes: ' + mode)


def verify_bound_receipt(result: dict, runtime: Path, lock: dict) -> None:
    basic = result['basic']
    if (result['runtime_id'] != lock['runtime_id'] or basic['runtime'] != lock['version']
            or type(basic['isolated']) is not int or basic['isolated'] != 1
            or not Path(basic['executable']).samefile(runtime / lock['entrypoint'])
            or not str(result['volume_guid_entry']).startswith('\\\\?\\Volume{')
            or not Path(result['volume_guid_entry']).samefile(runtime / lock['entrypoint'])
            or type(result['remaining_processes']) is not int or result['remaining_processes'] != 0):
        raise ValueError('Native bound runtime did not verify the extracted interpreter and empty Job')
    verify_sources_receipt(basic, result['sources'])


def verify_sources_receipt(basic: dict, sources: dict) -> None:
    if (type(sources['selected_files']) is not int or sources['selected_files'] != 2
            or any(sources[key] is not True for key in (
                'outside_profile', 'bytes_preserved', 'unselected_absent', 'host_original_editable'))):
        raise ValueError('Native bound runtime did not verify the selected source copies')
    for key in ('entry_write', 'entry_delete', 'source_folder_rename', 'neighbor_create', 'original_unselected_read'):
        if basic[key]['denied'] is not True:
            raise ValueError('Native selected source operation was not denied: ' + key)


def verify_worker_receipt(receipt: dict, runtime: Path, lock: dict) -> None:
    """Require real formal-worker results, not merely successful process creation."""
    expected = {
        'basic': ('completed', None), 'logs': ('completed', None),
        'nonzero': ('failed', 'EXPERIMENT_NONZERO_EXIT'),
        'cancel': ('cancelled', 'EXPERIMENT_CANCELLED'),
        'switch': ('cancelled', 'EXPERIMENT_CANCELLED'),
        'shutdown': ('cancelled', 'EXPERIMENT_CANCELLED'),
        'wall': ('limited', 'EXTENSION_TOOL_DEADLINE_EXCEEDED'),
        'cpu': ('limited', 'EXTENSION_RESOURCE_CPU_EXCEEDED'),
        'bad-output': ('failed', 'EXPERIMENT_OUTPUT_INVALID'),
        'named-stream': ('failed', 'EXPERIMENT_OUTPUT_NAMED_STREAM_REJECTED'),
        'png-output': ('completed', None),
        'journal-failure': ('failed', 'EXPERIMENT_CLEANUP_JOURNAL_FAILED'),
    }
    if (type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
            or receipt['runtime_id'] != lock['runtime_id'] or set(receipt['runs']) != set(expected)):
        raise ValueError('Formal worker receipt is incomplete or from another runtime')
    for mode, (outcome, error) in expected.items():
        item = receipt['runs'][mode]
        result = item['result']
        if (type(item['remaining_processes']) is not int or item['remaining_processes'] != 0
                or item['profile_removed'] is not True or item['profile_registry_removed'] is not True
                or item['cleanup_complete'] is not (mode != 'journal-failure')
                or item['cleanup_pending'] is not (mode == 'journal-failure')
                or item['runtime']['runtime_id'] != lock['runtime_id']
                or item['runtime']['version'] != lock['version']
                or type(item['runtime']['files']) is not int or item['runtime']['files'] <= 0
                or result['outcome'] != outcome or result['error'] != error):
            raise ValueError('Formal worker has an invalid terminal result: ' + mode)
        for field in ('elapsed_ms', 'user_cpu_ticks', 'peak_memory_bytes', 'final_disk_bytes'):
            if type(result[field]) is not int or result[field] < 0:
                raise ValueError('Formal worker has unknown or invalid accounting: ' + mode)
        if result['elapsed_ms'] == 0 or result['peak_memory_bytes'] == 0:
            raise ValueError('Formal worker is missing real measurements: ' + mode)
        code = result['exit_code']
        if ((mode in ('basic', 'logs', 'bad-output', 'named-stream', 'png-output', 'journal-failure') and (type(code) is not int or code != 0))
                or (mode == 'nonzero' and (type(code) is not int or code != 7))
                or (mode in ('cancel', 'switch', 'shutdown', 'wall', 'cpu')
                    and code is not None and (type(code) is not int or code == 0))):
            raise ValueError('Formal worker exit code disagrees with the outcome: ' + mode)
        outputs = result['outputs']
        if mode in ('bad-output', 'named-stream'):
            if outputs != {'status': 'rejected', 'error': error}:
                raise ValueError('Formal worker hid rejected output files: ' + mode)
        elif (outputs['status'] != 'collected' or type(outputs['summary']['total_bytes']) is not int
              or outputs['summary']['total_bytes'] < 0):
            raise ValueError('Formal worker omitted collected output evidence: ' + mode)
        if mode == 'png-output':
            summary = outputs['summary']
            if (len(summary['files']) != 1 or summary['skipped'] != []
                    or summary['files'][0]['path'] != 'result.png' or summary['files'][0]['kind'] != 'png'
                    or type(summary['files'][0]['bytes']) is not int
                    or summary['files'][0]['bytes'] < 60 or summary['total_bytes'] != summary['files'][0]['bytes']
                    or len(summary['files'][0]['sha256']) != 64
                    or 'generated-png' not in result['logs']['stdout']['text']):
                raise ValueError('Formal worker omitted the native generated PNG positive control')
            data = base64.b64decode(item['persisted_output_bytes']['result.png'], validate=True)
            if (set(item['persisted_output_bytes']) != {'result.png'} or len(data) > 4096
                    or len(data) != summary['total_bytes'] or not data.startswith(b'\x89PNG\r\n\x1a\n')
                    or hashlib.sha256(data).hexdigest() != summary['files'][0]['sha256']):
                raise ValueError('Formal worker did not verify the persisted PNG bytes after cleanup')
            offset, chunks = 8, []
            while offset < len(data):
                if offset + 12 > len(data):
                    raise ValueError('Native generated PNG is truncated')
                length = int.from_bytes(data[offset:offset + 4], 'big')
                end = offset + length + 12
                if end > len(data) or zlib.crc32(data[offset + 4:end - 4]) != int.from_bytes(data[end - 4:end], 'big'):
                    raise ValueError('Native generated PNG has invalid chunks')
                chunks.append((data[offset + 4:offset + 8], data[offset + 8:end - 4]))
                offset = end
            if ([kind for kind, _ in chunks] != [b'IHDR', b'IDAT', b'IEND']
                    or chunks[0][1] != struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0) or chunks[-1][1] != b''):
                raise ValueError('Native generated PNG differs from its positive control')
            decoder = zlib.decompressobj()
            if (decoder.decompress(chunks[1][1], 5) != b'\x00\xff\x00\x00' or not decoder.eof
                    or decoder.unused_data or decoder.unconsumed_tail):
                raise ValueError('Native generated PNG did not preserve the real pixel')
        for stream in ('stdout', 'stderr'):
            log = result['logs'][stream]
            if (log['complete'] is not True or log['read_error'] is not False
                    or log['invalid_utf8'] is not False or type(log['truncated']) is not bool
                    or type(log['bytes_seen']) is not int or type(log['retained_bytes']) is not int
                    or not 0 <= log['retained_bytes'] <= min(8192, log['bytes_seen'])
                    or not isinstance(log['text'], str)
                    or len(log['text'].encode('utf-8')) != log['retained_bytes']):
                raise ValueError('Formal worker did not drain bounded logs: ' + mode)
        if mode in ('cancel', 'switch', 'shutdown') and 'child-ready' not in result['logs']['stdout']['text']:
            raise ValueError('Formal worker cancellation did not cover a real child: ' + mode)
        if mode == 'logs':
            for stream, log in result['logs'].items():
                # CPython can also emit startup diagnostics on stderr. Count
                # them honestly while requiring the complete fixture payload.
                if ((stream == 'stdout' and log['bytes_seen'] != 2 * 1024 * 1024)
                        or log['bytes_seen'] < 2 * 1024 * 1024 or log['truncated'] is not True):
                    raise ValueError('Formal worker did not retain real truncated stream evidence')
        if mode == 'cpu' and result['user_cpu_ticks'] < 10_000_000:
            raise ValueError('Formal worker CPU limit was not measured as user CPU time')
        if mode == 'wall' and result['elapsed_ms'] < 1000:
            raise ValueError('Formal worker wall timer did not cover its configured interval')
    text = receipt['runs']['basic']['result']['logs']['stdout']['text']
    lines = [line.removeprefix('WORKER_REPORT:') for line in text.splitlines()
             if line.startswith('WORKER_REPORT:')]
    if len(lines) != 1:
        raise ValueError('Formal worker is missing its real interpreter positive control')
    report = json.loads(lines[0])
    for output_mode in ('basic', 'journal-failure'):
        outputs = receipt['runs'][output_mode]['result']['outputs']['summary']
        files = {item['path']: item for item in outputs['files']}
        if (len(outputs['files']) != 2 or set(files) != {'worker-output.txt', 'protected-descriptor.txt'} or outputs['skipped'] != []
                or outputs['total_bytes'] != 22):
            raise ValueError('Formal worker did not preserve both synthetic outputs')
        for path, content in (('worker-output.txt', b'owned synthetic output'), ('protected-descriptor.txt', b'')):
            if type(files[path]['bytes']) is not int or files[path] != {'path': path, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'kind': 'text'}:
                raise ValueError('Formal worker output manifest is invalid: ' + path)
            if base64.b64decode(receipt['runs'][output_mode]['persisted_output_bytes'][path], validate=True) != content:
                raise ValueError('Formal worker did not read its stored bytes after cleanup: ' + path)
    if (report['runtime'] != lock['version'] or not Path(report['executable']).samefile(runtime / lock['entrypoint'])
            or type(report['isolated']) is not int or report['isolated'] != 1 or report['argv'] != []
            or report['parent_environment_inherited'] is not False or report['input_write_denied'] is not True
            or type(report['csv_total']) is not int or report['csv_total'] != 18 or report['first_name'] != '中文'):
        raise ValueError('Formal worker did not verify the extracted interpreter and selected inputs')
    acl = report['scratch_acl_access']
    for key in ('root', 'parent', 'created_file', 'root_acl_change', 'file_acl_change',
                'outside_create', 'explicit_descriptor_acl_access'):
        if type(acl[key]) is not int or acl[key] != 5:
            raise ValueError('Formal worker did not verify owned filesystem access denied: ' + key)
    if (acl['cwd_is_scratch'] is not True or acl['scratch_read_write_delete'] is not True
            or type(acl['protected_descriptor_acl_access']) is not int
            or acl['protected_descriptor_acl_access'] != 0):
        raise ValueError('Formal worker omitted filesystem positive controls or explicit descriptor behavior')


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
    output = subprocess.run(command, cwd=ROOT / 'frontend/src-tauri', check=True,
                            stdout=subprocess.PIPE).stdout.decode('utf-8', errors='replace')
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
    name = 'storage-audit-result.json'
    verify_storage_receipt(json.loads((evidence / name).read_bytes()), runtime, lock)
    receipts[name] = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
    name = 'write-io-result.json'
    verify_write_receipt(json.loads((evidence / name).read_bytes()), runtime, lock)
    receipts[name] = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
    name = 'bound-runtime-result.json'
    verify_bound_receipt(json.loads((evidence / name).read_bytes()), runtime, lock)
    receipts[name] = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
    name = 'worker-runtime-result.json'
    verify_worker_receipt(json.loads((evidence / name).read_bytes()), runtime, lock)
    receipts[name] = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
    return {'system_only_path': True, 'runtime_source': 'extracted-installer',
            'native_signatures_verified': len(signature_items),
            'test_driver_sha256': hashlib.sha256(Path(drivers[0]).read_bytes()).hexdigest(),
            'receipts': receipts}
