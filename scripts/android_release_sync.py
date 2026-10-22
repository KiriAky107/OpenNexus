"""Signed foreground GUI association/upload and independent original peer apply.

No debug product instance, WebView bridge, product IPC, trust change, mutation
retry or credential export. An HTTP peer is not a desktop product pair.
"""
import hashlib
import json
import os
import re
import shlex

from android_auth_fixture import read_json
from android_auth_runtime import active_intent, provider_account
from android_release_auth import ReleaseAuth, main
from android_release_observation import APP, DATABASES, StoppedReleaseObserver, exclusive
from android_release_sync_snapshot import ASSET, LOCAL, PEER, owned_paths, project_sync
from android_release_upgrade import ReleaseUpgrade
from android_runtime_probe import BODY, DRIVER, IMAGE_BYTES, NODE, require, require_hosted_emulator
from android_sync_fixture import observe_provider
from android_sync_peer import Peer
from android_sync_setup import Setup
# Reuse only this bounded immutable-object GET, never the debug SyncProbe.
from android_sync_runtime import read_original_object
from release_plan import atomic_json, digest

PHASES = ('login-a', 'associate', 'association-original', 'status', 'cycle', 'cycle-incoming')
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
PEER_NOTE = '# 独立协议客户端 😀\r\n\r\n保留原始 Markdown / signed foreground.\r\n'.encode()
PEER_CANVAS = (json.dumps(dict(nodes=[dict(id='original-peer-text', type='text', x=0, y=0, width=300, height=180,
    text='独立客户端画布 😀'), dict(id='original-peer-image', type='file', x=0, y=220, width=320, height=240, file=ASSET)],
    edges=[], vendor=dict(original='未知字段与 CRLF 保留')), ensure_ascii=False, indent=2).replace('\n', '\r\n') + '\r\n').encode()


class ReleasePeer(Peer):
    paths = frozenset(PEER + (ASSET,))


def same_projection(before, after):
    # Separate logical state/content from SQLite WAL/checkpoint layout changes
    # across cold processes. Every individual raw snapshot is still hash-bound.
    require({k: v for k, v in before.items() if k != 'raw_sha256'} ==
            {k: v for k, v in after.items() if k != 'raw_sha256'}, 'RELEASE_SYNC_READ_CHANGED_LOGICAL_RECORDS')
    content = {'vault/' + row['path'] for row in before['files']}
    require({k: v for k, v in before['raw_sha256'].items() if k in content} ==
            {k: v for k, v in after['raw_sha256'].items() if k in content},
            'RELEASE_SYNC_READ_CHANGED_CONTENT_BYTES')


def validate_association(original, native, prepared, login):
    binding, receipt = native['binding'], native['association']
    require(binding is not None and receipt is not None and set(original) == {'operation_id', 'preview_fingerprint', 'remote_vault'}
            and re.fullmatch(UUID, original['operation_id']) and re.fullmatch('[0-9a-f]{64}', original['preview_fingerprint'])
            and binding['remote_vault'] == prepared['vault']['vault_id'] == original['remote_vault']
            and all(receipt[k] == v for k, v in original.items()) and receipt['auth_operation_id'] == login['operation_id']
            and binding['endpoint'] == login['endpoint'] and binding['account'] == login['account']
            and prepared['device_id'] != login['device_id'], 'RELEASE_SYNC_FROZEN_ASSOCIATION_IDENTITY')


