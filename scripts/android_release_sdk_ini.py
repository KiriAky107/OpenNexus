"""Pure, bounded SDK INI diagnostic projection; never an ownership or admission gate."""
import hashlib
import json
import re

MAX_BYTES = 65536
MAX_LINES = 1024
MAX_KEYS = 512
KIND = 'strict-sdk-ini-full-map-v1'
FP_KIND = 'sdk-ini-key-value-sha256-v1'
FP_MAX_KEYS = 256


def project(ctx, data):
    """Hash every semantic key/value, retaining no config text, keys, values or paths."""
    value = dict(kind=KIND, source_bytes=None, source_sha256=None, available=False,
                 canonical_sha256=None, key_count=None, image_literal_matches=None,
                 abi_x86_64=None, tag_google_apis=None, unavailable_reason=None,
                 runtime_verified=False)
    def unavailable(reason):
        value['unavailable_reason'] = reason
        return value
    if type(data) is not bytes: return unavailable('input_type')
    if len(data) > MAX_BYTES: return unavailable('input_limit')
    value.update(source_bytes=len(data), source_sha256=hashlib.sha256(data).hexdigest())
    if any(byte > 127 for byte in data): return unavailable('non_ascii')
    if any(byte not in (9, 10, 13) and not 32 <= byte <= 126 for byte in data):
        return unavailable('control_byte')
    lines = data.decode('ascii').split('\n')
    if len(lines) > MAX_LINES: return unavailable('line_limit')
    values = {}
    for line in lines:
        line = line.strip(' \t\r')
        if not line or line.startswith(('#', ';')): continue
        if '=' not in line: return unavailable('assignment')
        key, literal = line.split('=', 1)
        key = key.rstrip(' \t\r'); literal = literal.strip(' \t\r')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', key): return unavailable('key')
        if '\r' in literal: return unavailable('value_remainder')
        # The vendor overwrites duplicate keys or skips malformed lines. Diagnostics
        # deliberately become unavailable instead of discarding ambiguous input.
        if key in values: return unavailable('duplicate')
        values[key] = literal
        if len(values) > MAX_KEYS: return unavailable('key_limit')
    if not values: return unavailable('empty')
    encoded = json.dumps(sorted(values.items()), ensure_ascii=True, separators=(',', ':')).encode('ascii')
    relative = 'system-images/android-36/google_apis/x86_64'
    private = str(ctx['root']).replace('\\', '/') + '/runtime/' + relative
    value.update(available=True, canonical_sha256=hashlib.sha256(encoded).hexdigest(), key_count=len(values),
                 image_literal_matches=values.get('image.sysdir.1') in (relative, relative + '/', private, private + '/'),
                 abi_x86_64=values.get('abi.type') == 'x86_64', tag_google_apis=values.get('tag.id') == 'google_apis')
    return value


def fingerprints(ctx, data):
    """Opaque fingerprints for every parsed key; no raw key/value leaves memory."""
    projection = project(ctx, data)
    value = {key: projection[key] for key in ('source_bytes', 'source_sha256', 'canonical_sha256', 'key_count')}
    value.update(kind=FP_KIND, available=False, pairs=None, pairs_sha256=None,
                 unavailable_reason='projection_unavailable', runtime_verified=False)
    if not projection['available']: return value
    if projection['key_count'] > FP_MAX_KEYS:
        value['unavailable_reason'] = 'key_limit'; return value
    pairs = []
    for line in data.decode('ascii').split('\n'):
        line = line.strip(' \t\r')
        if not line or line.startswith(('#', ';')): continue
        key, literal = line.split('=', 1)
        key = key.rstrip(' \t\r'); literal = literal.strip(' \t\r')
        key_bytes = key.encode('ascii')
        pairs.append([hashlib.sha256(b'SDK_INI_KEY\0' + key_bytes).hexdigest(),
                      hashlib.sha256(b'SDK_INI_VALUE\0' + key_bytes + b'\0' + literal.encode('ascii')).hexdigest()])
    pairs.sort()
    if len(pairs) != projection['key_count'] or len({row[0] for row in pairs}) != len(pairs):
        value['unavailable_reason'] = 'key_hash_collision'; return value
    digest = hashlib.sha256(json.dumps(pairs, separators=(',', ':')).encode('ascii')).hexdigest()
    value.update(available=True, pairs=pairs, pairs_sha256=digest, unavailable_reason=None)
    return value


def fingerprints_valid(value):
    fields = {'kind', 'available', 'source_bytes', 'source_sha256', 'canonical_sha256', 'key_count',
              'pairs', 'pairs_sha256', 'unavailable_reason', 'runtime_verified'}
    def digest(v): return type(v) is str and re.fullmatch(r'[0-9a-f]{64}', v) is not None
    if not (type(value) is dict and set(value) == fields and value['kind'] == FP_KIND and
            type(value['available']) is bool and value['runtime_verified'] is False and
            type(value['source_bytes']) is int and 0 <= value['source_bytes'] <= MAX_BYTES and digest(value['source_sha256'])):
        return False
    if not value['available']:
        if value['pairs'] is not None or value['pairs_sha256'] is not None: return False
        if value['unavailable_reason'] == 'projection_unavailable':
            return value['canonical_sha256'] is None and value['key_count'] is None
        return (value['unavailable_reason'] in ('key_limit', 'key_hash_collision') and digest(value['canonical_sha256']) and
                type(value['key_count']) is int and 1 <= value['key_count'] <= MAX_KEYS and
                (value['unavailable_reason'] != 'key_limit' or value['key_count'] > FP_MAX_KEYS))
    rows = value['pairs']
    return (digest(value['canonical_sha256']) and type(value['key_count']) is int and
            1 <= value['key_count'] <= FP_MAX_KEYS and type(rows) is list and len(rows) == value['key_count'] and
            all(type(row) is list and len(row) == 2 and all(digest(v) for v in row) for row in rows) and
            rows == sorted(rows) and len({row[0] for row in rows}) == len(rows) and digest(value['pairs_sha256']) and
            value['pairs_sha256'] == hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode('ascii')).hexdigest() and
            value['unavailable_reason'] is None)


def fingerprint_delta(before, after):
    result = dict(kind='sdk-ini-key-delta-v1', available=False, before_pairs_sha256=None,
                  after_pairs_sha256=None, before_keys=None, after_keys=None, unchanged_keys=None,
                  added_key_sha256=None, removed_key_sha256=None, changed=None, runtime_verified=False)
    if not (fingerprints_valid(before) and fingerprints_valid(after) and before['available'] and after['available']):
        return result
    old, new = dict(before['pairs']), dict(after['pairs'])
    changed = [dict(key_sha256=key, before_value_sha256=old[key], after_value_sha256=new[key])
               for key in sorted(old.keys() & new.keys()) if old[key] != new[key]]
    result.update(available=True, before_pairs_sha256=before['pairs_sha256'], after_pairs_sha256=after['pairs_sha256'],
                  before_keys=len(old), after_keys=len(new), unchanged_keys=len(old.keys() & new.keys()) - len(changed),
                  added_key_sha256=sorted(new.keys() - old.keys()), removed_key_sha256=sorted(old.keys() - new.keys()),
                  changed=changed)
    return result
