"""Pure preparation for one fixed, newly created API36 AVD; no file or SDK actions.

The caller must establish ownership and record the original bytes before writing.
This module does not admit a changed runtime config or replace the raw-hash guard.
"""
import hashlib

import android_release_sdk_ini as ini

KEY = 'disk.dataPartition.size'
BEFORE = '800M'
AFTER = '6442450944'
KEYS = 137


class ConfigError(RuntimeError):
    pass


def prepare(ctx, data):
    """Return new bytes and a narrow unwritten result; refuse every other input shape."""
    projection = ini.project(ctx, data)
    if not (projection['available'] and projection['key_count'] == KEYS and
            all(projection[k] is True for k in ('image_literal_matches', 'abi_x86_64', 'tag_google_apis'))):
        raise ConfigError('SDK_CONFIG_PREPARE_INPUT')
    # The fixed vendor-created original has one literal assignment per line.
    # Do not guess how to preserve unknown comments, blanks or alternative syntax.
    if not data.endswith(b'\n'):
        raise ConfigError('SDK_CONFIG_PREPARE_SHAPE')
    lines = data.decode('ascii').split('\n')[:-1]
    if len(lines) != KEYS:
        raise ConfigError('SDK_CONFIG_PREPARE_SHAPE')
    pairs = []
    for line in lines:
        if '=' not in line:
            raise ConfigError('SDK_CONFIG_PREPARE_SHAPE')
        key, value = line.split('=', 1)
        if key != key.strip(' \t\r') or value != value.strip(' \t\r'):
            raise ConfigError('SDK_CONFIG_PREPARE_SHAPE')
        pairs.append((key, value))
    if dict(pairs).get(KEY) != BEFORE:
        raise ConfigError('SDK_CONFIG_PREPARE_PARTITION')
    output = ''.join(key + ' = ' + (AFTER if key == KEY else value) + '\n'
                     for key, value in pairs).encode('ascii')
    current = ini.project(ctx, output)
    old_fp, new_fp = ini.fingerprints(ctx, data), ini.fingerprints(ctx, output)
    delta = ini.fingerprint_delta(old_fp, new_fp)
    expected = dict(key_sha256=hashlib.sha256(b'SDK_INI_KEY\0' + KEY.encode()).hexdigest(),
                    before_value_sha256=hashlib.sha256(b'SDK_INI_VALUE\0' + KEY.encode() + b'\0' + BEFORE.encode()).hexdigest(),
                    after_value_sha256=hashlib.sha256(b'SDK_INI_VALUE\0' + KEY.encode() + b'\0' + AFTER.encode()).hexdigest())
    if not (current['available'] and delta['available'] and delta['before_keys'] == delta['after_keys'] == KEYS and
            delta['unchanged_keys'] == KEYS - 1 and delta['added_key_sha256'] == delta['removed_key_sha256'] == [] and
            delta['changed'] == [expected] and
            all(current[k] is True for k in ('image_literal_matches', 'abi_x86_64', 'tag_google_apis'))):
        raise ConfigError('SDK_CONFIG_PREPARE_RESULT')
    result = dict(kind='sdk-fixed-prelaunch-data-partition-v1',
                  before_raw_sha256=projection['source_sha256'], after_raw_sha256=current['source_sha256'],
                  before_bytes=projection['source_bytes'], after_bytes=current['source_bytes'],
                  before_map_sha256=projection['canonical_sha256'], after_map_sha256=current['canonical_sha256'],
                  before_pairs_sha256=old_fp['pairs_sha256'], after_pairs_sha256=new_fp['pairs_sha256'],
                  keys=KEYS, unchanged_keys=KEYS - 1, changed_keys=1,
                  before_partition_bytes=800 * 1024 ** 2, after_partition_bytes=6 * 1024 ** 3,
                  written=False, runtime_verified=False)
    return output, result