def validate_upload(provider, projection, device):
    native = projection['native']; remote = provider['rows']['revisions']; jobs = native['jobs']
    require(0 < len(remote) == len(jobs) <= 16 and provider['vault']['id'] == native['binding']['remote_vault']
            and not native['conflicts'], 'RELEASE_SYNC_UPLOAD_SCOPE')
    by_id = {v['operation_id']: v for v in jobs}; attempts = {v['operation_id']: v for v in native['attempts']}
    issuers = {v['operation_id']: v['device_id'] for v in native['issuers']}
    require(len(by_id) == len(jobs) and len(attempts) == len(native['attempts']) == len(jobs)
            and len(issuers) == len(native['issuers']) == len(jobs) and set(by_id) == set(attempts) == set(issuers),
            'RELEASE_SYNC_ORIGINAL_JOB_ATTEMPT_ISSUER')
    outbox = {v['operation_id']: v for v in native['outbox']}
    for value in remote:
        job = by_id.get(value['operation_id']); attempt = attempts.get(value['operation_id'])
        require(job is not None and job['path'] in LOCAL and job['state'] == 'acked' and job['error'] is None
                and value['device_id'] == device == issuers[job['operation_id']]
                and all(value[k] == job[k] for k in ('file_id', 'path', 'hash', 'size'))
                and value['sequence'] == job['remote_revision'] and value['operation'] == 'put'
                and attempt == dict(operation_id=job['operation_id'], attempts=1, outcome='succeeded', error=None),
                'RELEASE_SYNC_UNCONFIRMED_OR_CHANGED_UPLOAD')
        record = outbox.get(job['operation_id'])
        require(record is not None and record['state'] == 'acked'
                and all(record[k] == job[k] for k in ('file_id', 'path', 'hash')), 'RELEASE_SYNC_ORIGINAL_OUTBOX_UNCONFIRMED')
    heads = {v['path']: v for v in native['heads']}
    require(set(heads) == set(LOCAL) and len(native['heads']) == len(LOCAL), 'RELEASE_SYNC_UPLOAD_HEAD_SET')
    latest = []
    for path in LOCAL:
        value = max((v for v in remote if v['path'] == path), key=lambda v: v['sequence'])
        require(value['hash'] == projection['raw_sha256']['vault/' + path]
                and heads[path] == dict(file_id=value['file_id'], revision=value['sequence'], path=path, hash=value['hash']),
                'RELEASE_SYNC_ORIGINAL_FILE_OR_HEAD_CHANGED')
        latest.append(value)
    return latest


def validate_incoming(receipts, provider, before, after, device):
    require(len(receipts) == 3 and {v['path'] for v in receipts} == set(PEER + (ASSET,))
            and len({v['operation_id'] for v in receipts}) == 3, 'RELEASE_SYNC_PEER_ORIGINAL_SET')
    require(after['binding']['id'] == before['binding']['id'] and after['binding']['cursor'] == provider['vault']['sequence']
            and not after['conflicts'] and all(after[k] == before[k] for k in ('jobs', 'attempts', 'issuers', 'association', 'outbox')),
            'RELEASE_SYNC_INCOMING_CHANGED_ORIGINAL_UPLOADS')
    for receipt in receipts:
        remote = [v for v in provider['rows']['revisions'] if v['operation_id'] == receipt['operation_id']]
        inbox = [v for v in after['inbox'] if v['sequence'] == receipt['sequence']]
        head = [v for v in after['heads'] if v['file_id'] == receipt['file_id']]
        require(receipt['vault_id'] == provider['vault']['id'] and receipt['device_id'] != device
                and len(remote) == len(inbox) == len(head) == 1 and inbox[0]['state'] == 'applied'
                and all(remote[0][k] == receipt[k] for k in ('file_id', 'path', 'hash', 'size', 'sequence', 'device_id', 'fingerprint')),
                'RELEASE_SYNC_PEER_PROVIDER_OR_APPLY_MISSING')
        original = json.loads(inbox[0]['revision'])
        require(all(original[k] == receipt[k] for k in ('operation_id', 'file_id', 'base_revision', 'path', 'operation', 'hash', 'size', 'sequence', 'vault_id'))
                and head[0] == dict(file_id=receipt['file_id'], revision=receipt['sequence'], path=receipt['path'], hash=receipt['hash']),
                'RELEASE_SYNC_PEER_ORIGINAL_INBOX_OR_HEAD_CHANGED')
    require(all(v in after['heads'] for v in before['heads']) and all(v in after['inbox'] for v in before['inbox']),
            'RELEASE_SYNC_INCOMING_REPLACED_PRIOR_RECORDS')
    progress = {v['device_id']: v for v in provider['rows']['device_progress']}
    require(device in progress and progress[device]['offered_sequence'] == provider['vault']['sequence'],
            'RELEASE_SYNC_ACTUAL_GENERATED_PAGE_NOT_OBSERVED')


