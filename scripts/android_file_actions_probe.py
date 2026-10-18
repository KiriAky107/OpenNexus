"""New owned local MD/Canvas actions, using UI and original native receipts."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile

from android_runtime_probe import APP, require, require_hosted_emulator

BODY = '# 原始本地笔记 😀\nLocal original before a reviewed file action.\n'.encode()
UPDATED = BODY.replace(b'\n', b'\r\n') + '并发更改必须经新审阅 / preserve CRLF 😀\r\n'.encode()
NODE = '本地画布原节点 😀 / retained node'
STEPS = ('seed', 'rename-old', 'reject-old', 'cancel-old', 'reopen', 'rename-new',
         'rename-apply', 'renamed-cold', 'delete-cancel-preview', 'delete-cancel',
         'delete-preview', 'delete-apply', 'deleted-cold', 'seed-renamed')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def paths(kind):
    require(kind in ('md', 'canvas'), 'Unsafe owned local file kind')
    return 'fileops-original.' + kind, 'fileops/资料😀.' + kind


def file_steps(kind, remaining, *, canvas_delete=False):
    paths(kind)
    require(type(remaining) is bool and type(canvas_delete) is bool, 'Explicit file component selection required')
    if canvas_delete:
        require(kind == 'canvas' and not remaining, 'Canvas-only deletion scope cannot select other cases')
    if canvas_delete or remaining and kind == 'md':
        return ('seed-renamed', 'renamed-cold', 'delete-cancel-preview', 'delete-cancel', 'delete-preview', 'delete-apply', 'deleted-cold')
    return tuple(step for step in STEPS if step != 'seed-renamed')


def changed_content(kind, original):
    require(type(original) is bytes and 0 < len(original) <= 16384, 'Unexpected owned seed size')
    paths(kind)
    if kind == 'md':
        require(original == BODY, 'Original local MD seed differs')
        return UPDATED
    value = json.loads(original.decode('utf-8', 'strict'))
    require(type(value) is dict and type(value.get('nodes')) is list and len(value['nodes']) == 1
            and value['nodes'][0].get('type') == 'text' and value['nodes'][0].get('text') == NODE
            and 'vendor-file-actions' not in value, 'Original local Canvas seed differs')
    value['vendor-file-actions'] = {'original': '原字段 😀', 'opaque': [True, 17, None]}
    value['nodes'][0]['vendor-file-actions'] = {'retain': '不重写'}
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\r\n').encode()


def frozen(snapshot, log, kind, original_bytes, action, destination, *, state='prepared', expected_id=None):
    original, renamed = paths(kind)
    require(type(log) is str and len(log.encode()) <= 65536, 'Unbounded original file review log')
    rows = re.findall(r'^ORIGINAL_FILE_FREEZE (' + UUID + r') ([0-9a-f]{64})$', log, re.MULTILINE)
    require(len(rows) == 1, 'No unique original visible file review')
    op, digest = rows[0]
    require(expected_id is None or op == expected_id, 'Original review ID changed')
    found = [row for row in snapshot['mutations'] if row['operation_id'] == op]
    require(len(found) == 1, 'Visible file review is not its original native intent')
    value = found[0]
    require(value['kind'] == action and value['path'] == (original if action == 'rename' else renamed)
            and value['destination'] == destination and value['expected'] == digest == hashlib.sha256(original_bytes).hexdigest()
            and value['state'] == state and re.fullmatch(UUID, value['file_id']), 'Original reviewed file metadata differs')
    require(len(snapshot['files']) == 1 and snapshot['files'][0]['id'] == value['file_id']
            and snapshot['files'][0]['deleted'] == 0, 'Visible review replaced the owned live file identity')
    require(not any(row['operation_id'] == op for row in snapshot['operations'])
            and not any(row['operation_id'] == op for row in snapshot['outbox']), 'A preview/rejection executed a file mutation')
    return value


def committed(snapshot, intent, expected_bytes, *, deleted):
    require(type(deleted) is bool, 'Invalid local file result')
    rows = [row for row in snapshot['mutations'] if row['operation_id'] == intent['operation_id']]
    require(len(rows) == 1 and rows[0]['state'] == 'committed' and rows[0]['error'] is None,
            'Original local file action is not committed')
    for key in ('operation_id', 'kind', 'file_id', 'path', 'destination', 'expected'):
        require(rows[0][key] == intent[key], 'Original file action identity changed')
    require(len(snapshot['files']) == 1, 'Original file identity is missing or duplicated')
    entry = snapshot['files'][0]
    require(entry['id'] == intent['file_id'] and entry['path'] == (intent['path'] if deleted else intent['destination'])
            and entry['hash'] == hashlib.sha256(expected_bytes).hexdigest()
            and entry['deleted'] == int(deleted), 'Original file result does not match reviewed content or identity')
    receipts = [r for r in snapshot['operations'] if r['operation_id'] == intent['operation_id']]
    outbox = [r for r in snapshot['outbox'] if r['operation_id'] == intent['operation_id']]
    require(len(receipts) == len(outbox) == 1 and receipts[0]['state'] == 'committed'
            and outbox[0]['file_id'] == entry['id'] and outbox[0]['hash'] == ('' if deleted else entry['hash'])
            and outbox[0]['path'] == entry['path'] and outbox[0]['revision'] == entry['revision']
            and outbox[0]['operation'] == ('delete' if deleted else 'put'), 'Original file receipt/outbox is inconsistent')
    result = json.loads(receipts[0]['result'])
    require(result['file_id'] == entry['id'] and result['path'] == entry['path'] and result['hash'] == entry['hash']
            and result['deleted'] is deleted, 'Original native result differs from its persisted file')
    if deleted:
        require(not snapshot['drafts'], 'Clean deleted file draft survived as a live document')
    else:
        require(len(snapshot['drafts']) == 1 and snapshot['drafts'][0]['file_id'] == entry['id']
                and snapshot['drafts'][0]['path'] == entry['path']
                and snapshot['drafts'][0]['content'].encode() == expected_bytes
                and snapshot['drafts'][0]['generation'] == snapshot['drafts'][0]['saved_generation'],
                'Rename lost the original clean draft or rewrote its content')
    return {'operation_id': intent['operation_id'], 'fingerprint': receipts[0]['fingerprint'],
            'file_id': entry['id'], 'revision': entry['revision'], 'deleted': deleted}


def snapshot(probe, kind, label, file_id=None):
    original, renamed = paths(kind)
    columns = {'drafts': 'id,file_id,path,base_hash,generation,saved_generation,content,edit_session',
               'save_intents': 'operation_id,draft_id,generation,file_id,path,expected,content,state,error',
               'mutation_intents': 'operation_id,kind,file_id,path,destination,expected,state,error'}
    value = {}
    for table, fields in columns.items():
        # Creation intents keep their original NULL file_id; the native commit
        # allocates the file ID in its result. Never rewrite or lose that intent
        # when later observing the same file, including after draft deletion.
        where, params = (('file_id=?', (file_id,)) if file_id and table != 'save_intents'
                         else ('path IN (?,?)', (original, renamed)))
        name = {'save_intents': 'saves', 'mutation_intents': 'mutations'}.get(table, table)
        value[name] = [dict(zip(fields.split(','), row)) for row in probe.database_rows(
            'SELECT ' + fields + ' FROM ' + table + ' WHERE ' + where + ' ORDER BY rowid', params)]
    require(len(value['drafts']) <= 1 and len(value['saves']) == 1 and len(value['mutations']) <= 4,
            'Unexpected own local file action rows')
    if file_id is None:
        require(len(value['drafts']) == 1, 'Own seed draft is missing')
        file_id = value['drafts'][0]['file_id']
    require(type(file_id) is str and re.fullmatch(UUID, file_id), 'Invalid original file identity')
    folder = Path(tempfile.mkdtemp(prefix='owned-file-action-metadata-', dir=probe.tools))
    db_path = folder / 'host.sqlite3'
    for suffix in ('', '-wal', '-shm'):
        result = probe.private(probe.root + 'vault/.ainote/host.sqlite3' + suffix, check=False)
        if result.returncode == 0:
            Path(str(db_path) + suffix).write_bytes(result.stdout)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'Own native file metadata integrity failed')
        fields = 'id,path,hash,revision,deleted'
        value['files'] = [dict(zip(fields.split(','), row)) for row in db.execute(
            'SELECT ' + fields + ' FROM files WHERE id=?', (file_id,))]
        fields = 'operation_id,file_id,revision,path,hash,operation,state'
        value['outbox'] = [dict(zip(fields.split(','), row)) for row in db.execute(
            'SELECT ' + fields + ' FROM outbox WHERE file_id=? ORDER BY rowid', (file_id,))]
        ids = [row['operation_id'] for row in value['saves'] + value['mutations']]
        fields = 'operation_id,fingerprint,state,result'
        value['operations'] = [dict(zip(fields.split(','), row)) for row in db.execute(
            'SELECT ' + fields + ' FROM operations WHERE operation_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY rowid', ids)]
    seed = value['saves'][0]
    receipts = [row for row in value['operations'] if row['operation_id'] == seed['operation_id']]
    require(seed['state'] == 'committed' and seed['file_id'] in (None, file_id)
            and (seed['file_id'] is not None or seed['expected'] == '')
            and len(receipts) == 1 and receipts[0]['state'] == 'committed'
            and json.loads(receipts[0]['result'])['file_id'] == file_id,
            'Original seed intent does not bind its native committed file result')
    (probe.output / (label + '-native.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return value


def intent(probe, kind, step, **details):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope in ('file-actions', 'file-actions-remaining', 'canvas-file-delete') and step in (*STEPS, 'external-owned-change'), 'Explicit own file action required')
    original, renamed = paths(kind)
    data = {'run_id': os.environ['GITHUB_RUN_ID'], 'tools_source': probe.args.tools_commit,
            'compiled_source': probe.args.commit, 'kind': kind, 'step': step, 'original': original,
            'destination': renamed, 'automatic_retry': False, **details}
    with (probe.output / ('files-' + kind + '-' + step + '-original-intent.json')).open('xb') as output:
        output.write((json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())
        output.flush(); os.fsync(output.fileno())


def owned_concurrent_change(probe, kind, target, original, updated, reviewed_operation):
    require(target in paths(kind), 'Unrelated concurrent file target')
    intent(probe, kind, 'external-owned-change', original_operation_id=reviewed_operation, target_path=target,
           before_sha256=hashlib.sha256(original).hexdigest(), content_sha256=hashlib.sha256(updated).hexdigest(),
           explicitly_owned_filesystem_concurrency_not_GUI_input=True)
    require(probe.original(target) == original, 'Own source changed before the declared concurrency action')
    probe.device('shell', '-T', shlex.join(('run-as', APP, 'sh', '-c', 'cat > ' + shlex.quote(probe.root + 'vault/' + target))), input=updated)
    require(probe.original(target) == updated, 'Declared own concurrency bytes differ')


def run_file_actions(probe):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope in ('file-actions', 'file-actions-remaining', 'canvas-file-delete'), 'Local file action scope must be explicit')
    remaining = probe.args.scope == 'file-actions-remaining'
    canvas_only = probe.args.scope == 'canvas-file-delete'
    proof = {'passed': False, 'scope': probe.args.scope, 'run_id': os.environ['GITHUB_RUN_ID'],
             'tools_source': probe.args.tools_commit, 'compiled_source': probe.args.commit,
             'APK_sha256': probe.binding['sha256'], 'cases': {},
             'physical_phone_or_write_interruption_or_full_A2_A3C_verified': False}
    try:
        probe.enable_test_ime()
        for kind in (('canvas',) if canvas_only else ('md', 'canvas')):
            result = {'passed': False, 'phase': 'seed', 'cold_processes': []}; proof['cases'][kind] = result
            original, renamed = paths(kind)
            delete_only = canvas_only or remaining and kind == 'md'
            planned = file_steps(kind, remaining, canvas_delete=canvas_only); executed = []
            result['intended_steps'] = list(planned)
            result['MD_rename_exercised_in_this_fixture'] = not delete_only if kind == 'md' else False
            result['Canvas_rename_exercised_in_this_fixture'] = not delete_only if kind == 'canvas' else False
            def phase(step, *, operation_id=None):
                require(len(executed) < len(planned) and step == planned[len(executed)], 'Original remaining file phase changed or repeated')
                result['phase'] = step
                cold = step in ('reopen', 'renamed-cold', 'deleted-cold')
                old_pid = probe.shell('pidof', APP).stdout.strip() if cold else None
                intent(probe, kind, step, original_operation_id=operation_id)
                probe.phase('files-' + kind + '-' + step,
                            continue_process=step not in ('seed', 'seed-renamed', 'reopen', 'renamed-cold', 'deleted-cold'), keep_running=True)
                if cold:
                    new_pid = probe.shell('pidof', APP).stdout.strip()
                    require(re.fullmatch(rb'[1-9][0-9]{0,9}', old_pid) and re.fullmatch(rb'[1-9][0-9]{0,9}', new_pid)
                            and old_pid != new_pid, 'Own local file cold process was not replaced')
                    result['cold_processes'].append({'step': step, 'before': int(old_pid), 'after': int(new_pid)})
                observed = snapshot(probe, kind, 'files-' + kind + '-' + step, result.get('file_id'))
                executed.append(step); result['executed_steps'] = list(executed)
                return observed
            seed_target = renamed if delete_only else original
            seed = phase('seed-renamed' if delete_only else 'seed'); raw = probe.original(seed_target)
            require(len(seed['files']) == 1 and not seed['mutations'], 'Local seed file is not unique')
            result['file_id'] = seed['files'][0]['id']
            require(seed['files'][0]['path'] == seed_target and seed['files'][0]['hash'] == hashlib.sha256(raw).hexdigest()
                    and seed['saves'][0]['state'] == 'committed', 'Original GUI seed was not saved')
            updated = changed_content(kind, raw)
            if delete_only:
                owned_concurrent_change(probe, kind, renamed, raw, updated, None)
                cold = phase('renamed-cold')
                require(cold['files'][0]['id'] == result['file_id'] and cold['files'][0]['path'] == renamed
                        and probe.original(renamed) == updated, 'Fresh direct-destination original changed before deletion')
                result['original_creation_operation_id'] = seed['saves'][0]['operation_id']
                result['old_rename_component_not_replayed'] = True
            else:
                old = phase('rename-old')
                review = frozen(old, (probe.output / ('files-' + kind + '-rename-old.log')).read_text('utf-8'), kind, raw, 'rename', renamed)
                owned_concurrent_change(probe, kind, original, raw, updated, review['operation_id'])
                rejected = phase('reject-old', operation_id=review['operation_id'])
                frozen(rejected, (probe.output / ('files-' + kind + '-reject-old.log')).read_text('utf-8'), kind, raw,
                       'rename', renamed, state='rejected', expected_id=review['operation_id'])
                require(next(x for x in rejected['mutations'] if x['operation_id'] == review['operation_id'])['error'] == 'REVISION_CONFLICT',
                        'Old frozen action was not rejected as the actual concurrent content change')
                cancelled = phase('cancel-old', operation_id=review['operation_id'])
                require(next(x for x in cancelled['mutations'] if x['operation_id'] == review['operation_id'])['state'] == 'cancelled'
                        and not any(x['operation_id'] == review['operation_id'] for x in cancelled['operations']), 'Rejected own action executed on cancellation')
                phase('reopen'); require(probe.original(original) == updated, 'Cold open rewrote concurrent original bytes')
                fresh = phase('rename-new')
                rename = frozen(fresh, (probe.output / ('files-' + kind + '-rename-new.log')).read_text('utf-8'), kind, updated, 'rename', renamed)
                require(rename['operation_id'] != review['operation_id'] and rename['file_id'] == result['file_id'], 'New review replaced original file identity')
                renamed_state = phase('rename-apply', operation_id=rename['operation_id'])
                result['rename'] = committed(renamed_state, rename, updated, deleted=False)
                require(probe.original(renamed) == updated, 'Rename changed original bytes')
                require(probe.shell('run-as', APP, 'test', '!', '-e', probe.root + 'vault/' + original, check=False).returncode == 0, 'Old local path still exists')
                cold = phase('renamed-cold'); require(committed(cold, rename, updated, deleted=False) == result['rename']
                        and cold['operations'] == renamed_state['operations'] and cold['outbox'] == renamed_state['outbox'], 'Cold rename replayed an original action')
            preview = phase('delete-cancel-preview')
            cancelled_delete = frozen(preview, (probe.output / ('files-' + kind + '-delete-cancel-preview.log')).read_text('utf-8'), kind, updated, 'delete', '')
            cancelled = phase('delete-cancel', operation_id=cancelled_delete['operation_id'])
            require(next(x for x in cancelled['mutations'] if x['operation_id'] == cancelled_delete['operation_id'])['state'] == 'cancelled'
                    and not any(x['operation_id'] == cancelled_delete['operation_id'] for x in cancelled['operations'])
                    and probe.original(renamed) == updated, 'Cancelled delete changed original content or executed')
            preview = phase('delete-preview')
            delete = frozen(preview, (probe.output / ('files-' + kind + '-delete-preview.log')).read_text('utf-8'), kind, updated, 'delete', '')
            require(delete['operation_id'] != cancelled_delete['operation_id'], 'Deletion reused cancelled intent')
            deleted = phase('delete-apply', operation_id=delete['operation_id'])
            result['delete'] = committed(deleted, delete, updated, deleted=True)
            require(probe.shell('run-as', APP, 'test', '!', '-e', probe.root + 'vault/' + renamed, check=False).returncode == 0, 'Deleted own file still exists')
            trash = probe.private(probe.root + 'vault/.ainote/trash/' + delete['operation_id']).stdout
            require(trash == updated, 'Owned original deleted bytes were not retained in the original trash entry')
            (probe.output / ('files-' + kind + '-deleted-original.' + kind)).write_bytes(trash)
            cold = phase('deleted-cold')
            require(committed(cold, delete, updated, deleted=True) == result['delete']
                    and cold['mutations'] == deleted['mutations'] and cold['operations'] == deleted['operations']
                    and cold['outbox'] == deleted['outbox'], 'Cold deletion resurrected or repeated an original operation')
            require(probe.shell('run-as', APP, 'test', '!', '-e', probe.root + 'vault/' + renamed, check=False).returncode == 0, 'Cold deletion path returned')
            require(tuple(executed) == planned, 'Original local file steps incomplete')
            result.update(passed=True, original_sha256=hashlib.sha256(raw).hexdigest(), retained_sha256=hashlib.sha256(updated).hexdigest(),
                          cancelled_delete_operation_id=cancelled_delete['operation_id'])
            if not delete_only:
                result['rejected_original_operation_id'] = review['operation_id']
        proof['passed'] = True
    except Exception as error:
        proof['failure_class'] = type(error).__name__
        if probe.root is not None and proof['cases']:
            try:
                snapshot(probe, kind, 'files-' + kind + '-failure-observation', result.get('file_id'))
                proof['original_failure_metadata_preserved'] = True
            except Exception as observed:
                proof['failure_observation_class'] = type(observed).__name__
        raise
    finally:
        (probe.output / 'file-actions-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        probe.force_stop()
