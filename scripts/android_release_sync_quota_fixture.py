"""New owned quota32 factory and narrow provider reads; never product acceptance.

Prepare/GET must precede the historical zero-device proof, then Android login
and the separate real-API Setup actor. No quota mutation, fault injection,
object GET/write or device action is exposed here. Native/UI/stop/cold and the
scope-bound upload413 response recorder remain separate components.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from functools import wraps

import android_auth_fixture as auth
import android_release_sync_quota_validation as validation
import android_sync_fixture as sync

FIXTURE = 'signed-release-quota32'
PROOF = 'signed-quota-prepare-zero.json'


def need(value):
    if not value:
        raise RuntimeError('SIGNED_QUOTA_FIXTURE_UNAVAILABLE')


def closed_errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            raise RuntimeError('SIGNED_QUOTA_FIXTURE_UNAVAILABLE') from None
    return call


def read_metadata(path, limit=8192):
    need(path.absolute() == path.resolve() and not path.is_symlink() and path.is_file())
    stat = path.stat(); size = stat.st_size
    need(stat.st_nlink == 1 and 0 < size <= limit)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    need(len(raw) == size and len(raw) <= limit)
    def pairs(items):
        value = dict(items); need(len(value) == len(items)); return value
    def constant(_): need(False)
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _owned(owner, *, ready=False, state=None, config=None):
    auth.require_hosted()
    need(owner.absolute() == owner.resolve() == auth.owned_root() and not owner.is_symlink())
    config = auth.fixed_service(owner) if config is None else config
    state = auth.read_json(owner/'owner-state.json') if state is None else state
    run = os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    need(validation._pattern(run, r'[1-9][0-9]{0,17}-[1-9][0-9]{0,5}')
         and type(state) is dict and validation._same(state.get('schema'), 1)
         and validation._same(state.get('source'), validation.SYNC) and state.get('fixture') == FIXTURE
         and type(state.get('owner')) is str and re.fullmatch(re.escape(run)+r'-[0-9a-f]{24}', state['owner'])
         and type(state.get('result')) is str and state['result'] in (('ready',) if ready else ('preparing','ready')))
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    need(validation._same(state.get('fixture_script_sha256'), script_hash)
         and type(config) is dict and validation._same(config.get('source'), validation.SYNC)
         and validation._same(config.get('bucket'), 'onx-android-auth-' + run)
         and type(config.get('environment')) is dict
         and validation._same(config['environment'].get('SYNC_S3_BUCKET'), config['bucket'])
         and validation._same(config['environment'].get('SYNC_S3_ENDPOINT'), 'http://127.0.0.1:19072'))
    return run, state, config


@closed_errors
def normal_target(owner):
    expected = dict(revision=0, operation_id=None, mode='normal')
    for path in (owner/'control.json', sync.control_root(owner)/'control.json'):
        need(validation._same(read_metadata(path, 1024), expected))
    target = sync.control_root(owner)/'target.json'
    if not target.exists() and not target.is_symlink():
        return None
    value = read_metadata(target, 1024)
    need(validation._closed(value, ('schema','vault_id')) and validation._same(value['schema'], 1)
         and validation._pattern(value['vault_id'], r'[0-9a-f]{32}'))
    return value['vault_id']


class NormalOnly:
    def __init__(self, app, owner):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        normal_target(self.owner)
        await self.app(scope, receive, send)


@closed_errors
def build_app(owner):
    _,_,config = _owned(owner)
    from sync_server.__main__ import Database, S3Objects, create_app, operations_path
    env = config['environment']
    app = create_app(Database(env['SYNC_DATABASE_URL']), S3Objects(env['SYNC_S3_ENDPOINT'], env['SYNC_S3_BUCKET']),
        Path(env['SYNC_STAGING_DIR']), quota=validation.QUOTA, operations_path=operations_path(), operator_user_ids=())
    return NormalOnly(auth.DeliveryProxy(sync.TargetedDeliveryProxy(app, owner, uploads=True), owner), owner)


def proof_shape(value, run, state):
    keys = ('schema','source','fixture','owner_run','owner','fixture_script_sha256','factory_default_quota',
            'stage','accounts','Android_or_Native_verified')
    need(validation._closed(value, keys) and type(value['accounts']) is list and len(value['accounts']) == 2)
    accounts = []
    for account in value['accounts']:
        need(validation._closed(account, ('user_id','devices','vaults','account_policies'))
             and validation._pattern(account['user_id'], r'[0-9a-f]{32}'))
        expected = dict(user_id=account['user_id'], devices=0, vaults=0, account_policies=0)
        need(validation._same(account, expected)); accounts.append(expected)
    need(accounts[0]['user_id'] != accounts[1]['user_id'])
    expected = dict(schema=1, source=validation.SYNC, fixture=FIXTURE, owner_run=run, owner=state['owner'],
        fixture_script_sha256=state['fixture_script_sha256'], factory_default_quota=32,
        stage='prepare-before-Android-login-or-setup', accounts=accounts, Android_or_Native_verified=False)
    need(validation._same(value, expected))
    return expected


@closed_errors
def require_prepare_proof(owner, *, state=None, config=None):
    run,state,_ = _owned(owner, ready=True, state=state, config=config)
    return proof_shape(read_metadata(owner/PROOF), run, state)


@closed_errors
def verify_prepared(owner):
    run,state,config = _owned(owner, ready=True)
    if (owner/PROOF).exists() or (owner/PROOF).is_symlink():
        # Reconcile only this original exclusive record; never requery a later
        # login state or rewrite/repair a partial/unknown first write.
        return proof_shape(read_metadata(owner/PROOF), run, state)
    accounts = config.get('accounts')
    need(type(accounts) is list and len(accounts) == 2)
    from sync_server.database import Database
    from sqlalchemy import text
    db = Database(config['environment']['SYNC_DATABASE_URL'])
    values = []
    try:
        need(db.sqlite is False)
        with db.read_only() as connection:
            for kind, account in zip(('a','b'), accounts):
                need(type(account) is dict and validation._pattern(account.get('username'), 'android-owned-'+kind+r'-[0-9a-f]{12}'))
                user = connection.execute(text('SELECT id FROM users WHERE username=:name'), {'name':account['username']}).scalar_one()
                need(validation._pattern(user, r'[0-9a-f]{32}'))
                counts = {}
                for table, key in (('devices','devices'),('vaults','vaults'),('account_policy','account_policies')):
                    count = connection.execute(text('SELECT COUNT(*) FROM '+table+' WHERE user_id=:user'), {'user':user}).scalar_one()
                    need(type(count) is int and count == 0); counts[key] = count
                values.append(dict(user_id=user, **counts))
    finally:
        db.engine.dispose()
    value = dict(schema=1, source=validation.SYNC, fixture=FIXTURE, owner_run=run, owner=state['owner'],
        fixture_script_sha256=state['fixture_script_sha256'], factory_default_quota=32,
        stage='prepare-before-Android-login-or-setup', accounts=values, Android_or_Native_verified=False)
    value = proof_shape(value, run, state)
    auth.atomic_json(owner/PROOF, value, exclusive=True)
    return value


def _empty_owned_s3(config, scope):
    from sync_server.storage import S3Objects
    objects = S3Objects(config['environment']['SYNC_S3_ENDPOINT'], config['bucket'])
    prefix = scope['remote_vault'] + '/'
    try:
        value = objects.client.list_objects_v2(Bucket=config['bucket'], Prefix=prefix, MaxKeys=1)
        allowed = {'ResponseMetadata','IsTruncated','Name','Prefix','MaxKeys','KeyCount','Contents',
                   'CommonPrefixes','NextContinuationToken','EncodingType'}
        required = {'ResponseMetadata','IsTruncated','Name','Prefix','MaxKeys','KeyCount'}
        need(type(value) is dict and all(type(k) is str for k in value)
             and required <= set(value) <= allowed and type(value['ResponseMetadata']) is dict)
        meta = value['ResponseMetadata']
        need(validation._same(meta.get('HTTPStatusCode'), 200) and validation._same(meta.get('RetryAttempts'), 0)
             and value['IsTruncated'] is False and validation._same(value['Name'], config['bucket'])
             and validation._same(value['Prefix'], prefix) and validation._same(value['MaxKeys'], 1)
             and validation._same(value['KeyCount'], 0) and validation._same(value.get('Contents', []), [])
             and validation._same(value.get('CommonPrefixes', []), []) and value.get('NextContinuationToken') is None
             and (value.get('EncodingType') is None or validation._same(value['EncodingType'], 'url')))
    finally:
        objects.client.close()
    return dict(owner_run=scope['owner_run'], remote_vault=scope['remote_vault'],
                listing_complete_reported=True, object_count=0, continuation=None)


@closed_errors
def observe_quota_provider(owner, scope, *, before=None):
    scope = validation._scope(scope)
    run,_,config = _owned(owner, ready=True)
    need(scope['owner_run'] == run and normal_target(owner) == scope['remote_vault'])
    if before is not None:
        validation._provider_before(before, scope)
    observed = sync.observe_provider(owner, scope['remote_vault'])
    need(type(observed) is dict and observed.get('source') == validation.SYNC
         and observed.get('provider') == 'actual PostgreSQL; repeatable-read/read-only'
         and observed.get('secrets_selected') is False and observed.get('object_IO') is False
         and observed.get('client_received_or_applied_acknowledgement') is False)
    vault, rows = observed['vault'], observed['rows']
    need(validation._closed(vault, ('id','name','sequence','quota','used'))
         and validation._same(vault['id'], scope['remote_vault']) and validation._same(vault['quota'], 32)
         and validation._same(vault['used'], 0) and validation._same(vault['sequence'], 0)
         and validation._closed(rows, ('revisions','files','objects','uploads','device_progress'))
         and all(validation._same(rows[k], []) for k in ('revisions','files','objects','uploads')))
    s3 = _empty_owned_s3(config, scope)
    value = dict(schema=1, owner_run=run, source=validation.SYNC, backend='PostgreSQL/S3', remote_vault=scope['remote_vault'],
        quota=32, used=0, sequence=0, reserved_bytes=0, rows=dict(revisions=[], files=[], objects=[], uploads=[]),
        progress=rows['device_progress'], delivery=observed['delivery'], s3=s3)
    if before is None: validation._provider_before(value, scope)
    else: validation._provider_after(value, before, scope)
    # The pure helpers have validated every retained key/type; detach mutable
    # provider response lists before returning any approved metadata.
    return json.loads(json.dumps(value, separators=(',', ':')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seed','serve'))
    parser.add_argument('--owner-run')
    args = parser.parse_args(); owner = auth.owned_root()
    run,_,_ = _owned(owner)
    if args.action == 'seed':
        auth.main(); return
    need(args.owner_run == run)
    import uvicorn
    uvicorn.run(build_app(owner), host='127.0.0.1', port=19073, workers=1, access_log=False, log_level='warning')


if __name__ == '__main__':
    try: main()
    except Exception:
        print('SIGNED_QUOTA_FIXTURE_UNAVAILABLE', file=__import__('sys').stderr)
        raise SystemExit(1)
