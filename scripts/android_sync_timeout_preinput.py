"""Pure closed schema2 projection; never reads a device or replaces live ready gates."""
import hashlib
import json
import re

POLICY = 'timeout-preinput-current-public-region-v1'
PHASE = 'cycle-timeout-original'
APP = 'cc.kronecker.opennexus'
MAX = 2**63 - 1
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
SCOPE = {'schema', 'policy_id', 'phase', 'parent_intent_id', 'input_intent_id', 'package', 'uid', 'window',
         'started_uptime_ms', 'raw_UI_or_section_geometry_recorded', 'automatic_retry'}


def _keys(value, keys):
    return type(value) is dict and all(type(key) is str for key in value) and set(value) == keys


def _int(value, low, high):
    return type(value) is int and low <= value <= high


def _uuid(value):
    return type(value) is str and re.fullmatch(UUID, value) is not None


def _hash(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _canonical(value):
    # Only closed, type-checked builtins constructed below reach the encoder.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def canonical_parent(value):
    if not (_keys(value, {'schema', 'phase', 'parent_intent_id', 'input_intent_id', 'native_invocation_id_observed'})
            and type(value['schema']) is int and value['schema'] == 1 and type(value['phase']) is str and value['phase'] == PHASE
            and _uuid(value['parent_intent_id']) and _uuid(value['input_intent_id'])
            and value['parent_intent_id'] != value['input_intent_id'] and value['native_invocation_id_observed'] is False):
        return None
    return _canonical(value)


def canonical_ready(value, parent):
    if canonical_parent(parent) is None:
        return None
    keys = {'schema', 'phase', 'action', 'parent_intent_id', 'driver_intent_id', 'package', 'uid', 'window',
            'original', 'visible', 'x', 'y', 'elapsed_ms'}
    if not _keys(value, keys):
        return None
    fixed = dict(schema=1, phase=PHASE, action='cycle-start', parent_intent_id=parent['parent_intent_id'],
                 driver_intent_id=parent['input_intent_id'], package=APP)
    if not all(type(value[k]) is type(v) and value[k] == v for k, v in fixed.items()):
        return None
    if not (_int(value['uid'], 10000, 99999) and _int(value['window'], 0, 65535)
            and _int(value['elapsed_ms'], 0, MAX - 36000) and type(value['x']) is int and type(value['y']) is int):
        return None
    for key in ('original', 'visible'):
        r = value[key]
        if not (type(r) is list and len(r) == 4 and all(_int(n, -16384, 16384) for n in r)
                and r[0] <= value['x'] < r[2] and r[1] <= value['y'] < r[3]):
            return None
    a, b = value['original'], value['visible']
    if not (a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]
            and value['x'] == (b[0] + b[2]) // 2 >= 0 and value['y'] == (b[1] + b[3]) // 2 >= 0):
        return None
    return _canonical(value)


def _scope(parent, uid, window, start):
    return dict(schema=1, policy_id=POLICY, phase=PHASE, parent_intent_id=parent['parent_intent_id'],
                input_intent_id=parent['input_intent_id'], package=APP, uid=uid, window=window,
                started_uptime_ms=start, raw_UI_or_section_geometry_recorded=False, automatic_retry=False)


def _written(value, expected):
    if not (_keys(value, {'metadata', 'raw_sha256', 'writer_returned'}) and value['writer_returned'] is True
            and _hash(value['raw_sha256']) and _keys(value['metadata'], set(expected))):
        return False
    metadata = value['metadata']
    # Validate all scalar types before any comparison or cryptographic hashing.
    if not all(type(metadata[k]) is type(v) and metadata[k] == v for k, v in expected.items()):
        return False
    return value['raw_sha256'] == hashlib.sha256(_canonical(expected)).hexdigest()


def validate_wire(value, parent, uid):
    """Return an owned typed copy or None. Caller still needs the original live gates."""
    parent_bytes = canonical_parent(parent)
    if parent_bytes is None or not _int(uid, 10000, 99999):
        return None
    if not (_keys(value, {'schema', 'original_ready', 'preinput_current_fit'})
            and type(value['schema']) is int and value['schema'] == 2):
        return None
    ready = value['original_ready']
    ready_bytes = canonical_ready(ready, parent)
    if ready_bytes is None or ready['uid'] != uid or ready['original'] != ready['visible'] or min(ready['original'][:2]) < 0:
        return None
    envelope = value['preinput_current_fit']
    extra = {'parent_metadata_canonical_sha256', 'ready_metadata_canonical_sha256', 'final_sample_uptime_ms',
             'intent_written', 'attempt_written', 'reveal_returned', 'preparation_budget_ms', 'stable_policy_ms',
             'min_samples_policy', 'section_reveal_limit', 'current_pair_stable_reported',
             'final_original_button_hit_and_pair_checked_before_ready', 'current_geometry_only',
             'future_warning_visibility_guaranteed', 'network_admission', 'native_timeout_verified',
             'stable_geometry_duration_independently_recomputed'}
    if not _keys(envelope, SCOPE | extra):
        return None
    start, final = envelope['started_uptime_ms'], envelope['final_sample_uptime_ms']
    if not (_int(start, 0, MAX - 5000) and _int(final, start + 600, start + 5000)):
        return None
    expected = _scope(parent, uid, ready['window'], start)
    expected.update(parent_metadata_canonical_sha256=hashlib.sha256(parent_bytes).hexdigest(),
                    ready_metadata_canonical_sha256=hashlib.sha256(ready_bytes).hexdigest(),
                    final_sample_uptime_ms=final, preparation_budget_ms=5000, stable_policy_ms=600,
                    min_samples_policy=3, section_reveal_limit=1, current_pair_stable_reported=True,
                    final_original_button_hit_and_pair_checked_before_ready=True, current_geometry_only=True,
                    future_warning_visibility_guaranteed=False, network_admission=False,
                    native_timeout_verified=False, stable_geometry_duration_independently_recomputed=False)
    if not all(type(envelope[k]) is type(v) and envelope[k] == v for k, v in expected.items()):
        return None
    intent = _scope(parent, uid, ready['window'], start)
    intent.update(preinput_only=True, section_reveal_limit=1, native_timeout_verified=False)
    if not _written(envelope['intent_written'], intent):
        return None
    attempted = envelope['attempt_written']
    if attempted is None:
        if envelope['reveal_returned'] is not None:
            return None
    else:
        if envelope['reveal_returned'] is not True:
            return None
        attempt = _scope(parent, uid, ready['window'], start)
        attempt.update(attempt=1, action='SHOW_ON_SCREEN', preinput_only=True,
                       intent_raw_sha256=envelope['intent_written']['raw_sha256'])
        if not _written(attempted, attempt):
            return None
    # Reconstruct only approved scalars and nested metadata; no caller alias.
    expected['intent_written'] = dict(metadata=intent, raw_sha256=envelope['intent_written']['raw_sha256'], writer_returned=True)
    if attempted is None:
        expected['attempt_written'] = None
    else:
        expected['attempt_written'] = dict(metadata=attempt, raw_sha256=attempted['raw_sha256'], writer_returned=True)
    expected['reveal_returned'] = envelope['reveal_returned']
    projected = dict(schema=2, original_ready=json.loads(ready_bytes), preinput_current_fit=expected)
    return projected if len(_canonical(projected)) <= 4096 else None


def decode_wire(raw, parent, uid):
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        return None

    def pairs(items):
        out = dict(items)
        if len(out) != len(items):
            raise ValueError('Duplicate closed metadata')
        return out

    def constant(_):
        raise ValueError('Noninteger closed metadata')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return validate_wire(value, parent, uid)
