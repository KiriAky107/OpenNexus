"""Faulting API fixture and real Git blobs; never contacts a live release."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_release import GitHub, Publisher, snapshot
from release_plan import Plan, ReleaseError, atomic_json, build_source, check_local_source, digest, fixed_archive, journal_lock, text_digest


def load_prepare():
    spec = importlib.util.spec_from_file_location('prepare_release', Path(__file__).with_name('prepare-release.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGitHub:
    def __init__(self, plan, original=False):
        self.plan, self.now = plan, datetime.now(timezone.utc)
        self.fault, self.fired = None, False
        self.writes, self.uploads, self.tag_writes = [], 0, 0
        self.ref = {'type': 'tag', 'sha': 'b'*40} if original else None
        self.release = None
        self.assets, self.contents, self.next_id = {}, {}, 2000
        self.latest = None
        if original:
            self.release = {'id': 1000, 'tag_name': plan.data['tag'], 'target_commitish': 'c'*40, 'body': 'original bilingual notes',
                            'name': 'original release', 'draft': False, 'prerelease': False, 'created_at': self.now.isoformat(), 'upload_url': 'https://uploads.github.com/repos/owner/project/releases/1000/assets{?name,label}'}
            self.latest = copy.deepcopy(self.release)
            for asset in plan.data['assets']:
                self.add(asset['name'], b'original-'+asset['name'].encode())
            plan.data['original'] = {'release_id': 1000, 'tag_ref_sha': 'b'*40, 'tag_commit': 'c'*40,
                'body_sha256': text_digest(self.release['body']), 'assets': [{'id': a['id'], 'name': a['name'], 'sha256': a['digest'].removeprefix('sha256:'), 'bytes': a['size']} for a in self.assets.values()]}
            atomic_json(plan.path, plan.data)

    def add(self, name, content, state='uploaded'):
        self.next_id += 1
        value = {'id': self.next_id, 'name': name, 'size': len(content), 'digest': 'sha256:'+hashlib.sha256(content).hexdigest(), 'state': state, 'created_at': self.now.isoformat()}
        self.assets[value['id']] = value
        self.contents[value['id']] = content
        return copy.deepcopy(value)

    def fail(self, point):
        if self.fault == point and not self.fired:
            self.fired = True
            raise ReleaseError('REMOTE_OUTCOME_UNKNOWN_RESUME_SAME_PLAN')

    def request(self, method, path, body=None, file=None):
        url = urlsplit(path)
        route = url.path
        if method == 'GET':
            if '/git/ref/tags/' in route:
                return {'object': copy.deepcopy(self.ref)} if self.ref else None
            if '/git/tags/' in route:
                return {'object': {'type': 'commit', 'sha': 'c'*40}}
            if route.endswith('/releases/latest'):
                return copy.deepcopy(self.latest)
            if '/releases/tags/' in route:
                return copy.deepcopy(self.release)
            if route.endswith('/assets'):
                return copy.deepcopy(list(self.assets.values()))
            raise AssertionError(route)
        self.writes.append((method, route, copy.deepcopy(body)))
        if method == 'POST' and route.endswith('/releases'):
            self.release = dict(id=1000, upload_url='https://uploads.github.com/repos/owner/project/releases/1000/assets{?name,label}', created_at=datetime.now(timezone.utc).isoformat(), **body)
            self.fail('create-after')
            return copy.deepcopy(self.release)
        if method == 'POST' and route.endswith('/assets'):
            self.fail('upload-before')
            self.uploads += 1
            asset = self.add(parse_qs(url.query)['name'][0], file.read_bytes(), state='starter' if self.fault == 'starter-after' and not self.fired else 'uploaded')
            self.fail('upload-after')
            self.fail('starter-after')
            return asset
        if '/releases/assets/' in route:
            ident = int(route.rsplit('/', 1)[1])
            if method == 'DELETE':
                self.assets.pop(ident)
                self.fail('delete-after')
                return None
            self.assets[ident].update(body)
            if body['name'].startswith('original-'):
                self.fail('preserve-after')
            else:
                self.fail('promote-after')
            return copy.deepcopy(self.assets[ident])
        if method == 'PATCH' and '/releases/' in route:
            self.release.update(body)
            self.latest = copy.deepcopy(self.release)
            self.fail('publish-after')
            return copy.deepcopy(self.release)
        raise AssertionError((method, route))

    def tag_move(self, repository, tag, commit, lease):
        require = (self.ref['sha'] if self.ref else None) == lease
        if not require:
            raise ReleaseError('TAG_LEASE_REJECTED_OR_OUTCOME_UNKNOWN')
        self.tag_writes += 1
        self.ref = {'type': 'commit', 'sha': commit}
        self.fail('tag-after')

    def download(self, repository, asset, destination):
        destination.write_bytes(self.contents[asset['id']])
        self.fail('backup-after')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='onx-release-fixture-')
        self.root = Path(self.temp.name)
        self.repo = self.root/'repository'
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.email', 'fixture@example.invalid'), ('config', 'user.name', 'Release fixture')]:
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo/'README.md').write_bytes(b'controlled source\r\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'synthetic source'], cwd=self.repo, check=True, capture_output=True)
        self.commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True).strip()
        self.base = self.root/'artifacts'
        self.base.mkdir()
        source = self.base/'project_0.6.0_source.zip'
        fixed_archive(self.repo, self.commit, source)
        deploy = self.base/'project_0.6.0_deploy.zip'
        deploy.write_bytes(source.read_bytes())
        self.plan_data = {'schema': 1, 'repository': 'owner/project', 'product': 'Project', 'version': '0.6.0', 'tag': 'v0.6.0', 'commit': self.commit, 'original': None}
        self.rewrite([source, deploy])
        self.plan = Plan(self.base/'release-plan.json')

    def tearDown(self):
        self.temp.cleanup()

    def rewrite(self, files):
        assets = [{'name': p.name, 'file': p.name, 'kind': 'source' if '_source.' in p.name else 'deployment', 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files]
        sums = self.base/'SHA256SUMS.txt'
        sums.write_text(''.join(f'{a["sha256"]}  {a["name"]}\n' for a in assets), 'utf-8')
        assets.append({'name': sums.name, 'file': sums.name, 'kind': 'checksums', 'bytes': sums.stat().st_size, 'sha256': digest(sums)})
        note = '# Project 0.6.0\n\n## 更新内容\n\n- '+('真实恢复检查，保护用户原始数据并支持中断后继续。'*3)+'\n\n## English\n\n- '+('Verified source and deployment inputs preserve the original data and permit resuming an interrupted upload after checking the remote identifiers and all hashes. '*4)+'\n\n'
        note += '\n'.join('https://github.com/owner/project/releases/download/v0.6.0/'+a['name'] for a in assets)+'\n'
        (self.base/'RELEASE-NOTES.md').write_text(note, 'utf-8')
        receipt = self.base/'verification.json'
        deployment = next(a for a in assets if a['kind'] == 'deployment')
        receipt.write_text(json.dumps({'source_commit': self.commit, 'source_clean': True, 'version': '0.6.0', 'passed': True,
            'deployment_sha256': deployment['sha256'], 'deployment_probe': {'passed': True, 'cleanup_complete': True, 'module_origin_verified': True}}), 'utf-8')
        self.plan_data.update(assets=assets, notes_file='RELEASE-NOTES.md', notes_sha256=text_digest(note), verification={'file': receipt.name, 'sha256': digest(receipt)})
        atomic_json(self.base/'release-plan.json', self.plan_data)

    def api(self, original=False):
        api = FakeGitHub(self.plan, original)
        self.plan = Plan(self.plan.path)
        return api

    def publisher(self, api):
        return Publisher(Plan(self.plan.path), api, clock=lambda: api.now)

    def test_precheck_has_no_remote_mutations_or_journal_intents(self):
        api = self.api()
        check_local_source(self.plan, self.repo)
        self.assertFalse(self.publisher(api).precheck()['remote_modified'])
        self.assertEqual(api.writes, [])
        self.assertFalse((self.base/'release-journal.json').exists())

    def test_new_release_and_repeated_publish_do_not_repeat_completed_writes(self):
        api = self.api()
        self.publisher(api).stage()
        self.assertTrue(api.release['draft'])
        result = self.publisher(api).publish()
        self.assertTrue(result['formal'] and result['latest'])
        count = len(api.writes)
        self.publisher(api).publish()
        self.assertEqual(len(api.writes), count)
        self.assertEqual((api.uploads, api.tag_writes), (3, 1))

    def test_upload_lost_response_is_queried_and_not_uploaded_twice(self):
        for fault in ['upload-before', 'upload-after', 'create-after']:
            with self.subTest(fault=fault):
                api = self.api()
                api.fault = fault
                with self.assertRaises(ReleaseError):
                    self.publisher(api).stage()
                self.publisher(api).stage()
                self.publisher(api).publish()
                self.assertEqual(api.uploads, 3)
                (self.base/'release-journal.json').unlink()

    def test_owned_incomplete_upload_requires_age_and_preserves_recent_uncertainty(self):
        api = self.api()
        api.fault = 'starter-after'
        with self.assertRaises(ReleaseError):
            self.publisher(api).stage()
        with self.assertRaisesRegex(ReleaseError, 'PENDING_OUTCOME_UNCERTAIN'):
            self.publisher(api).stage()
        self.assertFalse(any(m == 'DELETE' for m, _, _ in api.writes))
        api.now += timedelta(minutes=6)
        self.publisher(api).stage()
        self.publisher(api).publish()
        self.assertEqual(api.uploads, 4)

    def test_lost_tag_promotion_is_not_repeated(self):
        api = self.api()
        self.publisher(api).stage()
        api.fault = 'tag-after'
        with self.assertRaises(ReleaseError):
            self.publisher(api).publish()
        self.publisher(api).publish()
        self.assertEqual(api.tag_writes, 1)

    def test_renames_publish_and_cleanup_resume_using_fixed_original_ids(self):
        for fault in ['preserve-after', 'promote-after', 'publish-after', 'delete-after', 'backup-after']:
            with self.subTest(fault=fault):
                previous_base = self.base
                self.base = self.root/fault
                self.base.mkdir()
                for file in previous_base.iterdir():
                    if file.is_file() and file.name not in {'release-journal.json', 'release-journal.json.lock'}:
                        (self.base/file.name).write_bytes(file.read_bytes())
                self.plan = Plan(self.base/'release-plan.json')
                api = self.api(original=True)
                api.fault = fault
                try:
                    self.publisher(api).stage()
                    self.publisher(api).publish(replace=True)
                except ReleaseError:
                    pass
                self.publisher(api).stage()
                result = self.publisher(api).publish(replace=True)
                self.assertTrue(api.fired, 'The declared fault was never exercised')
                self.assertTrue(result['formal'])
                self.assertEqual(api.uploads, 3)
                self.assertEqual(api.tag_writes, 1)
                backup = next(self.base.glob('original-1000-*'))
                for asset in self.plan.data['original']['assets']:
                    self.assertEqual(digest(backup/asset['name']), asset['sha256'])
                (self.base/'release-journal.json').unlink()
                self.plan_data['original'] = None
                atomic_json(self.plan.path, self.plan_data)
                self.plan = Plan(self.plan.path)
                self.base = previous_base
                self.plan = Plan(self.base/'release-plan.json')

    def test_remote_tag_change_aborts_before_canonical_renames(self):
        api = self.api(original=True)
        self.publisher(api).stage()
        api.ref = {'type': 'commit', 'sha': 'd'*40}
        before = len(api.writes)
        with self.assertRaisesRegex(ReleaseError, 'REMOTE_TAG_CHANGED'):
            self.publisher(api).publish(replace=True)
        self.assertEqual(len(api.writes), before)

    def test_notes_mismatch_never_deletes_originals(self):
        api = self.api(original=True)
        self.publisher(api).stage()
        api.release['body'] = 'changed by another actor'
        before = len(api.writes)
        with self.assertRaisesRegex(ReleaseError, 'ORIGINAL_NOTES_CHANGED'):
            self.publisher(api).publish(replace=True)
        self.assertEqual(len(api.writes), before)

    def test_unowned_or_corrupt_pending_asset_is_preserved(self):
        api = self.api()
        publisher = self.publisher(api)
        publisher.stage()
        candidate = next(iter(api.assets.values()))
        candidate['digest'] = 'sha256:'+'0'*64
        with self.assertRaisesRegex(ReleaseError, 'PENDING_OUTCOME_UNCERTAIN_OR_WRONG_BYTES'):
            self.publisher(api).stage()
        self.assertFalse(any(method == 'DELETE' for method, _, _ in api.writes))

    def test_foreign_asset_blocks_publication_and_is_never_deleted(self):
        api = self.api()
        self.publisher(api).stage()
        api.add('foreign.bin', b'preserve')
        before = len(api.writes)
        with self.assertRaisesRegex(ReleaseError, 'UNEXPECTED_REMOTE_ASSET'):
            self.publisher(api).publish()
        self.assertEqual(len(api.writes), before)
        self.assertEqual(api.tag_writes, 0)

    def test_replacement_requires_explicit_mode_and_complete_original_backup(self):
        api = self.api(original=True)
        self.publisher(api).stage()
        with self.assertRaisesRegex(ReleaseError, 'EXPLICIT_REPLACEMENT_REQUIRED'):
            self.publisher(api).publish()
        backup = next(self.base.glob('original-1000-*'))
        target = backup/self.plan.data['original']['assets'][0]['name']
        target.write_bytes(b'changed backup')
        with self.assertRaisesRegex(ReleaseError, 'ORIGINAL_BACKUP_CHANGED'):
            self.publisher(api).publish(replace=True)
        self.assertEqual(api.tag_writes, 0)

    def test_snapshot_is_read_only_and_refuses_overwriting_an_existing_file(self):
        api = self.api(original=True)
        output = self.root/'original.json'
        result = snapshot(api, 'owner/project', 'v0.6.0', output)
        self.assertFalse(result['remote_modified'])
        self.assertEqual(json.loads(output.read_text())['tag_ref_sha'], 'b'*40)
        with self.assertRaisesRegex(ReleaseError, 'SNAPSHOT_ALREADY_EXISTS'):
            snapshot(api, 'owner/project', 'v0.6.0', output)
        self.assertEqual(api.writes, [])

    def test_source_comment_alone_does_not_approve_wrong_git_blob_bytes(self):
        source = self.base/'project_0.6.0_source.zip'
        with zipfile.ZipFile(source) as archive:
            entry = archive.getinfo('README.md')
        with zipfile.ZipFile(source, 'w') as archive:
            archive.comment = self.commit.encode()
            archive.writestr(entry, b'altered source\r\n')
        self.rewrite([source, self.base/'project_0.6.0_deploy.zip'])
        with self.assertRaisesRegex(ReleaseError, 'SOURCE_ARCHIVE_BLOB_MISMATCH'):
            check_local_source(Plan(self.plan.path), self.repo)

    def test_deployment_archive_rejects_private_and_unsafe_empty_directories(self):
        deployment = self.base/'project_0.6.0_deploy.zip'
        source = self.base/'project_0.6.0_source.zip'
        for name in ('frontend/docs/', 'documents/', '.local-plans/', '../outside/', 'C:/outside/'):
            with self.subTest(name=name):
                deployment.write_bytes(source.read_bytes())
                with zipfile.ZipFile(deployment, 'a') as archive:
                    archive.writestr(name, b'')
                self.rewrite([source, deployment])
                with self.assertRaisesRegex(ReleaseError, 'PRIVATE_OR_UNSAFE_SOURCE_PATH'):
                    check_local_source(Plan(self.plan.path), self.repo)

    def test_archive_cannot_turn_a_fixed_git_blob_into_a_symlink(self):
        source = self.base/'project_0.6.0_source.zip'
        with zipfile.ZipFile(source) as archive:
            entry, content = archive.getinfo('README.md'), archive.read('README.md')
        entry.external_attr = 0o120777 << 16
        with zipfile.ZipFile(source, 'w') as archive:
            archive.comment = self.commit.encode()
            archive.writestr(entry, content)
        self.rewrite([source, self.base/'project_0.6.0_deploy.zip'])
        with self.assertRaisesRegex(ReleaseError, 'SOURCE_ARCHIVE_MODE_MISMATCH'):
            check_local_source(Plan(self.plan.path), self.repo)

    def test_private_source_paths_and_altered_receipts_are_refused(self):
        source = self.base/'project_0.6.0_source.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.comment = self.commit.encode()
            archive.writestr('frontend/docs/private.md', 'local only')
        self.rewrite([source, self.base/'project_0.6.0_deploy.zip'])
        with self.assertRaisesRegex(ReleaseError, 'PRIVATE_OR_UNSAFE_SOURCE_PATH'):
            Plan(self.plan.path)

    def test_changed_verification_receipt_is_not_trusted(self):
        (self.base/'verification.json').write_text('{"passed":true}', 'utf-8')
        with self.assertRaisesRegex(ReleaseError, 'VERIFICATION_CHANGED'):
            Plan(self.plan.path)

    def test_service_receipt_must_bind_the_actual_deployment_archive(self):
        receipt = self.base/'verification.json'
        data = json.loads(receipt.read_text('utf-8'))
        data['deployment_sha256'] = '0'*64
        receipt.write_text(json.dumps(data), 'utf-8')
        self.plan_data['verification']['sha256'] = digest(receipt)
        atomic_json(self.plan.path, self.plan_data)
        with self.assertRaisesRegex(ReleaseError, 'DEPLOYMENT_VERIFICATION_MISMATCH'):
            Plan(self.plan.path)

    def test_service_receipt_cannot_approve_an_unfinished_packaged_probe(self):
        receipt = self.base/'verification.json'
        data = json.loads(receipt.read_text('utf-8'))
        data['deployment_probe']['cleanup_complete'] = False
        receipt.write_text(json.dumps(data), 'utf-8')
        self.plan_data['verification']['sha256'] = digest(receipt)
        atomic_json(self.plan.path, self.plan_data)
        with self.assertRaisesRegex(ReleaseError, 'PACKAGED_SERVICE_PROBE_REQUIRED'):
            Plan(self.plan.path)

    def test_new_owned_draft_notes_change_is_rejected_before_tag_or_asset_mutations(self):
        api = self.api()
        self.publisher(api).stage()
        api.release['body'] = 'another actor changed this draft'
        before = len(api.writes)
        with self.assertRaisesRegex(ReleaseError, 'OWNED_RELEASE_NOTES_CHANGED'):
            self.publisher(api).publish()
        self.assertEqual(len(api.writes), before)
        self.assertEqual(api.tag_writes, 0)

    def test_unknown_original_asset_id_never_receives_a_destructive_request(self):
        api = self.api(original=True)
        self.publisher(api).stage()
        original = next(iter(api.assets.values()))
        value = api.assets.pop(original['id'])
        value['id'] = 99999
        api.assets[value['id']] = value
        before = len(api.writes)
        with self.assertRaisesRegex(ReleaseError, 'ORIGINAL_ASSET_DISAPPEARED'):
            self.publisher(api).publish(replace=True)
        self.assertEqual(len(api.writes), before)

    def test_kernel_lock_prevents_two_publishers_and_releases_after_close(self):
        path = self.base/'release-journal.json'
        with journal_lock(path):
            with self.assertRaisesRegex(ReleaseError, 'JOURNAL_BUSY'):
                with journal_lock(path):
                    self.fail('Second publisher obtained a live lock')
        with journal_lock(path):
            pass

    def test_actual_process_death_preserves_intent_and_releases_kernel_lock(self):
        code = "import sys; sys.path.insert(0,sys.argv[1]); from release_plan import Plan,Journal,journal_lock; p=Plan(sys.argv[2]); lock=journal_lock(p.base/'release-journal.json'); lock.__enter__(); Journal(p).record('owned-test-intent'); print('READY',flush=True); sys.stdin.read()"
        child = subprocess.Popen([sys.executable, '-c', code, str(Path(__file__).parent), str(self.plan.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            self.assertEqual(child.stdout.readline().strip(), b'READY')
            with self.assertRaisesRegex(ReleaseError, 'JOURNAL_BUSY'):
                with journal_lock(self.base/'release-journal.json'):
                    pass
            child.kill()
            child.wait(timeout=10)
            with journal_lock(self.base/'release-journal.json'):
                data = json.loads((self.base/'release-journal.json').read_text('utf-8'))
                self.assertEqual(data['events'][-1]['action'], 'owned-test-intent')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            for handle in [child.stdin, child.stdout, child.stderr]:
                handle.close()

    def test_preparation_preserves_existing_output_and_emits_fixed_source_and_deploy(self):
        prepare = load_prepare().prepare
        output = self.root/'prepared'
        result = prepare(self.repo, output, repository='owner/project', product='Project', version='0.6.0', commit=self.commit,
                         notes=self.base/'RELEASE-NOTES.md', verification=self.base/'verification.json', deployment=self.base/'project_0.6.0_deploy.zip')
        self.assertFalse(result['remote_modified'])
        self.assertEqual(len(result['assets']), 3)
        original = (output/'release-plan.json').read_bytes()
        with self.assertRaises(FileExistsError):
            prepare(self.repo, output, repository='owner/project', product='Project', version='0.6.0', commit=self.commit,
                    notes=self.base/'RELEASE-NOTES.md', verification=self.base/'verification.json', deployment=self.base/'project_0.6.0_deploy.zip')
        self.assertEqual((output/'release-plan.json').read_bytes(), original)

    def test_precompile_source_receipt_detects_tracked_input_changes(self):
        before = build_source(self.repo, self.commit)
        self.assertTrue(before['source_clean'])
        self.assertEqual(before['files_sha256']['README.md'], digest(self.repo/'README.md'))
        (self.repo/'README.md').write_bytes(b'changed during compilation')
        with self.assertRaisesRegex(ReleaseError, 'FIXED_CLEAN_BUILD_SOURCE_REQUIRED'):
            build_source(self.repo, self.commit)

    def test_ignored_signing_overlay_is_bound_to_the_actual_build_receipt(self):
        (self.repo/'.gitignore').write_text('frontend/src-tauri/tauri.rc.conf.json\n', 'utf-8')
        subprocess.run(['git', 'add', '.gitignore'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'synthetic signing overlay exclusion'], cwd=self.repo, check=True, capture_output=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True).strip()
        overlay = self.repo/'frontend/src-tauri/tauri.rc.conf.json'
        overlay.parent.mkdir(parents=True)
        overlay.write_text('{"controlled":"first"}', 'utf-8')
        first = build_source(self.repo, commit)
        overlay.write_text('{"controlled":"changed"}', 'utf-8')
        second = build_source(self.repo, commit)
        self.assertTrue(first['source_clean'] and second['source_clean'])
        self.assertNotEqual(first['overlays_sha256'], second['overlays_sha256'])

    def test_tag_lease_keeps_auth_out_of_arguments_and_inherited_trace_files(self):
        calls = []
        def fake_git(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0)
        traces = {'GIT_TRACE2_EVENT': str(self.root/'trace.json'), 'GIT_TRACE_CURL': str(self.root/'curl.log'),
                  'GIT_TRACE2_PERF': str(self.root/'performance.log'), 'GIT_CURL_VERBOSE': '1'}
        with patch.dict(os.environ, traces), patch('github_release.subprocess.run', side_effect=fake_git):
            GitHub('synthetic-not-a-real-token', self.repo).tag_move('owner/project', 'v0.6.0', self.commit, None)
        args, options = calls[-1]
        self.assertIn('--force-with-lease=refs/tags/v0.6.0:', args)
        self.assertNotIn('synthetic-not-a-real-token', ' '.join(args))
        for name in traces:
            if name == 'GIT_CURL_VERBOSE':
                self.assertEqual(options['env'][name], '0')
            else:
                self.assertNotIn(name, options['env'])
        self.assertEqual(options['env']['GIT_TRACE_REDACT'], '1')
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
