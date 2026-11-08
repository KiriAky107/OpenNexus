"""RAM-only checks for original APK build/PR merge source association."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from android_runtime_probe import build_job_origin, validate_build_job


class RuntimeBuildOrigin(unittest.TestCase):
    def fixture(self):
        head, base, merge = 'a' * 40, 'b' * 40, 'c' * 40
        run = {'id': 42, 'path': '.github/workflows/android.yml', 'event': 'pull_request',
            'head_sha': head, 'head_branch': 'feat/canvas-handwriting', 'run_attempt': 1,
            'repository': {'id': 7}, 'pull_requests': [{'head': {'sha': head,
                'ref': 'feat/canvas-handwriting', 'repo': {'id': 7}},
                'base': {'sha': base, 'ref': 'main', 'repo': {'id': 7}}}]}
        job = {'id': 99, 'run_id': 42, 'run_attempt': 1, 'head_sha': head,
            'name': 'debug-apk', 'status': 'completed', 'conclusion': 'success'}
        compiled = {'sha': merge, 'parents': [{'sha': base}, {'sha': head}]}
        return run, job, merge, compiled

    def test_merge_keeps_reported_head_and_compiled_source_distinct(self):
        run, job, merge, compiled = self.fixture()
        result = validate_build_job(run, [job], merge, compiled)
        self.assertEqual(result['head_sha'], run['head_sha'])
        self.assertEqual(result['source_commit'], merge)
        self.assertEqual(result['source_kind'], 'pull_request_merge')
        self.assertEqual(result['merge_base_verification'], 'reported_pr_base')
        self.assertEqual(result['merge_parents'], ['b' * 40, 'a' * 40])
        self.assertEqual(result['job_id'], 99)

    def test_merge_requires_immutable_ordered_parents_and_original_same_repo_pr(self):
        run, job, merge, compiled = self.fixture()
        invalid = []
        for key, value in (('event', 'push'), ('pull_requests', []),
                ('pull_requests', run['pull_requests'] * 2), ('head_sha', 'd' * 40),
                ('head_branch', 'feat/other'), ('path', '.github/workflows/other.yml')):
            invalid.append(({**run, key: value}, compiled))
        for branch, key, value in (('head', 'sha', 'd' * 40),
                ('base', 'sha', 'a' * 40), ('head', 'ref', 'feat/other'),
                ('head', 'repo', {'id': 8}), ('base', 'repo', {'id': 8})):
            other = copy.deepcopy(run)
            other['pull_requests'][0][branch][key] = value
            invalid.append((other, compiled))
        for value in (None, {}, {**compiled, 'sha': 'd' * 40},
                {**compiled, 'parents': list(reversed(compiled['parents']))},
                {**compiled, 'parents': compiled['parents'][:1]},
                {**compiled, 'parents': [{'sha': 'd' * 40}, {'sha': 'a' * 40}]}):
            invalid.append((run, value))
        for other, metadata in invalid:
            with self.subTest(run=other, metadata=metadata), self.assertRaisesRegex(RuntimeError, 'source mismatch'):
                validate_build_job(other, [job], merge, metadata)

    def test_successful_build_cannot_come_from_another_run_head_or_attempt(self):
        run, job, merge, compiled = self.fixture()
        for key, value in (('run_id', 43), ('run_id', True), ('head_sha', 'd' * 40),
                ('run_attempt', 2), ('run_attempt', True), ('conclusion', 'failure'),
                ('status', 'in_progress')):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(RuntimeError, 'no successful terminal'):
                validate_build_job(run, [{**job, key: value}], merge, compiled)

    def fetch(self, run, job, commit, compiled=None, main_ref=None):
        payloads = [run, {'jobs': [job]}]
        if compiled is not None:
            payloads.append(compiled)
        if main_ref is not None:
            payloads.append(main_ref)
        connection = Mock()
        connection.getresponse.side_effect = [Mock(status=200,
            read=Mock(return_value=json.dumps(value).encode())) for value in payloads]
        with patch.dict('android_runtime_probe.os.environ', {
                'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_TOKEN': 'controlled-placeholder'}), \
                patch('android_runtime_probe.http.client.HTTPSConnection', return_value=connection):
            result = build_job_origin('42', commit)
        return result, connection

    def test_origin_fetches_only_the_selected_immutable_merge_after_run_and_jobs(self):
        run, job, merge, compiled = self.fixture()
        result, connection = self.fetch(run, job, merge, compiled)
        self.assertEqual(result['source_commit'], merge)
        self.assertEqual([call.args[:2] for call in connection.request.call_args_list], [
            ('GET', '/repos/owner/repo/actions/runs/42'),
            ('GET', '/repos/owner/repo/actions/runs/42/jobs?per_page=100'),
            ('GET', '/repos/owner/repo/git/commits/' + merge)])
        self.assertEqual(connection.close.call_count, 3)

    def test_advanced_main_requires_the_exact_fresh_canonical_commit_ref(self):
        run, job, merge, compiled = self.fixture()
        compiled['parents'][0]['sha'] = 'd' * 40
        current = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'd' * 40}}
        result = validate_build_job(run, [job], merge, compiled, current)
        self.assertEqual(result['merge_base_verification'], 'observed_main_ref')
        self.assertEqual(result['merge_parents'], ['d' * 40, 'a' * 40])
        for invalid in (None, {}, {**current, 'ref': 'refs/heads/other'},
                {**current, 'object': {'type': 'tag', 'sha': 'd' * 40}},
                {**current, 'object': {'type': 'commit', 'sha': 'e' * 40}}):
            with self.subTest(ref=invalid), self.assertRaisesRegex(RuntimeError, 'source mismatch'):
                validate_build_job(run, [job], merge, compiled, invalid)
        other = copy.deepcopy(run)
        other['pull_requests'][0]['base']['ref'] = 'release/other'
        with self.assertRaisesRegex(RuntimeError, 'source mismatch'):
            validate_build_job(other, [job], merge, compiled, current)

    def test_advanced_main_lookup_is_explicit_once_and_keeps_original_base_distinct(self):
        run, job, merge, compiled = self.fixture()
        compiled['parents'][0]['sha'] = 'd' * 40
        current = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'd' * 40}}
        result, connection = self.fetch(run, job, merge, compiled, current)
        self.assertEqual(result['merge_base_verification'], 'observed_main_ref')
        self.assertEqual(run['pull_requests'][0]['base']['sha'], 'b' * 40)
        self.assertEqual(connection.request.call_args_list[-1].args[:2],
            ('GET', '/repos/owner/repo/git/ref/heads/main'))
        self.assertEqual(connection.request.call_count, 4)
        self.assertEqual(connection.close.call_count, 4)

    def test_direct_head_needs_no_merge_metadata_and_malformed_selection_makes_no_request(self):
        run, job, _, _ = self.fixture()
        result, connection = self.fetch({**run, 'event': 'workflow_dispatch'}, job, run['head_sha'])
        self.assertEqual(result['source_kind'], 'head')
        self.assertIsNone(result['merge_parents'])
        self.assertEqual(connection.request.call_count, 2)
        with patch('android_runtime_probe.http.client.HTTPSConnection') as request:
            for run_id, commit in (('42/other', 'a' * 40), ('42', '../other')):
                with self.assertRaises(RuntimeError):
                    build_job_origin(run_id, commit)
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
