"""Explicit fresh-AVD file ENOSPC recovery; never fill /data or the Vault DB."""
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

CAPACITY = 16 * 1024**2
NOTE = 'full-area/original.md'
ORIGINAL = '# 原始文件 😀\nA confirmed original before the owned filesystem is full.\n'.encode()
UPDATED = ORIGINAL + '空间不足时保留此草稿 / retry this exact original content\n'.encode()


def action_intent(probe, phase, **details):
    require_hosted_emulator(probe.args.serial)
    require(phase in ('storage-mount', 'storage-seed', 'storage-fill', 'storage-fail', 'storage-check',
                      'storage-release', 'storage-retry', 'storage-cold'), 'Unsafe storage action phase')
    value = {'run_id': os.environ['GITHUB_RUN_ID'], 'tools_source': probe.args.tools_commit,
             'compiled_source': probe.args.commit, 'phase': phase, 'path': NOTE,
             'automatic_retry': False, 'native_operation_id': False, **details}
    with (probe.output / (phase + '-original-intent.json')).open('xb') as f:
        f.write((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()); f.flush(); os.fsync(f.fileno())


def storage_paths(home, relative, run_id):
    require(type(home) is str and home in ('/data/user/0/' + APP, '/data/data/' + APP), 'Storage app home is not the owned user0 app')
    require(type(relative) is str and re.fullmatch(r'\./(?:[A-Za-z0-9_.-]+/)+', relative)
            and all(p not in ('.', '..') for p in relative[2:].split('/')[:-1]), 'Unsafe original private storage root')
    require(type(run_id) is str and re.fullmatch(r'[1-9][0-9]{0,17}', run_id), 'Storage requires its original run ID')
    parent = home + '/' + relative[2:] + 'vault'
    return parent, parent + '/full-area', '.owned-enospc-' + run_id


def file_identity(raw, uid):
    require(type(raw) is bytes and len(raw) < 256, 'Unbounded owned file identity')
    fields = raw.decode('ascii', 'strict').strip().split(':')
    require(len(fields) == 6 and all(re.fullmatch(r'[0-9]+', f) for f in fields), 'Malformed owned file identity')
    owner, group, mode, inode, device, size = map(int, fields)
    require(owner == group == uid and 10000 <= uid < 100000 and mode in (600, 700)
            and inode > 0 and device > 0 and 0 <= size <= CAPACITY, 'Owned storage identity changed')
    return {'uid': owner, 'gid': group, 'mode': mode, 'inode': inode, 'device': device, 'bytes': size}


def space(raw):
    require(type(raw) is bytes and len(raw) < 256, 'Unbounded filesystem observation')
    fields = raw.decode('ascii', 'strict').strip().split(':')
    require(len(fields) == 4 and fields[0] == 'tmpfs' and all(re.fullmatch(r'[0-9]+', f) for f in fields[1:]),
            'Owned bounded tmpfs was not observed')
    blocks, block, available = map(int, fields[1:])
    require(blocks * block == CAPACITY and 0 <= available <= blocks and 512 <= block <= 65536,
            'Filesystem is not the exact bounded16MiB target')
    return {'capacity_bytes': CAPACITY, 'available_bytes': available * block}


def mount_identity(raw, target, source):
    require(type(raw) is bytes and len(raw) <= 256 * 1024, 'Unbounded owned mount observation')
    matches = []
    for line in raw.decode('ascii', 'strict').splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != target:
            continue
        require('-' in fields, 'Malformed original mount record')
        split = fields.index('-')
        require(fields[3] == '/' and fields[split + 1:split + 3] == ['tmpfs', source]
                and {'nodev', 'nosuid', 'noexec'} <= set(fields[5].split(',')), 'Unexpected original mount identity')
        matches.append({'mount_id': int(fields[0]), 'device': fields[2], 'target': target, 'source': source})
    require(len(matches) == 1, 'Owned bounded mount is missing or duplicated')
    return matches[0]


def original_pending(before, failed):
    require(len(before['saves']) == 1 and before['saves'][0]['state'] == 'committed', 'Original seed save is not unique')
    pending = [s for s in failed['saves'] if s['state'] == 'prepared']
    require(len(failed['saves']) == 2 and len(pending) == 1 and pending[0]['content'].encode() == UPDATED
            and pending[0]['error'] == 'FILESYSTEM_ERROR', 'Actual failed original save was not preserved')
    intent = pending[0]
    require(len(failed['drafts']) == len(failed['files']) == 1
            and failed['saves'][0] == before['saves'][0]
            and failed['drafts'][0]['id'] == before['drafts'][0]['id']
            and failed['drafts'][0]['content'].encode() == UPDATED
            and failed['drafts'][0]['generation'] == intent['generation']
            and failed['drafts'][0]['saved_generation'] == before['drafts'][0]['saved_generation']
            and failed['files'] == before['files'] and failed['outbox'] == before['outbox'],
            'File ENOSPC changed the original saved metadata or lost its actual durable draft')
    require(intent['file_id'] == before['drafts'][0]['file_id'] and intent['expected'] == hashlib.sha256(ORIGINAL).hexdigest()
            and intent['generation'] > before['drafts'][0]['saved_generation'], 'Failed intent replaced the original file or generation')
    rows = [r for r in failed['operations'] if r['operation_id'] == intent['operation_id']]
    journal = [r for r in failed['journal'] if r['operation_id'] == intent['operation_id']]
    payload = [r for r in failed['payloads'] if r['operation_id'] == intent['operation_id']]
    require(len(rows) == len(journal) == len(payload) == 1 and rows[0]['state'] == journal[0]['state'] == 'pending'
            and payload[0]['hash'] == hashlib.sha256(UPDATED).hexdigest() and payload[0]['size'] == len(UPDATED)
            and not any(r['operation_id'] == intent['operation_id'] for r in failed['outbox']),
            'File ENOSPC did not retain the original accepted journal outside its filesystem')
    return intent


def snapshot(probe, label):
    # Only the new fixture's one note and its original operation IDs are read.
    draft_columns = 'id,file_id,path,base_hash,generation,saved_generation,content,edit_session'
    save_columns = 'operation_id,draft_id,generation,file_id,path,expected,content,state,error'
    value = {'drafts': [dict(zip(draft_columns.split(','), r)) for r in probe.database_rows(
        'SELECT ' + draft_columns + ' FROM drafts WHERE path=?', (NOTE,))],
        'saves': [dict(zip(save_columns.split(','), r)) for r in probe.database_rows(
        'SELECT ' + save_columns + ' FROM save_intents WHERE path=? ORDER BY rowid', (NOTE,))]}
    require(len(value['drafts']) == 1 and 1 <= len(value['saves']) <= 2, 'Unexpected own storage fixture rows')
    folder = Path(tempfile.mkdtemp(prefix='owned-storage-metadata-', dir=probe.tools)); db_path = folder / 'host.sqlite3'
    for suffix in ('', '-wal', '-shm'):
        r = probe.private(probe.root + 'vault/.ainote/host.sqlite3' + suffix, check=False)
        if r.returncode == 0:
            Path(str(db_path) + suffix).write_bytes(r.stdout)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'Owned native metadata integrity failed')
        for table, columns in (('journal', 'operation_id,file_id,path,expected,state'),
                               ('outbox', 'operation_id,file_id,revision,path,hash,operation,state'),
                               ('files', 'id,path,hash,revision,deleted')):
            value[table] = [dict(zip(columns.split(','), row)) for row in db.execute(
                'SELECT ' + columns + ' FROM ' + table + ' WHERE path=? ORDER BY rowid', (NOTE,))]
        ids = [s['operation_id'] for s in value['saves']]
        for table, columns in (('operations', 'operation_id,fingerprint,state,result'), ('payloads', 'operation_id,hash,size')):
            value[table] = [dict(zip(columns.split(','), row)) for row in db.execute(
                'SELECT ' + columns + ' FROM ' + table + ' WHERE operation_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY rowid', ids)]
    (probe.output / (label + '-native.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return value


class OwnedFilesystem:
    def __init__(self, probe):
        require_hosted_emulator(probe.args.serial)
        require(probe.args.scope == 'storage-recovery' and probe.root is not None, 'Explicit initialized storage scope required')
        self.probe = probe
        home = probe.shell('run-as', APP, 'pwd').stdout.decode('ascii', 'strict').strip()
        home = probe.shell('readlink', '-f', home).stdout.decode('ascii', 'strict').strip()
        self.parent, self.target, self.filler_name = storage_paths(home, probe.root, os.environ['GITHUB_RUN_ID'])
        self.source = 'onx-owned-' + os.environ['GITHUB_RUN_ID']
        raw_uid = probe.shell('run-as', APP, 'id', '-u').stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{4}', raw_uid), 'Unexpected owned app UID')
        self.uid = int(raw_uid)
        self.mounted = None
        self.filler = None
        self.observed = {'owned_app_home': home, 'original_private_root': probe.root, 'original_UID': self.uid}

    def stat(self, path):
        return file_identity(self.probe.shell('stat', '-c', '%u:%g:%a:%i:%d:%s', path).stdout, self.uid)

    def verify(self):
        p = self.probe
        raw = p.shell('cat', '/proc/self/mountinfo').stdout
        self.observed['root_mount_lines'] = [line for line in raw.decode('ascii', 'strict').splitlines()
                                             if self.target in line.split()]
        current = mount_identity(raw, self.target, self.source)
        require(self.mounted is None or current == self.mounted, 'Original owned mount changed')
        pid = p.shell('pidof', APP).stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{0,9}', pid), 'Owned original app process missing or ambiguous')
        raw = p.shell('cat', '/proc/' + pid.decode() + '/mountinfo').stdout
        self.observed['app_mount_lines'] = [line for line in raw.decode('ascii', 'strict').splitlines()
                                            if self.target in line.split()]
        inside = mount_identity(raw, self.target, self.source)
        require(inside['device'] == current['device'] and inside['source'] == current['source'], 'App namespace cannot see owned filesystem')
        raw = p.shell('stat', '-f', '-c', '%T:%b:%S:%a', self.target).stdout
        self.observed['last_bounded_space_stat'] = raw.decode('ascii', 'strict').strip()
        return space(raw)

    def prepare(self):
        p = self.probe
        require_hosted_emulator(p.args.serial)
        require(p.shell('test', '!', '-e', self.target, check=False).returncode == 0, 'Storage target already exists; never replace it')
        # Supported root is requested only on this new hosted emulator. Refusal
        # is a platform capability failure, never a reason to weaken a policy.
        root = p.device('root', check=False)
        self.observed['supported_root_request'] = {'exit': root.returncode,
            'stdout': root.stdout[:1024].decode('utf-8', 'replace'), 'stderr': root.stderr[:1024].decode('utf-8', 'replace')}
        require(root.returncode == 0 and root.stdout.strip() in (b'restarting adbd as root', b'adbd is already running as root'),
                'Owned AVD root capability unavailable; no mount attempted')
        p.wait_owned_connection()
        require(p.shell('id', '-u').stdout.strip() == b'0' and p.shell('getenforce').stdout.strip() == b'Enforcing',
                'Owned AVD root/enforcing identity not observed')
        p.shell('run-as', APP, 'mkdir', self.target)
        label = p.shell('ls', '-Zd', self.target).stdout.decode('ascii', 'strict').split()[0]
        self.observed['original_new_directory_SELinux_label'] = label
        require(re.fullmatch(r'u:object_r:app_data_file:s0(?::c[0-9]+(?:,c[0-9]+)*)?', label), 'Original app directory label not recognized')
        # New mount gets exactly the original app owner/mode/label. No existing
        # data directory, policy, app permission or global filesystem changes.
        p.shell('mount', '-t', 'tmpfs', '-o', f'size={CAPACITY},mode=0700,uid={self.uid},gid={self.uid},nodev,nosuid,noexec', self.source, self.target)
        p.shell('chcon', label, self.target)
        self.mounted = mount_identity(p.shell('cat', '/proc/self/mountinfo').stdout, self.target, self.source)
        require(self.stat(self.target)['mode'] == 700, 'Original owned mount permissions differ')
        observed = self.verify()
        # Databases and journal are intentionally outside the full filesystem.
        require(p.shell('stat', '-c', '%d', self.parent + '/.ainote/host.sqlite3').stdout.strip()
                != p.shell('stat', '-c', '%d', self.target).stdout.strip(), 'Native journal shares full filesystem')
        return {'mount': self.mounted, 'space': observed, 'original_label_preserved': True,
                'new_owned_mount_only': True, 'global_data_fill_or_policy_change': False}

    def fill(self):
        require(self.verify()['available_bytes'] > 0 and self.filler is None, 'Original fill is not a new bounded action')
        target = self.target + '/' + self.filler_name
        require(self.probe.shell('test', '!', '-e', target, check=False).returncode == 0, 'Owned filler already exists')
        command = 'umask 077; set -C; : > ' + shlex.quote(target) + ' && dd if=/dev/zero of=' + shlex.quote(target) + ' bs=4096 count=4096'
        result = self.probe.shell('run-as', APP, 'sh', '-c', command, check=False)
        require(result.returncode != 0 and b'No space left on device' in result.stdout + result.stderr,
                'Original bounded filler did not observe actual ENOSPC; no Save sent')
        self.filler = self.stat(target)
        require(self.filler['mode'] == 600 and self.filler['bytes'] > CAPACITY // 2
                and self.verify()['available_bytes'] == 0, 'Owned file filesystem is not actually full')
        return {'file': self.filler, 'actual_ENOSPC_from_owned_app_UID': True, 'bounded_bytes': CAPACITY}

    def release_filler(self):
        self.verify()
        require(self.filler is not None, 'No original owned filler to release')
        target = self.target + '/' + self.filler_name
        require(self.stat(target) == self.filler, 'Original filler identity changed; do not remove it')
        self.probe.shell('run-as', APP, 'rm', target)
        require(self.verify()['available_bytes'] > CAPACITY // 2, 'Original bounded space did not return')


def run_storage_recovery(probe):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope == 'storage-recovery', 'Storage recovery must be explicit')
    proof = {'passed': False, 'scope': 'storage-recovery', 'compiled_source': probe.args.commit,
             'tools_source': probe.args.tools_commit, 'APK_sha256': probe.binding['sha256'],
             'run_id': os.environ['GITHUB_RUN_ID'], 'physical_phone_or_Canvas_or_write_interruption_verified': False}
    output = probe.output / 'storage-recovery-proof.json'
    try:
        proof['stage'] = 'initialize-owned-native-UI'
        probe.phase('storage-init', keep_running=True)
        fs = OwnedFilesystem(probe)
        proof['platform'] = fs.observed
        proof['stage'] = 'admit-new-bounded-filesystem'
        action_intent(probe, 'storage-mount', target=fs.target, maximum_bytes=CAPACITY)
        proof['filesystem'] = fs.prepare()
        probe.enable_test_ime()
        proof['stage'] = 'one-original-seed-Save'
        action_intent(probe, 'storage-seed', original_sha256=hashlib.sha256(ORIGINAL).hexdigest())
        probe.phase('storage-seed', continue_process=True, keep_running=True)
        fs.verify()
        require(probe.original(NOTE) == ORIGINAL, 'Original seeded file differs before filling')
        before = snapshot(probe, 'storage-seed')
        proof['stage'] = 'one-bounded-owned-filler'
        action_intent(probe, 'storage-fill', source=fs.source, filler=fs.filler_name, maximum_bytes=CAPACITY)
        proof['fill'] = fs.fill()
        proof['stage'] = 'one-new-Save-under-real-file-ENOSPC'
        action_intent(probe, 'storage-fail', original_content_sha256=hashlib.sha256(UPDATED).hexdigest())
        probe.phase('storage-fail', continue_process=True, keep_running=True)
        require(probe.original(NOTE) == ORIGINAL, 'Failed Save replaced the acknowledged original')
        failed = snapshot(probe, 'storage-failed'); original = original_pending(before, failed)
        proof['original_operation_id'] = original['operation_id']
        proof['stage'] = 'read-original-pending-result'
        action_intent(probe, 'storage-check', native_operation_id=original['operation_id'])
        probe.phase('storage-check', continue_process=True, keep_running=True)
        checked = snapshot(probe, 'storage-checked')
        require(checked == failed and probe.original(NOTE) == ORIGINAL, 'Original result check changed the accepted action or file')
        proof['stage'] = 'release-only-identity-checked-filler'
        action_intent(probe, 'storage-release', original_filler=fs.filler)
        fs.release_filler()
        proof['stage'] = 'explicit-same-ID-original-content-retry'
        action_intent(probe, 'storage-retry', native_operation_id=original['operation_id'], original_content_sha256=hashlib.sha256(UPDATED).hexdigest())
        probe.phase('storage-retry', continue_process=True, keep_running=True)
        recovered = snapshot(probe, 'storage-recovered')
        require(probe.original(NOTE) == UPDATED, 'Explicit original retry did not preserve exact requested bytes')
        require(len(recovered['saves']) == 2 and recovered['saves'][1]['operation_id'] == original['operation_id']
                and recovered['saves'][1]['state'] == 'committed' and recovered['saves'][1]['error'] is None
                and not recovered['journal'] and len(recovered['outbox']) == 2
                and recovered['operations'][1]['operation_id'] == original['operation_id']
                and recovered['operations'][1]['fingerprint'] == failed['operations'][1]['fingerprint']
                and recovered['operations'][1]['state'] == 'committed'
                and recovered['files'][0]['id'] == before['files'][0]['id']
                and recovered['drafts'][0]['generation'] == recovered['drafts'][0]['saved_generation'],
                'Recovery replaced the original operation, file identity or saved generation')
        old_pid = probe.shell('pidof', APP).stdout.strip()
        proof['stage'] = 'new-process-cold-original-read'
        action_intent(probe, 'storage-cold', native_operation_id=original['operation_id'], original_PID=int(old_pid))
        probe.phase('storage-cold', keep_running=True)
        fs.verify()
        require(probe.shell('pidof', APP).stdout.strip() != old_pid and probe.original(NOTE) == UPDATED,
                'New original process did not reopen exact recovered bytes')
        cold = snapshot(probe, 'storage-cold')
        require(cold['saves'] == recovered['saves'] and cold['operations'] == recovered['operations']
                and cold['outbox'] == recovered['outbox'], 'Cold reopening replayed a Save or changed its receipt')
        proof.update(passed=True, original_file_sha256=hashlib.sha256(ORIGINAL).hexdigest(),
                     recovered_file_sha256=hashlib.sha256(UPDATED).hexdigest(),
                     original_id_retry_once=True, cold_original_receipt_preserved=True,
                     metadata_and_drafts_outside_full_filesystem=True)
    except Exception as error:
        proof['failure_class'] = type(error).__name__
        if probe.root is not None:
            try:
                probe.original(NOTE)
                snapshot(probe, 'storage-failure-observation')
                proof['failure_originals_preserved'] = True
            except Exception as observation:
                proof['failure_observation_class'] = type(observation).__name__
        raise
    finally:
        output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        probe.force_stop()
        # The normal owner-bound AVD teardown removes this ephemeral mount.
        # Actual original/final bytes and metadata are preserved in evidence.
