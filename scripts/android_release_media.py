"""Production picker and SAF export on the unchanged intended-signed successor.

Only an exclusive hosted API36 AVD is admitted. No predecessor update, debug
product path, credential store, direct product IPC or signing operation is used.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time
import zipfile
from types import SimpleNamespace

from android_release_observation import APP, MAX_FILE, StoppedReleaseObserver, exclusive
from android_release_upgrade import ReleaseUpgrade
from android_release_snapshot import inspect_stopped_snapshot, same_originals
from android_runtime_probe import DRIVER, IMAGE_BODY, IMAGE_BYTES, Probe, require, require_hosted_emulator
from android_signed_runtime_binding import bind_pair
from android_signed_runtime_inputs import strict_json
from release_plan import atomic_json, digest

FILES = ['release-owned-image.md', 'release-owned-image.canvas']
IMAGE_HASH = hashlib.sha256(IMAGE_BYTES).hexdigest()
ASSET = f'attachments/{IMAGE_HASH[:2]}/{IMAGE_HASH}.png'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
PHASES = ('release-image-markdown', 'release-image-canvas', 'release-image-markdown-reopen',
          'release-image-canvas-reopen', 'release-content-export')


def original_media(projection, originals):
    require(set(projection['owned_files']) == set(FILES + [ASSET]) and set(originals) == set(FILES + [ASSET]),
            'RELEASE_MEDIA_EXACT_OWN_FILES')
    require(originals[ASSET] == IMAGE_BYTES, 'RELEASE_MEDIA_ORIGINAL_IMAGE_BYTES')
    reference = ('![](' + ASSET + ')').encode()
    require(originals[FILES[0]].count(reference) == 1
            and originals[FILES[0]].replace(reference, b'') == IMAGE_BODY.encode(), 'RELEASE_MEDIA_MARKDOWN_BYTES')
    canvas = strict_json(originals[FILES[1]])
    require(type(canvas.get('nodes')) is list and len(canvas['nodes']) == 1
            and canvas['nodes'][0].get('type') == 'file' and canvas['nodes'][0].get('file') == ASSET,
            'RELEASE_MEDIA_CANVAS_ORIGINAL_REFERENCE')
    saves, images = [], []
    for name in FILES:
        item = projection['owned_files'][name]
        require(len(item['drafts']) == len(item['saves']) == len(item['images']) == 1, 'RELEASE_MEDIA_SINGLE_ORIGINAL_ACTIONS')
        draft, save, image = item['drafts'][0], item['saves'][0], item['images'][0]
        require(draft['content'].encode() == originals[name] and draft['generation'] == draft['saved_generation']
                and save['state'] == 'committed' and not save['error'] and save['generation'] == draft['generation']
                and save['draft_id'] == draft['id'] and save['content'] == draft['content'], 'RELEASE_MEDIA_COMMITTED_GUI_SAVE')
        require(image['draft_id'] == draft['id'] and image['asset_path'] == ASSET and image['hash'] == IMAGE_HASH
                and image['size'] == len(IMAGE_BYTES) and image['state'] == 'committed'
                and not image['error'] and image['content_after'] == draft['content'], 'RELEASE_MEDIA_NATIVE_IMAGE_RECEIPT')
        saves.append(save['operation_id']); images.append(image['operation_id'])
    require(len(set(saves + images)) == 4, 'RELEASE_MEDIA_DISTINCT_ORIGINAL_IDS')
    return dict(original_GUI_save_ids=saves, original_image_operation_ids=images)


def validate_export(delivery, sealed, content, originals):
    require(type(delivery) is dict and type(sealed) is dict
            and type(delivery.get('ticket')) is str and re.fullmatch(UUID, delivery['ticket'])
            and type(delivery.get('delivery_id')) is str and re.fullmatch(UUID, delivery['delivery_id'])
            and delivery.get('state') == 'saved' and type(delivery.get('verified_at')) is int
            and delivery['verified_at'] > 0 and not delivery.get('error'), 'RELEASE_MEDIA_SAVED_DELIVERY')
    require(type(content) is bytes and len(content) <= MAX_FILE, 'RELEASE_MEDIA_EXPORT_BOUND')
    sha = hashlib.sha256(content).hexdigest()
    require(delivery.get('sha256') == sealed.get('sha256') == sha
            and delivery.get('size') == sealed.get('size') == len(content)
            and delivery['ticket'] == sealed.get('ticket')
            and sealed.get('filename') == 'OpenNexus-content-' + delivery['ticket'] + '.zip'
            and sealed.get('file_count') == len(originals)
            and sealed.get('content_bytes') == sum(map(len, originals.values())), 'RELEASE_MEDIA_FROZEN_EXPORT')
    expected = {'vault/' + name: raw for name, raw in originals.items()}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        require(len(entries) == len(expected) + 1 and len({e.filename for e in entries}) == len(entries)
                and {e.filename for e in entries} == set(expected) | {'OpenNexus-export.json'}
                and all(not e.is_dir() and e.file_size <= MAX_FILE for e in entries), 'RELEASE_MEDIA_EXPORT_CLOSED_SCOPE')
        for name, raw in expected.items():
            require(archive.read(name) == raw, 'RELEASE_MEDIA_EXPORTED_ORIGINAL_CHANGED')
        manifest = strict_json(archive.read('OpenNexus-export.json'))
        rows = [dict(path=name, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for name, raw in sorted(expected.items())]
        require(manifest == dict(format='opennexus-content-export', schema=1, scope='markdown-canvas-images', files=rows),
                'RELEASE_MEDIA_EXPORT_MANIFEST')
    return {key: delivery[key] for key in ('ticket', 'delivery_id', 'state', 'sha256', 'size', 'verified_at')}


class ReleaseMedia(ReleaseUpgrade):
    def __init__(self, inputs, sdk, java, output, serial='emulator-5562'):
        require_hosted_emulator(serial)
        self.inputs, self.sdk, self.java, self.output = Path(inputs), Path(sdk), Path(java), Path(output)
        require(not self.output.exists(), 'RELEASE_MEDIA_ORIGINAL_OUTPUT_EXISTS')
        self.output.mkdir(parents=True)
        self.tools = self.output.parent / 'release-media-driver-build'; self.tools.mkdir()
        self.args = SimpleNamespace(serial=serial, sdk=self.sdk, scope='release-media')
        self.adb = self.sdk / 'platform-tools/adb'
        self.original_ime = self.original_hardware_ime = None
        self.phases, self.identities = [], {}
        self.binding = bind_pair(self.inputs, self.sdk, self.java, self.output / 'binary-binding')
        self.manifest = strict_json((self.inputs / 'runtime-inputs.json').read_bytes())
        self.observer = StoppedReleaseObserver(self.device, serial, self.output / 'stopped-originals', FILES, attachment=ASSET)
        self.identity = self.root = None
        self.extra_reads = 0
        atomic_json(self.output / 'media-scope-intent.json', dict(files=FILES, attachment=ASSET,
            export_namespace='exports/<UUID>.<UUID>.delivery.json, exports/<UUID>.json/zip',
            install_only='successor', credential_store_or_permissions_touched=False))

    def phase(self, phase, label):
        require(phase in PHASES and re.fullmatch(r'[a-z][a-z0-9-]{0,47}', label), 'RELEASE_MEDIA_PHASE')
        self.stop()
        launch = self.original_command(label + '-launch', ('shell', '-T', shlex.join(('am', 'start', '-W', '-n', self.activity))))
        require(b'Error:' not in launch.stdout, 'RELEASE_MEDIA_LAUNCH_FAILED')
        pid = self.shell('pidof', APP).stdout.decode('ascii').strip()
        require(re.fullmatch(r'[1-9][0-9]{0,9}', pid), 'RELEASE_MEDIA_PROCESS_MISSING')
        result = self.original_command(label + '-instrument', ('shell', '-T', shlex.join(
            ('am', 'instrument', '-w', '-e', 'phase', phase, DRIVER + '/.ReleaseRuntimeProbe'))), timeout=180)
        self.capture(label)
        require(('OPENNEXUS_RUNTIME_PHASE_OK ' + phase).encode() in result.stdout
                and b'OPENNEXUS_RUNTIME_FAILED' not in result.stdout and b'INSTRUMENTATION_FAILED' not in result.stdout,
                'RELEASE_MEDIA_GUI_PHASE_FAILED ' + label)
        self.phases.append(dict(phase=phase, label=label, pid=int(pid), passed=True))
        self.stop()

    def snapshot(self, label):
        if self.root is None: self.root = self.observer.discover_data_root()
        self.observer.copy_stopped(self.root, label)
        projection = inspect_stopped_snapshot(self.output / 'stopped-originals' / label,
            self.output / ('working-' + label), FILES, attachment=ASSET)
        atomic_json(self.output / (label + '-projection.json'), projection)
        return projection

    def extra_original(self, relative, bound=4096):
        require(type(relative) is str and re.fullmatch('exports/' + UUID + r'(?:\.' + UUID + r'\.delivery\.json|\.json|\.zip)', relative)
                and bound in (4096, MAX_FILE), 'RELEASE_MEDIA_EXACT_EXPORT_PATH')
        self.observer.require_stopped()
        self.extra_reads += 1
        label = 'export-read-' + str(self.extra_reads).zfill(2)
        exclusive(self.output / (label + '-intent.json'), json.dumps(dict(relative=relative, bound=bound, read_only=True)).encode())
        self.observer.allowed.add(relative)
        try: path = self.observer.path(self.root, relative)
        finally: self.observer.allowed.remove(relative)
        require(self.observer.shell('test', '-f', path, check=False).returncode == 0, 'RELEASE_MEDIA_EXPORT_NOT_REGULAR')
        size = self.observer.text('stat', '-c', '%s', path)
        require(re.fullmatch(r'[0-9]{1,9}', size) and int(size) <= bound, 'RELEASE_MEDIA_EXPORT_BOUND')
        before = self.observer.text('sha256sum', path).split()
        require(len(before) == 2 and before[1] == path, 'RELEASE_MEDIA_EXPORT_HASH')
        raw = self.observer.shell('cat', path).stdout
        exclusive(self.output / (label + '.original'), raw)
        require(len(raw) == int(size) and hashlib.sha256(raw).hexdigest() == before[0]
                and self.observer.text('sha256sum', path).split() == before, 'RELEASE_MEDIA_EXPORT_CHANGED')
        self.observer.require_stopped()
        return raw

    def exported(self, originals):
        self.observer.require_stopped()
        result = self.observer.shell('find', self.root + 'exports', '-maxdepth', '1', '-name', '*.delivery.json', '-type', 'f')
        exclusive(self.output / 'export-delivery-discovery.stdout', result.stdout)
        require(len(result.stdout) <= 8192, 'RELEASE_MEDIA_EXPORT_DISCOVERY_BOUND')
        paths = result.stdout.decode('utf-8', 'strict').splitlines()
        require(len(paths) == 1 and paths[0].startswith(self.root), 'RELEASE_MEDIA_SINGLE_DELIVERY')
        relative = paths[0][len(self.root):]
        record = strict_json(self.extra_original(relative))
        ticket, delivery_id = record.get('ticket'), record.get('delivery_id')
        require(type(ticket) is str and re.fullmatch(UUID, ticket) and type(delivery_id) is str
                and re.fullmatch(UUID, delivery_id) and relative == f'exports/{ticket}.{delivery_id}.delivery.json',
                'RELEASE_MEDIA_DELIVERY_ID_BINDING')
        sealed = strict_json(self.extra_original('exports/' + ticket + '.json'))
        content = self.extra_original('exports/' + ticket + '.zip', MAX_FILE)
        proof = validate_export(record, sealed, content, originals)
        filename = 'OpenNexus-content-' + ticket + '.zip'
        result = self.shell('find', '/sdcard/Download', '/sdcard/Documents', '-maxdepth', '2', '-name', filename, '-type', 'f', check=False)
        exclusive(self.output / 'system-export-discovery.stdout', result.stdout)
        exclusive(self.output / 'system-export-discovery.stderr', result.stderr)
        paths = result.stdout.decode('utf-8', 'strict').splitlines()
        require(len(result.stdout) <= 8192 and len(paths) == 1 and paths[0].startswith(('/sdcard/Download/', '/sdcard/Documents/'))
                and PurePosixPath(paths[0]).name == filename and '..' not in PurePosixPath(paths[0]).parts,
                'RELEASE_MEDIA_EXACT_SAF_DESTINATION')
        exclusive(self.output / 'system-export-read-intent.json', json.dumps(dict(path=paths[0], read_only=True)).encode())
        size = self.shell('stat', '-c', '%s', paths[0]).stdout.decode().strip()
        require(size == str(len(content)), 'RELEASE_MEDIA_SAF_SIZE')
        actual = self.device('exec-out', 'cat', paths[0]).stdout
        exclusive(self.output / 'system-saved-content.zip', actual)
        require(actual == content, 'RELEASE_MEDIA_SAF_ORIGINAL_BYTES')
        return proof

    def run(self):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if self.device('get-state', check=False, timeout=10).stdout.strip() == b'device' and self.shell('getprop', 'sys.boot_completed', check=False).stdout.strip() == b'1': break
            time.sleep(2)
        else: raise RuntimeError('RELEASE_MEDIA_OWN_AVD_BOOT_TIMEOUT')
        self.identity = self.observer.observe_identity()
        require(self.shell('pm', 'list', 'packages', APP).stdout.decode().splitlines() == [], 'RELEASE_MEDIA_FRESH_PROFILE_REQUIRED_NO_CLEAR')
        for setting in ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale'):
            self.shell('settings', 'put', 'global', setting, '0')
        self.shell('input', 'keyevent', 'KEYCODE_WAKEUP'); self.shell('wm', 'dismiss-keyguard')
        self.original_command('install-successor', ('install', '--no-streaming', self.inputs / 'successor.apk'))
        self.installed('successor')
        activities = self.shell('cmd', 'package', 'resolve-activity', '--brief', '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', APP).stdout.decode().strip().splitlines()
        require(activities and re.fullmatch(re.escape(APP) + r'/[A-Za-z0-9_.$]+', activities[-1]), 'RELEASE_MEDIA_OWN_ACTIVITY')
        self.activity = activities[-1]
        exclusive(self.output / 'picker-stage-intent.json', json.dumps(dict(path='/sdcard/Download/opennexus-ci-original.png', sha256=IMAGE_HASH, bytes=len(IMAGE_BYTES), original_only=True)).encode())
        Probe.stage_original_image(self)
        self.build_driver(); self.enable_test_ime()
        try:
            self.phase(PHASES[0], 'media-markdown'); self.phase(PHASES[1], 'media-canvas')
            self.observer.start_root_observation()
            before = self.snapshot('after-media-save')
            originals = {name: (self.output / 'stopped-originals/after-media-save/vault' / name).read_bytes() for name in FILES + [ASSET]}
            ids = original_media(before, originals)
            self.phase(PHASES[2], 'media-markdown-cold'); self.phase(PHASES[3], 'media-canvas-cold')
            cold = self.snapshot('after-media-cold-read'); same_originals(before, cold)
            require(self.phases[0]['pid'] != self.phases[2]['pid'] and self.phases[1]['pid'] != self.phases[3]['pid'], 'RELEASE_MEDIA_NEW_COLD_PROCESSES')
            self.phase(PHASES[4], 'media-system-export')
            export = self.exported(originals)
            after = self.snapshot('after-media-export'); same_originals(before, after)
            result = dict(schema=1, passed=True, kind='android-signed-release-media', package=APP, abi='x86_64',
                tools_commit=os.environ['GITHUB_SHA'], run_id=int(os.environ['GITHUB_RUN_ID']),
                manifest_sha256=digest(self.inputs / 'runtime-inputs.json'), installed_binary=self.identities['successor'],
                phases=self.phases, original_actions=ids, image_sha256=IMAGE_HASH, system_export=export,
                original_files_and_Native_receipts_preserved=True, production_system_picker_and_SAF_verified=True,
                predecessor_installed_or_upgrade_repeated=False, debug_product_or_private_credential_probe_used=False,
                device_kind='owned-hosted-emulator', physical_phone_verified=False, HTTPS_credentials_or_worker_verified=False, published=False)
            atomic_json(self.output / 'media-proof.json', result)
            return result
        finally:
            self.restore_test_ime(); self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'sdk', 'java', 'output'): parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    probe = ReleaseMedia(args.inputs, args.sdk, args.java, args.output)
    try: result = probe.run()
    except Exception as error:
        atomic_json(args.output / 'original-failure.json', dict(passed=False, error_type=type(error).__name__, message=str(error)[:2000]))
        probe.capture('original-failure')
        raise
    print(json.dumps({k: result[k] for k in ('kind', 'passed', 'abi', 'physical_phone_verified', 'published')}))


if __name__ == '__main__': main()
