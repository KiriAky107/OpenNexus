"""One owned prelaunch config replacement, with original receipts and no replay."""
import hashlib
import os
import re
import stat

import android_release_sdk_archive as kernel
import android_release_sdk_config_prepare as transform
import android_release_sdk_ini as ini

require = kernel.require
INTENT_KEYS = ('owner', 'avd', 'lineage', 'chain', 'locator_sha256', 'create_intent_sha256', 'original', 'plan', 'automatic_retry')
RESULT_KEYS = ('owner', 'avd', 'passed', 'intent_sha256', 'attempt_sha256', 'replace_returned',
               'after_identity', 'temporary_absent', 'cleanup_unknown', 'guard_code', 'error_kind', 'runtime_verified')


def paths(ctx):
    name = 'opennexus-ci-' + ctx['owner']['GITHUB_RUN_ID'] + '-' + ctx['owner']['GITHUB_RUN_ATTEMPT']
    avd = ctx['parent'] / 'avd' / (name + '.avd')
    return name, avd / 'config.ini', avd.with_suffix('.ini'), avd / '.opennexus-config-prepare.tmp'


def identity(ctx, path, directory=False):
    value = path.lstat()
    require(not path.is_symlink() and path.resolve() == path and value.st_uid == ctx['owner']['uid'] and
            (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)), 'SDK_CONFIG_OWNER')
    return [value.st_dev, value.st_ino, value.st_mode, value.st_uid]


def guard(ctx, api, lineage, chain=None, locator_sha256=None):
    require(api.context() == ctx and api.boot() == ctx['owner']['host_boot_id'], 'SDK_CONFIG_OWNER')
    api.prepared(ctx); api.image_ready(ctx); api.validate_probe(ctx); api.layout_ready(ctx)
    actual = {key: hashlib.sha256(api.raw_file(ctx['evidence'] / ('sdk-' + key + '.json'))).hexdigest()
              for key in ('registry-result', 'image-result', 'probe-result', 'runtime-dependencies-result')}
    require(actual == lineage, 'SDK_CONFIG_LINEAGE')
    name, config, locator, temporary = paths(ctx)
    current = {key: identity(ctx, path, True) for key, path in
               (('parent', ctx['parent']), ('evidence', ctx['evidence']), ('root', ctx['root']),
                ('avd_home', config.parent.parent), ('avd', config.parent))}
    current['locator'] = identity(ctx, locator)
    require(chain is None or current == chain, 'SDK_CONFIG_OWNER')
    raw = api.raw_file(locator)
    found = [line[5:] for line in raw.decode('ascii').splitlines() if line.startswith('path=')]
    require(found == [str(config.parent)] and
            (locator_sha256 is None or hashlib.sha256(raw).hexdigest() == locator_sha256), 'SDK_CONFIG_LINEAGE')
    return current, hashlib.sha256(raw).hexdigest()


