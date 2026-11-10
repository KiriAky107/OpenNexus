"""RAM fresh owner/history ordering; no actual service, GUI, DB or Native run."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import android_auth_hosted as hosted
import android_auth_fixture as auth
import android_release_sync_quota_fixture as quota
from test_android_quota32_prelogin_prepare import PreloginPrepare


class Private:
    def __str__(self): raise AssertionError('private coercion')


def proof(state, run='12345-1'):
    return dict(schema=1, source=quota.validation.SYNC, fixture=quota.FIXTURE,
        owner_run=run, owner=state['owner'], fixture_script_sha256=state['fixture_script_sha256'],
        factory_default_quota=32, stage='prepare-before-Android-login-or-setup',
        accounts=[dict(user_id=x*32, devices=0, vaults=0, account_policies=0) for x in ('a','b')],
        Android_or_Native_verified=False)


class FreshOrigin(unittest.TestCase):
    def harness(self):
        legacy, events, verifier = PreloginPrepare.harness(self)
        stack = ExitStack(); self.addCleanup(stack.close)
        root = legacy.root
        stack.enter_context(patch.object(hosted, 'owned_root', return_value=root))
        stack.enter_context(patch.object(Path, 'is_symlink', return_value=False))
        origin = hosted.HostedOrigin(root, fixture=quota.FIXTURE, fresh_quota32=True)
        stack.enter_context(patch.object(origin, 'fixture_script', return_value=legacy.fixture_script()))
        stack.enter_context(patch.object(origin, 'save', lambda: events.append(('save', origin.state['result']))))
        stack.enter_context(patch.object(origin, 'command', lambda argv, stage, **kwargs: events.append(('command', stage))))
        stack.enter_context(patch.object(origin, 'start_container', lambda *args: events.append(('container', args[0]))))
        def spawn(name, *args):
            events.append(('spawn', name))
            child = SimpleNamespace(poll=lambda: None)
            origin.processes.append(child)
            return child
        stack.enter_context(patch.object(origin, 'spawn', spawn))
        stop = stack.enter_context(patch.object(origin, 'stop', side_effect=AssertionError('implicit Stop')))
        originals = []
        def zero(path, *, fresh=False):
            self.assertIs(path, root); self.assertIs(fresh, True)
            self.assertEqual(origin.state['result'], 'ready')
            self.assertEqual(events[-1], ('save', 'ready'))
            self.assertIn(('private-config', 'device-private.json'), events)
            value = proof(origin.state); originals.append(value); events.append(('fresh-zero', path))
            return value
        verifier.side_effect = zero
        return origin, events, verifier, originals, stop

    def refused(self, origin):
        with self.assertRaisesRegex(RuntimeError, '^OWNED_AUTH_FRESH_QUOTA32_PREPARE_UNAVAILABLE$'): origin.prepare()
        self.assertEqual(origin._fresh_state, 'FAILED')
        self.assertIsNone(origin._fresh_prepare_proof)

    def test_fresh_constructor_never_reads_old_owner_and_rejects_existing_or_foreign_root(self):
        origin, events, _, _, _ = self.harness()
        for root, exists in ((origin.root, True), (origin.root/'foreign', False)):
            reads = []
            with patch.object(Path, 'exists', return_value=exists), \
                    patch.object(hosted, 'read_json', side_effect=lambda *a: reads.append(a)):
                with self.assertRaisesRegex(RuntimeError, '^OWNED_AUTH_FRESH_QUOTA32_PREPARE_UNAVAILABLE$'):
                    hosted.HostedOrigin(root, fixture=quota.FIXTURE, fresh_quota32=True)
            self.assertEqual(reads, [])

    def test_exact_fresh_selector_and_fixture_refuse_private_inputs_before_FS(self):
        origin, _, _, _, _ = self.harness()
        with patch.object(Path, 'exists', side_effect=AssertionError('unexpected FS')):
            for root, fixture, flag in ((origin.root, quota.FIXTURE, 1), (origin.root, 'auth', True),
                    (origin.root, Private(), True), (Private(), quota.FIXTURE, True)):
                with self.assertRaisesRegex(RuntimeError, '^OWNED_AUTH_FRESH_QUOTA32_PREPARE_UNAVAILABLE$'):
                    hosted.HostedOrigin(root, fixture=fixture, fresh_quota32=flag)

    def test_actual_unsupported_host_rejects_before_root_metadata_or_private_read(self):
        origin, _, _, _, _ = self.harness()
        touched = []
        with patch.object(hosted, 'require_hosted', auth.require_hosted), \
                patch.object(auth, 'sys', SimpleNamespace(platform='win32')), \
                patch.object(Path, 'exists', side_effect=lambda *a: touched.append(True)), \
                patch.object(hosted, 'read_json', side_effect=lambda *a: touched.append(True)):
            with self.assertRaisesRegex(RuntimeError, '^OWNED_AUTH_FRESH_QUOTA32_PREPARE_UNAVAILABLE$'):
                hosted.HostedOrigin(origin.root, fixture=quota.FIXTURE, fresh_quota32=True)
        self.assertEqual(touched, [])

    def test_original_prepare_owns_same_historical_proof_before_any_consumer(self):
        origin, events, verifier, originals, stop = self.harness()
        result = origin.prepare(); events.append(('consumer-would-start-after-return',))
        verifier.assert_called_once_with(origin.root, fresh=True)
        self.assertEqual(events[-2], ('fresh-zero', origin.root))
        self.assertIs(origin._fresh_prepare_proof, originals[0])
        self.assertEqual(origin._fresh_state, 'PREPARED')
        self.assertEqual(result['fixture'], quota.FIXTURE)
        self.assertIs(result['Android_TLS_or_auth_verified'], False)
        self.assertNotIn('accounts', result); self.assertNotIn('fresh_prepare_proof', result)
        stop.assert_not_called()

    def test_duplicate_prepare_burns_without_another_action_or_zero_query(self):
        origin, events, verifier, _, stop = self.harness(); origin.prepare()
        before = list(events); self.refused(origin)
        self.assertEqual(events, before); self.assertEqual(verifier.call_count, 1); stop.assert_not_called()

    def test_changed_current_owned_root_refuses_before_prepare_side_effects(self):
        origin, events, verifier, _, stop = self.harness(); before = list(events)
        with patch.object(hosted, 'owned_root', return_value=origin.root/'other'): self.refused(origin)
        self.assertEqual(events, before); verifier.assert_not_called(); stop.assert_not_called()

    def test_unknown_or_private_zero_error_retains_service_owner_without_retry_or_Stop(self):
        for returned in (Private(), RuntimeError(Private())):
            origin, events, verifier, _, stop = self.harness()
            if isinstance(returned, BaseException): verifier.side_effect = returned
            else: verifier.side_effect = None; verifier.return_value = returned
            self.refused(origin)
            self.assertEqual(len(origin.processes), 2); self.assertEqual(origin.state['result'], 'ready')
            before = list(events); self.refused(origin); self.assertEqual(events, before)
            self.assertEqual(verifier.call_count, 1); stop.assert_not_called(); self.doCleanups()

    def test_swallowed_reentry_during_service_action_blocks_next_prepare_action(self):
        origin, events, verifier, _, stop = self.harness()
        def command(*args, **kwargs):
            events.append(('reenter',))
            try: origin.prepare()
            except RuntimeError: pass
        with patch.object(origin, 'command', command): self.refused(origin)
        self.assertEqual(events[-1], ('reenter',)); verifier.assert_not_called(); stop.assert_not_called()
        self.assertFalse(any(e[0] == 'container' for e in events))

    def test_readiness_reentry_is_not_swallowed_into_another_clock_or_retry(self):
        origin, events, verifier, _, stop = self.harness(); clocks = []
        def clock(): clocks.append(True); return 100
        def reenter(*args):
            try: origin.prepare()
            except RuntimeError: pass
            return object()
        with patch.object(hosted.time, 'monotonic', clock), patch.object(hosted, 'origin_handshake', reenter): self.refused(origin)
        self.assertEqual(len(clocks), 1); verifier.assert_not_called(); stop.assert_not_called()

    def test_ready_state_save_reentry_prevents_zero_even_after_original_ready_save(self):
        origin, events, verifier, _, stop = self.harness()
        def save():
            events.append(('save', origin.state['result']))
            if origin.state['result'] == 'ready':
                try: origin.prepare()
                except RuntimeError: pass
        with patch.object(origin, 'save', save): self.refused(origin)
        self.assertEqual(events[-1], ('save', 'ready')); verifier.assert_not_called(); stop.assert_not_called()


class FreshProof(unittest.TestCase):
    def scope(self):
        root = Path(__file__).absolute().parent/'never-created-fresh-proof'
        state = dict(owner='12345-1-'+'a'*24, fixture_script_sha256='a'*64)
        config = dict(accounts=[dict(username='android-owned-'+x+'-'+'a'*12) for x in ('a','b')],
            environment={'SYNC_DATABASE_URL':'synthetic-private'})
        return root, state, config

    def test_invalid_fresh_flag_fails_before_owned_source_or_FS(self):
        root, _, _ = self.scope()
        with patch.object(quota, '_owned', side_effect=AssertionError('unexpected source read')) as owned:
            with self.assertRaisesRegex(RuntimeError, '^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'): quota.verify_prepared(root, fresh=1)
            owned.assert_not_called()

    def test_strict_existing_or_symlink_proof_never_reconciles_or_reads_private_metadata(self):
        root, state, config = self.scope()
        for exists, link in ((True, False), (False, True)):
            with patch.object(quota, '_owned', return_value=('12345-1',state,config)), \
                    patch.object(Path, 'exists', return_value=exists), patch.object(Path, 'is_symlink', return_value=link), \
                    patch.object(quota, 'read_metadata', side_effect=AssertionError('cached proof read')) as read:
                with self.assertRaisesRegex(RuntimeError, '^SIGNED_QUOTA_FIXTURE_UNAVAILABLE$'): quota.verify_prepared(root, fresh=True)
                read.assert_not_called()

    def test_default_cached_history_stays_readonly_without_new_zero(self):
        root, state, config = self.scope(); value = proof(state)
        with patch.object(quota, '_owned', return_value=('12345-1',state,config)), \
                patch.object(Path, 'exists', return_value=True), patch.object(quota, 'read_metadata', return_value=value) as read:
            self.assertEqual(quota.verify_prepared(root), value)
            read.assert_called_once_with(root/quota.PROOF)

    def test_actual_verify_body_new_fresh_proof_uses_zero_readonly_queries_once_and_exclusive_write(self):
        root, state, config = self.scope(); queries = []
        class Database:
            sqlite = False
            def __init__(self, url): self.engine = SimpleNamespace(dispose=lambda: queries.append(('dispose',)))
            @contextmanager
            def read_only(self): yield self
            def execute(self, query, values):
                queries.append((query, values))
                result = ('a' if '-a-' in values['name'] else 'b')*32 if query=='SELECT id FROM users WHERE username=:name' else 0
                return SimpleNamespace(scalar_one=lambda: result)
        package = ModuleType('sync_server'); package.__path__=[]
        with patch.object(quota, '_owned', return_value=('12345-1',state,config)), \
                patch.object(Path, 'exists', return_value=False), patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(quota.auth, 'atomic_json') as write, \
                patch.dict(sys.modules, {'sync_server':package, 'sync_server.database':SimpleNamespace(Database=Database), 'sqlalchemy':SimpleNamespace(text=lambda q:q)}):
            result = quota.verify_prepared(root, fresh=True)
            self.assertEqual(result, proof(state))
            write.assert_called_once_with(root/quota.PROOF, result, exclusive=True)
            self.assertEqual(len(queries), 9)
            self.assertEqual(queries[-1], ('dispose',))


if __name__ == '__main__':
    unittest.main()
