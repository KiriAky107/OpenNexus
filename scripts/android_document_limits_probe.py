"""One hosted native document-boundary scene and an actual SAF content export.

Only the declared fresh fixture files are written directly, before the cold list.
Opening/rejecting them must retain their original bytes, native IDs and ledger.
This is not a physical-device, user import, power-loss or whole A2/A3C claim.
"""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile
import zipfile
from android_auth_fixture import atomic_json
from android_runtime_probe import APP, require, require_hosted_emulator

MIB = 1024 * 1024
SEED_PATH = 'limits-seed.md'
SEED = '# Limits seed 原件 😀\nOne actual original GUI Save.\n'.encode()
NODE = 'Limits 原节点 😀'
CASES = (
    ('limits-md', 'limits-exact.md', MIB, True),
    ('limits-md-over', 'limits-over.md', MIB + 1, False),
    ('limits-canvas-over-md', 'limits-above-md.canvas', MIB + 1, True),
    ('limits-canvas', 'limits-exact.canvas', 4 * MIB, True),
    ('limits-canvas-over', 'limits-over.canvas', 4 * MIB + 1, False),
)
STEPS = ('limits-seed', 'limits-list', *(case[0] for case in CASES), 'limits-cold', 'export')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def document_steps(scope):
    require(type(scope) is str and scope in ('document-limits', 'document-export'), 'LIMITS_EXPLICIT_SCOPE')
    return ('limits-seed', 'limits-list', 'export') if scope == 'document-export' else STEPS


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(path, size):
    require(any(path == p and size == n for _, p, n, _ in CASES), 'LIMITS_DECLARED_FIXTURE_ONLY')
    if path.endswith('.md'):
        prefix = '# 相同标题 😀\r\n# 相同标题 😀\r\nOriginal CRLF and duplicate headings.\r\n'.encode()
        count = size - len(prefix)
        return prefix + b'x\n' * (count // 2) + b'x' * (count % 2)
    value = {'nodes': [dict(id='limits-original-node', type='text', x=0, y=0, width=200, height=120,
        text=NODE, vendor={'keep': ['甲', True, None]})], 'edges': [],
        'vendor-root': {'original': ['扩展', 17, False], 'padding': ''}}
    body = (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\r\n').encode()
    marker = b'"padding":""'
    require(body.count(marker) == 1 and len(body) < size, 'LIMITS_PADDING_ADMISSION')
    return body.replace(marker, b'"padding":"' + b'x' * (size - len(body)) + b'"')


def ledger(value):
    return {key: value[key] for key in ('files', 'saves', 'operations', 'outbox', 'journal')}


def exported_originals(raw, originals):
    require(type(raw) is bytes and 0 < len(raw) < 32 * MIB, 'LIMITS_EXPORT_BOUND')
    def unique(pairs):
        require(len(pairs) == len({key for key, _ in pairs}), 'LIMITS_EXPORT_DUPLICATE_FIELD')
        return dict(pairs)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(len(names) == len(set(names)) == len(originals) + 1
            and set(names) == {'OpenNexus-export.json', *('vault/' + path for path in originals)}
            and all(not entry.is_dir() and ((entry.external_attr >> 16) & 0o170000) != 0o120000 for entry in entries),
            'LIMITS_EXPORT_NOT_EXACT_CONTENT_SET')
        metadata = json.loads(archive.read('OpenNexus-export.json'), object_pairs_hook=unique)
        require(set(metadata) == {'format', 'schema', 'scope', 'files'}
            and metadata['format'] == 'opennexus-content-export' and metadata['schema'] == 1
            and metadata['scope'] == 'markdown-canvas-images' and len(metadata['files']) == len(originals),
            'LIMITS_EXPORT_MANIFEST')
        expected = {('vault/' + path, len(body), digest(body)) for path, body in originals.items()}
        require(all(set(row) == {'path', 'size', 'sha256'} and type(row['size']) is int for row in metadata['files'])
            and {(row['path'], row['size'], row['sha256']) for row in metadata['files']} == expected,
            'LIMITS_EXPORT_ORIGINAL_MANIFEST_BYTES')
        for path, body in originals.items():
            require(archive.read('vault/' + path) == body, 'LIMITS_EXPORTED_RAW_ORIGINAL_CHANGED')


def readonly_result(before, after, originals, opened):
    require(type(opened) is set and opened <= {path for _, path, _, allowed in CASES if allowed},
        'LIMITS_OPENED_PATH_ADMISSION')
    require(ledger(after) == ledger(before), 'LIMITS_READ_CHANGED_NATIVE_LEDGER')
    files = {row['path']: row for row in after['files']}
    require(set(files) == set(originals) and len(files) == len(after['files'])
        and len({row['id'] for row in files.values()}) == len(files), 'LIMITS_NATIVE_ORIGINAL_SET')
    for path, body in originals.items():
        row = files[path]
        require(re.fullmatch(UUID, row['id']) and row['hash'] == digest(body) and row['deleted'] == 0,
            'LIMITS_NATIVE_ORIGINAL_BYTES')
    drafts = {row['path']: row for row in after['drafts']}
    require(set(drafts) == opened | {SEED_PATH} and len(drafts) == len(after['drafts']),
        'LIMITS_REJECTED_FILE_CREATED_DRAFT_OR_DUPLICATE')
    for path in opened:
        row = drafts[path]
        require(row['file_id'] == files[path]['id'] and row['base_hash'] == digest(originals[path])
            and row['sha256'] == digest(originals[path]) and row['bytes'] == len(originals[path])
            and row['generation'] == row['saved_generation'] == 0, 'LIMITS_OPEN_REWROTE_NATIVE_DRAFT')
    require(drafts[SEED_PATH] == before['drafts'][0], 'LIMITS_ORIGINAL_SEED_DRAFT_CHANGED')


def snapshot(probe, phase):
    require(phase.removesuffix('-failure') in document_steps(probe.args.scope),
        'LIMITS_OWN_SNAPSHOT_SCOPE')
    paths = [SEED_PATH, *(path for _, path, _, _ in CASES)]
    marks = ','.join('?' for _ in paths)
    value = {}
    for key, table, columns in [('drafts', 'drafts', 'id,file_id,path,base_hash,generation,saved_generation,content'),
                              ('saves', 'save_intents', 'operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        rows = probe.database_rows('SELECT ' + columns + ' FROM ' + table + ' WHERE path IN (' + marks + ') ORDER BY path,rowid', paths)
        value[key] = [dict(zip(columns.split(','), row)) for row in rows]
        if key == 'drafts':
            for row in value[key]:
                body = row.pop('content').encode()
                row.update(bytes=len(body), sha256=digest(body))
    folder = Path(tempfile.mkdtemp(prefix='original-document-limits-', dir=probe.tools))
    database = folder / 'host.sqlite3'
    for suffix in ('', '-wal', '-shm'):
        raw = probe.private(probe.root + 'vault/.ainote/host.sqlite3' + suffix, check=False)
        if raw.returncode == 0:
            Path(str(database) + suffix).write_bytes(raw.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'LIMITS_NATIVE_INTEGRITY')
        for key, columns in [('files', 'id,path,hash,revision,deleted'),
                ('outbox', 'operation_id,file_id,revision,path,hash,operation,state'),
                ('journal', 'operation_id,file_id,path,expected,state')]:
            value[key] = [dict(zip(columns.split(','), row)) for row in db.execute(
                'SELECT ' + columns + ' FROM ' + key + ' WHERE path IN (' + marks + ') ORDER BY path,rowid', paths)]
        operations = sorted({row['operation_id'] for row in value['saves'] + value['outbox'] + value['journal']})
        require(1 <= len(operations) <= 16, 'LIMITS_ORIGINAL_OPERATION_BOUND')
        columns = 'operation_id,fingerprint,state,result'
        value['operations'] = [dict(zip(columns.split(','), row)) for row in db.execute(
            'SELECT ' + columns + ' FROM operations WHERE operation_id IN (' + ','.join('?' for _ in operations) + ') ORDER BY rowid', operations)]
    atomic_json(probe.output / (phase + '-native.json'), value, exclusive=True)
    return value


def run_document_limits(probe):
    require_hosted_emulator(probe.args.serial)
    planned = document_steps(probe.args.scope)
    export_only = probe.args.scope == 'document-export'
    report = {'passed': False, 'run_id': os.environ['GITHUB_RUN_ID'], 'tools_source': probe.args.tools_commit,
        'compiled_source': probe.args.commit, 'APK_sha256': probe.binding['sha256'],
        'scope': 'Only remaining real SAF export of declared original files' if export_only else
            'Native Markdown1MiB/Canvas4MiB boundaries, original read-only drafts and real SAF content export',
        'boundary_open_refusal_GUI_not_replayed': export_only,
        'synthetic_files_declared_not_GUI_imported': True, 'physical_upgrade_or_whole_A2_A3C_complete': False,
        'executed_steps': []}
    current = None
    originals = {SEED_PATH: SEED, **{path: fixture(path, size) for _, path, size, _ in CASES}}
    def phase(name, cold=False):
        nonlocal current
        require(name == planned[len(report['executed_steps'])], 'LIMITS_ORIGINAL_PHASE_ORDER')
        current = name
        atomic_json(probe.output / (name + '-original-intent.json'), {'step': name, 'scope': probe.args.scope,
            'run_id': os.environ['GITHUB_RUN_ID'], 'tools_source': probe.args.tools_commit,
            'compiled_source': probe.args.commit, 'automatic_retry': False}, exclusive=True)
        probe.phase(name, continue_process=not cold, keep_running=True)
        value = snapshot(probe, name)
        report['executed_steps'].append(name)
        return value
    try:
        probe.enable_test_ime()
        seed = phase('limits-seed', cold=True)
        require(probe.original(SEED_PATH) == SEED and len(seed['saves']) == 1
            and seed['saves'][0]['state'] == 'committed', 'LIMITS_ACTUAL_SEED_NOT_COMMITTED')
        atomic_json(probe.output / 'limits-fixtures-original-intent.json', {
            'scope': 'Fresh owned synthetic external files only; no native operation or user path writes',
            'files': {path: {'bytes': len(body), 'sha256': digest(body)} for path, body in originals.items() if path != SEED_PATH},
            'automatic_retry': False}, exclusive=True)
        for path, body in originals.items():
            if path != SEED_PATH:
                target = probe.root + 'vault/' + path
                command = 'umask 077; test ! -e ' + shlex.quote(target) + ' && cat > ' + shlex.quote(target)
                probe.device('shell', '-T', shlex.join(('run-as', APP, 'sh', '-c', command)), input=body)
            require(probe.private(probe.root + 'vault/' + path).stdout == body, 'LIMITS_ORIGINAL_FIXTURE_BYTES')
            (probe.output / ('original-' + path)).write_bytes(body)
        before_pid = probe.shell('pidof', APP).stdout.strip()
        baseline = phase('limits-list', cold=True)
        require(probe.shell('pidof', APP).stdout.strip() != before_pid, 'LIMITS_DECLARED_FIXTURE_REOPEN_NOT_COLD')
        opened = set()
        readonly_result(baseline, baseline, originals, opened)
        old, new = before_pid, probe.shell('pidof', APP).stdout.strip()
        if not export_only:
            for step, path, _, allowed in CASES:
                after = phase(step)
                if allowed:
                    opened.add(path)
                readonly_result(baseline, after, originals, opened)
            old = probe.shell('pidof', APP).stdout.strip()
            after = phase('limits-cold', cold=True)
            new = probe.shell('pidof', APP).stdout.strip()
            readonly_result(baseline, after, originals, opened)
        require(re.fullmatch(rb'[1-9][0-9]{0,9}', old) and re.fullmatch(rb'[1-9][0-9]{0,9}', new) and old != new,
            'LIMITS_FINAL_PROCESS_NOT_NEW')
        exported = phase('export')
        readonly_result(baseline, exported, originals, opened)
        delivery = probe.verify_export(originals)
        exported_originals((probe.output / 'system-saved-content.zip').read_bytes(), originals)
        for path, body in originals.items():
            require(probe.private(probe.root + 'vault/' + path).stdout == body, 'LIMITS_READ_OR_EXPORT_REWROTE_FILE')
        report.update(passed=True, actual_seed_file_id=seed['files'][0]['id'], actual_native_file_ids={x['path']: x['id'] for x in baseline['files']},
            cases=[] if export_only else [{'path': path, 'bytes': size, 'native_opened': allowed, 'sha256': digest(originals[path])} for _, path, size, allowed in CASES],
            actual_boundary_GUI_tested=not export_only, declared_files_have_no_new_save_intents=True,
            unknown_Canvas_extensions_and_original_CRLF_unchanged=True,
            cold_original_PID=int(old), cold_new_PID=int(new), system_destination=delivery,
            export_is_content_not_private_DB_history_or_credentials=True)
        if not export_only:
            report['rejected_paths_have_no_drafts_or_save_intents'] = True
    except Exception as error:
        report['failure_class'] = type(error).__name__
        report['phase'] = current
        if probe.root is not None and current in planned:
            try:
                snapshot(probe, current + '-failure')
            except Exception as observation:
                report['failure_observation_class'] = type(observation).__name__
        raise
    finally:
        atomic_json(probe.output / ('document-export-proof.json' if export_only else 'document-limits-proof.json'), report, exclusive=True)
        probe.force_stop()