class ReleaseSync(ReleaseAuth):
    allowed_phases = PHASES
    driver_entry = 'ReleaseSyncRuntimeProbe'
    driver_namespace = 'RELEASE_SYNC'

    def stage_sync_input(self, vault):
        require_hosted_emulator(self.args.serial)
        require(set(vault) == {'operation_id', 'vault_id', 'name', 'delivery', 'server_operation_id_available'}
                and re.fullmatch(UUID, vault['operation_id']) and vault['server_operation_id_available'] is False
                and vault['delivery'] in ('original_response_received', 'reconciled_by_original_name_GET')
                and re.fullmatch('[0-9a-f]{32}', vault['vault_id'])
                and re.fullmatch('Owned Android Sync [0-9a-f]{32}', vault['name']), 'RELEASE_SYNC_EXACT_PRIVATE_VAULT_INPUT')
        value = dict(vault_id=vault['vault_id'], vault_name=vault['name'])
        self.device('shell', '-T', 'run-as ' + DRIVER + ' sh -c ' + shlex.quote(
            'umask 077; test ! -e no_backup/owned-sync.json && cat > no_backup/owned-sync.json'), input=json.dumps(value).encode())

    def original_association(self):
        value = self.shell('run-as', DRIVER, 'cat', 'no_backup/owned-sync-association-intent.json', check=False)
        require(value.returncode == 0 and 0 < len(value.stdout) <= 2048, 'RELEASE_SYNC_ORIGINAL_UI_RECEIPT_MISSING')
        original = json.loads(value.stdout)
        require(set(original) == {'operation_id', 'preview_fingerprint', 'remote_vault'}, 'RELEASE_SYNC_ORIGINAL_UI_RECEIPT_SCOPE')
        atomic_json(self.output / 'original-association-intent.json', original)
        return original

    def content_snapshot(self, label, incoming=False):
        paths = owned_paths(incoming)
        observer = StoppedReleaseObserver(self.device, self.args.serial, self.output / 'content-originals' / label,
            list(LOCAL + PEER if incoming else LOCAL), attachment=ASSET if incoming else None)
        # Reuse only the already admitted exact owned boot/root identity; there
        # is no second adb-root request or access to the auth index here.
        observer.identity = self.observer.identity
        require(observer.observe_identity() == self.identity, 'RELEASE_SYNC_CONTENT_OWNER_CHANGED')
        root = observer.discover_data_root(); observer.copy_stopped(root, 'snapshot')
        original = observer.output / 'snapshot'
        projection = project_sync(original, self.tools / ('sync-working-' + label), incoming=incoming)
        atomic_json(self.output / (label + '-content-projection.json'), projection)
        return projection, {path: (original / 'vault' / path).read_bytes() for path in paths}

    def sync_provider(self, label):
        value = observe_provider(self.owner)
        atomic_json(self.output / (label + '-sync-provider.json'), value)
        return value

    def failure_observations(self):
        super().failure_observations()
        try: self.sync_provider('failure')
        except Exception as error:
            atomic_json(self.output / 'failure-sync-provider-unavailable.json', dict(error_type=type(error).__name__, mutation_retried=False))
        if self.identity is not None:
            try:
                observer = StoppedReleaseObserver(self.device, self.args.serial, self.output / 'failure-content-databases', list(LOCAL))
                observer.allowed = {db + suffix for db in DATABASES for suffix in ('', '-wal', '-shm')}
                observer.identity = self.observer.identity
                root = observer.discover_data_root(); observer.copy_stopped(root, 'snapshot')
            except Exception as error:
                atomic_json(self.output / 'failure-content-databases-unavailable.json', dict(error_type=type(error).__name__, mutation_retried=False))

    def run(self):
        self.prepare()  # Fresh normal auth boundary, before any independent setup device exists.
        try:
            self.enable_test_ime()
            for phase in ('markdown', 'canvas'): ReleaseUpgrade.phase(self, phase, 'seed-' + phase)
        finally: self.restore_test_ime()
        seeded, original = self.content_snapshot('seeded')
        require(original[LOCAL[0]] == BODY.encode() and NODE in original[LOCAL[1]].decode(), 'RELEASE_SYNC_ORIGINAL_GUI_SEED')
        saves = [seeded['saves'][path] for path in LOCAL]
        require(all(len(v) == 1 and v[0]['state'] == 'committed' and v[0]['error'] is None for v in saves)
                and len({v[0]['operation_id'] for v in saves}) == 2, 'RELEASE_SYNC_TWO_ORIGINAL_GUI_SAVE_IDS')
        self.phase('login-a', 'sync-login')
        auth = self.snapshot('sync-login'); require(len(auth['intents']) == 1, 'RELEASE_SYNC_ORIGINAL_LOGIN_COUNT')
        login = auth['intents'][0]; active_intent(auth, 'login', login['device_id'])
        setup = Setup(self.owner); prepared = setup.run()
        atomic_json(self.output / 'independent-setup.json', prepared)
        require(not prepared['provider']['rows']['revisions'], 'RELEASE_SYNC_FRESH_REMOTE_REVISIONS')
        self.stage_sync_input(prepared['vault'])
        self.phase('associate', 'association'); frozen = self.original_association()
        bound, bound_bytes = self.content_snapshot('associated')
        validate_association(frozen, bound['native'], prepared, login)
        require(bound_bytes == original and not self.sync_provider('associated')['rows']['revisions'], 'RELEASE_SYNC_ASSOCIATION_TRANSFERRED_CONTENT')
        self.phase('cycle', 'first-cycle')
        uploaded, upload_bytes = self.content_snapshot('uploaded'); provider = self.sync_provider('uploaded')
        latest = validate_upload(provider, uploaded, login['device_id'])
        require(upload_bytes == original, 'RELEASE_SYNC_UPLOAD_CHANGED_LOCAL_BYTES')
        for value in latest:
            body = read_original_object(setup.endpoint, prepared['vault']['vault_id'], value['hash'], setup.session()['access_token'])
            require(body == original[value['path']], 'RELEASE_SYNC_ACTUAL_REMOTE_OBJECT_BYTES')
            exclusive(self.output / ('remote-' + value['path']), body)
        self.phase('association-original', 'association-read'); self.phase('status', 'upload-read')
        readonly, readonly_bytes = self.content_snapshot('after-readonly')
        same_projection(uploaded, readonly)
        require(readonly_bytes == original, 'RELEASE_SYNC_READONLY_CHANGED_ORIGINAL_BYTES')
        peer = ReleasePeer(self.owner); receipts = []
        for label, path, body in (('release-incoming-png', ASSET, IMAGE_BYTES), ('release-incoming-note', PEER[0], PEER_NOTE),
                                  ('release-incoming-canvas', PEER[1], PEER_CANVAS)):
            receipt = peer.publish(label, path, body); receipts.append(receipt)
            atomic_json(self.output / (label + '-receipt.json'), receipt)
        self.phase('cycle-incoming', 'incoming-cycle')
        incoming, incoming_bytes = self.content_snapshot('incoming', True); provider = self.sync_provider('incoming')
        validate_incoming(receipts, provider, uploaded['native'], incoming['native'], login['device_id'])
        require(incoming_bytes == {**original, PEER[0]: PEER_NOTE, PEER[1]: PEER_CANVAS, ASSET: IMAGE_BYTES}, 'RELEASE_SYNC_APPLIED_ORIGINAL_BYTES')
        self.phase('status', 'incoming-cold-read'); cold, cold_bytes = self.content_snapshot('incoming-cold', True)
        same_projection(incoming, cold)
        require(cold_bytes == incoming_bytes, 'RELEASE_SYNC_COLD_ORIGINAL_BYTES_CHANGED')
        final_auth = self.snapshot('sync-final'); require(final_auth == auth, 'RELEASE_SYNC_CHANGED_ORIGINAL_AUTH_SELECTION')
        result = dict(schema=1, passed=True, kind='android-signed-release-foreground-sync', package=APP, abi='x86_64',
            tools_commit=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']), compiled_source=self.manifest['successor']['source_commit'],
            manifest_sha256=digest(self.inputs / 'runtime-inputs.json'), installed_binary=self.identities['successor'], phases=self.phases,
            original_login_id=login['operation_id'], Android_device=login['device_id'], independent_setup_device=prepared['device_id'],
            original_Save_ids=[v[0]['operation_id'] for v in saves], original_association=frozen,
            original_uploaded_operation_ids=[v['operation_id'] for v in uploaded['native']['jobs']], original_peer_receipts=receipts,
            actual_GUI_foreground_upload_and_peer_Native_apply_bytes=True, cold_sync_metadata_and_original_bytes_preserved=True,
            cycle_input_kind='native_accessibility_click', physical_cycle_touch_verified=False,
            original_touch_failure_cause_confirmed=False,
            Native_RAM_invocation_id_observed=False, incoming_editor_render_reopen_verified=False,
            private_auth_DB_or_ciphertext_or_UI_or_credentials_exported=False, only_successor_installed=True,
            device_kind='owned-hosted-emulator', physical_phone_verified=False, actual_desktop_product_pair_verified=False,
            offline_conflict_Stop_worker_recovery_verified=False, whole_Sync_A4_complete=False, published=False)
        atomic_json(self.output / 'sync-runtime-proof.json', result)
        return result


if __name__ == '__main__': raise SystemExit(main(ReleaseSync))