def prepare_owned(ctx, api, lineage):
    """Called only after the new vendor create; all writes have one original intent."""
    chain, locator_sha = guard(ctx, api, lineage)
    name, config, locator, temporary = paths(ctx)
    forbidden = ['avd-config-prepare-intent', 'avd-config-prepare-attempt', 'avd-config-prepare-result',
                 'avd-binding', 'avd-config-semantics', 'avd-config-fingerprints', 'launch-observed']
    require(all(not (ctx['evidence'] / ('sdk-' + key + '.json')).exists() and
                not (ctx['evidence'] / ('sdk-' + key + '.json')).is_symlink() for key in forbidden) and
            all(not p.exists() and not p.is_symlink() for p in
                (temporary, ctx['parent'] / 'emulator.pid', ctx['parent'] / 'emulator.log')), 'SDK_ORIGINAL_RECORD_EXISTS')
    original_identity = identity(ctx, config)
    data = api.raw_file(config)
    try: output, plan = transform.prepare(ctx, data)
    except transform.ConfigError: raise kernel.ArchiveError('SDK_CONFIG_INPUT') from None
    original = dict(identity=original_identity, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    mtime_ns=config.stat().st_mtime_ns)
    def original_unchanged():
        guard(ctx, api, lineage, chain, locator_sha)
        require(identity(ctx, config) == original_identity and config.stat().st_mtime_ns == original['mtime_ns'] and
                api.raw_file(config) == data, 'SDK_CONFIG_CHANGED')
    original_unchanged()
    creation = ctx['evidence'] / 'sdk-avd-create-intent.json'
    creation_sha = hashlib.sha256(api.raw_file(creation)).hexdigest()
    intent = api.emit(ctx, 'avd-config-prepare-intent', dict(owner=ctx['owner'], avd=name, lineage=lineage,
        chain=chain, locator_sha256=locator_sha, create_intent_sha256=creation_sha,
        original=original, plan=plan, automatic_retry=False))
    intent_sha = hashlib.sha256(api.raw_file(intent)).hexdigest()
    result = dict(owner=ctx['owner'], avd=name, passed=False, intent_sha256=intent_sha, attempt_sha256=None,
                  replace_returned=False, after_identity=None, temporary_absent=False, cleanup_unknown=False,
                  guard_code=None, error_kind=None, runtime_verified=False)
    temp_identity = None
    try:
        original_unchanged()
        require(hashlib.sha256(api.raw_file(creation)).hexdigest() == creation_sha, 'SDK_CONFIG_LINEAGE')
        attempt = api.emit(ctx, 'avd-config-prepare-attempt', dict(owner=ctx['owner'], intent_sha256=intent_sha, attempted=True))
        result['attempt_sha256'] = hashlib.sha256(api.raw_file(attempt)).hexdigest()
        original_unchanged()
        require(hashlib.sha256(api.raw_file(creation)).hexdigest() == creation_sha, 'SDK_CONFIG_LINEAGE')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, original_identity[2] & 0o777)
        with os.fdopen(descriptor, 'wb') as stream:
            temp_identity = identity(ctx, temporary)
            require(temp_identity[2:] == original_identity[2:], 'SDK_CONFIG_OWNER')
            stream.write(output); stream.flush(); os.fsync(stream.fileno())
        require(identity(ctx, temporary) == temp_identity and api.raw_file(temporary) == output, 'SDK_CONFIG_CHANGED')
        original_unchanged()
        require(hashlib.sha256(api.raw_file(intent)).hexdigest() == intent_sha and
                hashlib.sha256(api.raw_file(creation)).hexdigest() == creation_sha and
                hashlib.sha256(api.raw_file(attempt)).hexdigest() == result['attempt_sha256'], 'SDK_CONFIG_LINEAGE')
        require(identity(ctx, config) == original_identity and config.stat().st_mtime_ns == original['mtime_ns'] and
                api.raw_file(config) == data and identity(ctx, temporary) == temp_identity and
                api.raw_file(temporary) == output, 'SDK_CONFIG_CHANGED')
        os.replace(temporary, config)
        result['replace_returned'] = True
        guard(ctx, api, lineage, chain, locator_sha)
        require(identity(ctx, config) == temp_identity and api.raw_file(config) == output, 'SDK_CONFIG_CHANGED')
        result['after_identity'] = identity(ctx, config)
        result['passed'] = True
    except Exception as error:
        result['guard_code'] = str(error) if type(error) is kernel.ArchiveError and str(error) in api.SAFE else 'SDK_CONFIG_UNAVAILABLE'
        result['error_kind'] = {kernel.ArchiveError: 'sequence_guard', PermissionError: 'permission_denied',
            FileExistsError: 'file_exists', FileNotFoundError: 'file_missing', OSError: 'io'}.get(type(error), 'unclassified')
    finally:
        try:
            guard(ctx, api, lineage, chain, locator_sha)
            if temporary.exists() or temporary.is_symlink():
                require(temp_identity is not None and identity(ctx, temporary) == temp_identity, 'SDK_CONFIG_CLEANUP')
                require(output.startswith(api.raw_file(temporary)), 'SDK_CONFIG_CLEANUP')
                require(identity(ctx, temporary) == temp_identity, 'SDK_CONFIG_CLEANUP')
                temporary.unlink()
            result['temporary_absent'] = not temporary.exists() and not temporary.is_symlink()
            require(result['temporary_absent'], 'SDK_CONFIG_CLEANUP')
        except Exception:
            result.update(passed=False, cleanup_unknown=True)
            result['guard_code'] = result['guard_code'] or 'SDK_CONFIG_CLEANUP'
    api.emit(ctx, 'avd-config-prepare-result', result)
    return result


