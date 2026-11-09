"""Fresh Setup source checks in RAM; no real files, HTTPS, PG or Android."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch
import uuid

import android_release_sync_quota_fixture as quota
import android_sync_setup as setup


class SecretError(Exception):
    def __str__(self):
        raise AssertionError('private exception must not be stringified')


class RamPath:
    def __init__(self, ram, name):
        self.ram, self.name = ram, name

    def __truediv__(self, name):
        key = self.name + '/' + name
        return self.ram.paths.setdefault(key, RamPath(self.ram, key))

    def resolve(self):
        self.ram.events.append(('resolve', self.name))
        return self

    def exists(self):
        self.ram.events.append(('exists', self.name))
        return self.name in self.ram.files or self.name in self.ram.dirs

    def is_symlink(self):
        self.ram.events.append(('link', self.name))
        return self.name in self.ram.links

    def is_dir(self):
        self.ram.events.append(('is_dir', self.name))
        return self.name in self.ram.dirs

    def mkdir(self, *, mode, exist_ok):
        self.ram.events.append(('mkdir', self.name, mode, exist_ok))
        if self.name in self.ram.dirs and not exist_ok:
            raise FileExistsError('private path')
        self.ram.dirs.add(self.name)

    def iterdir(self):
        self.ram.events.append(('iterdir', self.name))
        return iter(self / name[len(self.name)+1:] for name in self.ram.files
            if name.startswith(self.name + '/'))


class Ram:
    def __init__(self, fixture='signed-release-quota32'):
        self.events, self.paths, self.links, self.hooks = [], {}, set(), {}
        self.dirs = {'owner'}
        self.owner = RamPath(self, 'owner')
        self.config = dict(accounts=[dict(username='android-owned-a-'+'a'*12, password='p'*48),
            dict(username='android-owned-b-'+'b'*12, password='q'*48)])
        self.endpoint = 'https://owned-test.trycloudflare.com'
        self.files = {'owner/owner-state.json': dict(source=setup.original.SERVICE_SHA,
            result='ready', fixture=fixture, endpoint=self.endpoint),
            'owner/device-private.json': dict(endpoint=self.endpoint, accounts=self.config['accounts'])}
        self.counter, self.requests = 10, []
        self.listed = []

    def event(self, kind, *args):
        self.events.append((kind, *args))
        if kind in self.hooks:
            self.hooks[kind](*args)

    def read(self, path):
        self.event('read', path.name)
        return self.files[path.name]

    def write(self, path, value, *, exclusive):
        self.event('write', path.name)
        assert exclusive is True
        if path.name in self.files:
            raise FileExistsError('private')
        self.files[path.name] = value

    def history(self, owner, **kwargs):
        self.event('history')
        assert owner is self.owner
        assert kwargs['state'] is self.files['owner/owner-state.json']
        assert kwargs['config'] is self.config

    def identifier(self):
        self.counter += 1
        return uuid.UUID(int=self.counter)

    def observe(self, owner, vault):
        self.event('observe')
        return dict(rows=dict(revisions=[]))

    def freeze(self, owner, vault):
        self.event('freeze')

    def connection(self, hostname, **kwargs):
        ram = self
        self.event('http_open')
        class Connection:
            def request(self, method, path, *, body, headers):
                self.method, self.path = method, path
                self.body = None if body is None else json.loads(body)
                ram.requests.append((method, path))
                ram.event('http_request', method, path)

            def getresponse(self):
                ram.event('http_response')
                if self.path == '/sync/v1/auth/sessions':
                    value = dict(access_token='a'*43, refresh_token='b'*64, expires_in=900,
                        device_id='c'*32, must_change_credentials=False)
                elif self.method == 'GET':
                    items = ram.listed
                    if items == 'match':
                        name = ram.files['owner/sync-setup/vault-intent.json']['body']['name']
                        items = [dict(id='d'*32, name=name)]
                    value = dict(items=items)
                else:
                    value = dict(vault_id='d'*32, name=self.body['name'])
                class Response:
                    status = 200
                    def read(self, limit):
                        ram.event('http_read')
                        assert limit == 65537
                        return json.dumps(value).encode()
                return Response()

            def close(self):
                ram.event('http_close')
        return Connection()


@contextmanager
def controlled(ram):
    with ExitStack() as stack:
        for owner, name, value in (
                (setup.original, 'owned_root', lambda: ram.owner),
                (setup.original, 'fixed_service', lambda owner: ram.config),
                (setup.original, 'read_json', ram.read),
                (setup.original, 'atomic_json', ram.write),
                (quota, 'require_prepare_proof', ram.history),
                (setup, 'observe_provider', ram.observe),
                (setup, 'freeze_target', ram.freeze),
                (setup.uuid, 'uuid4', ram.identifier),
                (setup.time, 'time', lambda: 100),
                (setup.ssl, 'create_default_context', lambda: object()),
                (setup.http.client, 'HTTPSConnection', ram.connection)):
            stack.enter_context(patch.object(owner, name, value))
        yield


class FreshQuotaSetup(unittest.TestCase):
    def unavailable(self, function):
        with self.assertRaisesRegex(RuntimeError, '^OWNED_SYNC_FRESH_QUOTA32_SETUP_UNAVAILABLE$'):
            function()

    def test_invalid_mode_refuses_before_private_source_io(self):
        for value in (None, 0, 1, 'true', object()):
            ram = Ram()
            with controlled(ram):
                self.unavailable(lambda: setup.Setup(ram.owner, fresh_quota32=value))
                self.assertEqual(ram.events, [])

    def test_original_quota_history_precedes_exclusive_directory_and_no_zero_requery(self):
        ram = Ram()
        with controlled(ram), patch.object(quota, 'verify_prepared',
                side_effect=AssertionError('no zero requery')):
            actor = setup.Setup(ram.owner, fresh_quota32=True)
            self.assertEqual(actor._fresh_state, 'NEW')
            self.assertEqual(ram.requests, [])
            history = ram.events.index(('history',))
            mkdir = ram.events.index(('mkdir', 'owner/sync-setup', 0o700, False))
            self.assertLess(history, mkdir)
            self.assertEqual(sum(e[0] == 'history' for e in ram.events), 1)

    def test_wrong_fixture_existing_root_and_private_history_failure_never_setup_or_retry(self):
        for mode in ('fixture', 'existing', 'history'):
            ram = Ram('sync' if mode == 'fixture' else 'signed-release-quota32')
            if mode == 'existing':
                ram.dirs.add('owner/sync-setup')
            if mode == 'history':
                ram.hooks['history'] = lambda: (_ for _ in ()).throw(SecretError())
            with controlled(ram):
                self.unavailable(lambda: setup.Setup(ram.owner, fresh_quota32=True))
                self.assertEqual(ram.requests, [])
                self.assertFalse(any(e[0] in ('write', 'observe', 'freeze') for e in ram.events))
                if mode != 'existing':
                    self.assertFalse(any(e[0] == 'mkdir' for e in ram.events))

    def test_fresh_concrete_setup_runs_original_sequence_once_and_preserves_result_schema(self):
        ram = Ram()
        with controlled(ram):
            actor = setup.Setup(ram.owner, fresh_quota32=True)
            result = actor.run()
            self.assertEqual(ram.requests, [('POST', '/sync/v1/auth/sessions'),
                ('GET', '/sync/v1/vaults'), ('POST', '/sync/v1/vaults')])
            self.assertEqual(actor._fresh_state, 'DONE')
            self.assertEqual(actor._fresh_seen, {'session', 'vault'})
            self.assertEqual(set(result), {'source', 'kind', 'vault', 'device_id', 'real_API_setup',
                'desktop_or_Android_verified', 'provider', 'credentials_or_session_in_artifacts'})
            self.assertIs(result['desktop_or_Android_verified'], False)
            self.assertIs(result['credentials_or_session_in_artifacts'], False)
            events = list(ram.events)
            self.unavailable(actor.run)
            self.assertEqual(ram.events, events)

    def test_direct_operations_outside_own_run_are_revoked_before_io(self):
        for name, args in (('session', ()), ('vault', ({},)), ('intent', ('login', {})),
                ('request', ('GET', '/sync/v1/vaults'))):
            ram = Ram()
            with controlled(ram):
                actor = setup.Setup(ram.owner, fresh_quota32=True)
                before = list(ram.events)
                self.unavailable(lambda: getattr(actor, name)(*args))
                self.unavailable(actor.run)
                self.assertEqual(ram.events, before)
                self.assertEqual(actor._fresh_state, 'FAILED')

    def test_nonempty_or_linked_root_refuses_before_login_and_preserves_original(self):
        for mode in ('file', 'link'):
            ram = Ram()
            with controlled(ram):
                actor = setup.Setup(ram.owner, fresh_quota32=True)
                private = object()
                if mode == 'file':
                    ram.files['owner/sync-setup/unknown.private'] = private
                else:
                    ram.links.add('owner/sync-setup')
                self.unavailable(actor.run)
                self.assertEqual(ram.requests, [])
                self.assertFalse(any(e[0] == 'write' for e in ram.events))
                if mode == 'file':
                    self.assertIs(ram.files['owner/sync-setup/unknown.private'], private)

    def test_existing_intent_response_or_receipt_is_not_read_or_reconciled_in_fresh_mode(self):
        for name in ('login-intent.json', 'login-response.private.json', 'vault-intent.json',
                'vault-response.private.json', 'vault-receipt.json'):
            ram = Ram()
            with controlled(ram):
                actor = setup.Setup(ram.owner, fresh_quota32=True)
                actor._fresh_state = 'RUNNING'  # Controlled entry-point gate, not runtime evidence.
                key = 'owner/sync-setup/' + name
                private = object()
                ram.files[key] = private
                before = len(ram.events)
                self.unavailable(actor.session if name.startswith('login') else lambda: actor.vault({}))
                self.assertFalse(any(e[0] == 'read' and e[1] == key for e in ram.events[before:]))
                self.assertEqual(ram.requests, [])
                self.assertIs(ram.files[key], private)

    def test_existing_remote_name_is_failure_without_reconcile_or_create(self):
        ram = Ram()
        ram.listed = 'match'
        with controlled(ram):
            actor = setup.Setup(ram.owner, fresh_quota32=True)
            self.unavailable(actor.run)
            self.assertEqual(ram.requests, [('POST', '/sync/v1/auth/sessions'),
                ('GET', '/sync/v1/vaults')])
            self.assertNotIn('owner/sync-setup/vault-receipt.json', ram.files)
            self.assertFalse(any(e[0] in ('observe', 'freeze') for e in ram.events))

    def test_partial_response_writer_is_private_failure_without_readback_or_repost(self):
        ram = Ram()
        key = 'owner/sync-setup/login-response.private.json'
        private = object()
        def fail(name):
            if name == key:
                ram.files[key] = private
                raise SecretError()
        ram.hooks['write'] = fail
        with controlled(ram):
            actor = setup.Setup(ram.owner, fresh_quota32=True)
            self.unavailable(actor.run)
            self.assertEqual(ram.requests, [('POST', '/sync/v1/auth/sessions')])
            self.assertIs(ram.files[key], private)
            self.assertFalse(any(e == ('read', key) for e in ram.events))
            events = list(ram.events)
            self.unavailable(actor.run)
            self.assertEqual(ram.events, events)

    def test_swallowed_reentry_after_actual_send_allows_only_original_http_close(self):
        ram = Ram()
        with controlled(ram):
            actor = setup.Setup(ram.owner, fresh_quota32=True)
            def reenter(*args):
                try:
                    actor.run()
                except RuntimeError:
                    pass
            ram.hooks['http_request'] = reenter
            self.unavailable(actor.run)
            sent = next(i for i, e in enumerate(ram.events) if e[0] == 'http_request')
            self.assertEqual(ram.events[sent+1:], [('http_close',)])
            self.assertEqual(len(ram.requests), 1)

    def test_concrete_step_failures_do_not_continue_provider_freeze_or_retry(self):
        for stage in ('http_request', 'http_response', 'http_read', 'observe', 'freeze'):
            ram = Ram()
            ram.hooks[stage] = lambda *args: (_ for _ in ()).throw(SecretError())
            with controlled(ram):
                actor = setup.Setup(ram.owner, fresh_quota32=True)
                self.unavailable(actor.run)
                first = next(i for i, e in enumerate(ram.events) if e[0] == stage)
                self.assertTrue(all(e[0] == 'http_close' for e in ram.events[first+1:]))
                before = list(ram.events)
                self.unavailable(actor.run)
                self.assertEqual(ram.events, before)

    def test_default_recovery_keeps_original_cached_session_vault_and_repeated_run(self):
        ram = Ram('sync')
        with controlled(ram):
            actor = setup.Setup(ram.owner)
            first, second = actor.run(), actor.run()
            self.assertEqual(first, second)
            self.assertEqual(len(ram.requests), 3)
            self.assertIn(('mkdir', 'owner/sync-setup', 0o700, True), ram.events)
            self.assertFalse(any(e[0] in ('history', 'iterdir') for e in ram.events))
            self.assertEqual(sum(e[0] == 'observe' for e in ram.events), 2)
            self.assertEqual(sum(e[0] == 'freeze' for e in ram.events), 2)

    def test_real_fs_sql_process_device_and_uncontrolled_clock_are_not_used(self):
        ram = Ram()
        with controlled(ram), ExitStack() as stack:
            def forbidden(*args, **kwargs):
                raise AssertionError('unexpected real side effect')
            for owner, name in ((Path, 'open'), (Path, 'read_bytes'), (Path, 'write_bytes'),
                    (Path, 'mkdir'), (sqlite3, 'connect'), (subprocess, 'run'),
                    (subprocess, 'Popen'), (os, 'open'), (os, 'system'),
                    (time, 'monotonic'), (time, 'sleep')):
                stack.enter_context(patch.object(owner, name, forbidden))
            actor = setup.Setup(ram.owner, fresh_quota32=True)
            actor.run()
            self.assertEqual(len(ram.requests), 3)


if __name__ == '__main__':
    unittest.main()
