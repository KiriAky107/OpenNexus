"""Pure, bounded SDK INI diagnostic projection; never an ownership or admission gate."""
import hashlib
import json
import re

MAX_BYTES = 65536
MAX_LINES = 1024
MAX_KEYS = 512
KIND = 'strict-sdk-ini-full-map-v1'


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