def ready(ctx, api, data=None):
    """Use only the same original receipts; no repair, replay or config normalization."""
    intent = api.read(ctx, 'avd-config-prepare-intent', INTENT_KEYS)
    value = api.read(ctx, 'avd-config-prepare-result', RESULT_KEYS)
    name, config, locator, temporary = paths(ctx)
    require(value['passed'] is True and value['replace_returned'] is True and value['temporary_absent'] is True and
            value['cleanup_unknown'] is False and value['runtime_verified'] is False and
            value['guard_code'] is None and value['error_kind'] is None and value['avd'] == intent['avd'] == name and
            intent['automatic_retry'] is False, 'SDK_CONFIG_NOT_READY')
    require(type(intent['owner']['uid']) is int and type(value['owner']['uid']) is int, 'SDK_CONFIG_NOT_READY')
    def digest(v): return type(v) is str and re.fullmatch(r'[0-9a-f]{64}', v) is not None
    def ident(v): return type(v) is list and len(v) == 4 and all(type(i) is int and i >= 0 for i in v)
    require(type(intent['lineage']) is dict and set(intent['lineage']) ==
            {'registry-result', 'image-result', 'probe-result', 'runtime-dependencies-result'} and
            all(digest(v) for v in intent['lineage'].values()) and type(intent['chain']) is dict and
            set(intent['chain']) == {'parent', 'evidence', 'root', 'avd_home', 'avd', 'locator'} and
            all(ident(v) for v in intent['chain'].values()) and ident(value['after_identity']) and
            all(digest(v) for v in (intent['locator_sha256'], intent['create_intent_sha256'],
                                   value['intent_sha256'], value['attempt_sha256'])), 'SDK_CONFIG_NOT_READY')
    guard(ctx, api, intent['lineage'], intent['chain'], intent['locator_sha256'])
    require(hashlib.sha256(api.raw_file(ctx['evidence'] / 'sdk-avd-create-intent.json')).hexdigest() ==
            intent['create_intent_sha256'], 'SDK_CONFIG_LINEAGE')
    require(not temporary.exists() and not temporary.is_symlink() and
            identity(ctx, config) == value['after_identity'], 'SDK_CONFIG_NOT_READY')
    intent_sha = hashlib.sha256(api.raw_file(ctx['evidence'] / 'sdk-avd-config-prepare-intent.json')).hexdigest()
    attempt = api.read(ctx, 'avd-config-prepare-attempt', ('owner', 'intent_sha256', 'attempted'))
    require(value['intent_sha256'] == attempt['intent_sha256'] == intent_sha and attempt['attempted'] is True and
            type(attempt['owner']['uid']) is int and hashlib.sha256(api.raw_file(
                ctx['evidence'] / 'sdk-avd-config-prepare-attempt.json')).hexdigest() == value['attempt_sha256'], 'SDK_CONFIG_NOT_READY')
    plan = intent['plan']
    keys = {'kind', 'before_raw_sha256', 'after_raw_sha256', 'before_bytes', 'after_bytes', 'before_map_sha256',
            'after_map_sha256', 'before_pairs_sha256', 'after_pairs_sha256', 'keys', 'unchanged_keys', 'changed_keys',
            'before_partition_bytes', 'after_partition_bytes', 'written', 'runtime_verified'}
    require(type(plan) is dict and set(plan) == keys and plan['kind'] == 'sdk-fixed-prelaunch-data-partition-v1' and
            plan['written'] is False and plan['runtime_verified'] is False and
            all(type(plan[k]) is int for k in ('before_bytes', 'after_bytes', 'keys', 'unchanged_keys', 'changed_keys',
                'before_partition_bytes', 'after_partition_bytes')) and
            [plan[k] for k in ('keys', 'unchanged_keys', 'changed_keys', 'before_partition_bytes', 'after_partition_bytes')] ==
            [137, 136, 1, 838860800, 6442450944], 'SDK_CONFIG_NOT_READY')
    require(0 < plan['before_bytes'] <= ini.MAX_BYTES and 0 < plan['after_bytes'] <= ini.MAX_BYTES and
            all(digest(plan[k]) for k in keys if k.endswith('_sha256')), 'SDK_CONFIG_NOT_READY')
    original = intent['original']
    require(type(original) is dict and set(original) == {'identity', 'bytes', 'sha256', 'mtime_ns'} and
            type(original['bytes']) is int and original['bytes'] == plan['before_bytes'] and
            ident(original['identity']) and type(original['mtime_ns']) is int and original['mtime_ns'] >= 0 and
            original['sha256'] == plan['before_raw_sha256'], 'SDK_CONFIG_NOT_READY')
    if data is None: data = api.raw_file(config)
    projection, fp = ini.project(ctx, data), ini.fingerprints(ctx, data)
    require(type(data) is bytes and len(data) == plan['after_bytes'] <= ini.MAX_BYTES and projection['available'] and
            projection['source_sha256'] == plan['after_raw_sha256'] and projection['canonical_sha256'] == plan['after_map_sha256'] and
            projection['key_count'] == 137 and all(projection[k] is True for k in
                ('image_literal_matches', 'abi_x86_64', 'tag_google_apis')) and fp['available'] and
            fp['pairs_sha256'] == plan['after_pairs_sha256'], 'SDK_CONFIG_NOT_READY')
    return value
