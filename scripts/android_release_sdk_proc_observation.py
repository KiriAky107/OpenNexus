"""Closed optional cleanup facts from existing memory; no IO or admission."""
import hashlib
import json
import re

import android_release_sdk_proc_facts as facts

NAME = 'cleanup-proc-failure'
KEYS = ('schema', 'owner', 'stage', 'guard_code', 'launch_record_raw_sha256',
        'previous_launch_identity', 'cleanup_result_canonical_sha256', 'read_failure',
        'failure_only', 'diagnostic_only', 'automatic_retry', 'whole_cleanup_passed',
        'current_process_identity_verified', 'process_absence_verified', 'runtime_verified')


class Observer:
    __slots__ = ('fact',)

    def __init__(self):
        self.fact = None


def _keys(value, keys):
    return type(value) is dict and all(type(key) is str for key in value) and set(value) == set(keys)


def _same(value, expected):
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return _keys(value, expected) and all(_same(value[key], item) for key, item in expected.items())
    return value == expected


def _text(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def _owner(value):
    return (_keys(value, ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'uid', 'host_boot_id')) and
            _text(value['GITHUB_RUN_ID'], r'[1-9][0-9]{0,19}') and
            _text(value['GITHUB_RUN_ATTEMPT'], r'[1-9][0-9]{0,3}') and
            _text(value['GITHUB_SHA'], r'[0-9a-f]{40}') and type(value['uid']) is int and 0 <= value['uid'] < 2**31 and
            _text(value['host_boot_id'], r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}'))


def project(observation, owner, previous, launch_sha, result):
    if type(observation) is not Observer or not _owner(owner):
        return None
    read_failure = facts.project(observation.fact)
    if read_failure is None or not facts.validate(read_failure):
        return None
    if not (_keys(previous, ('owner', 'pid', 'uid', 'start_ticks', 'runtime_verified')) and
            _same(previous['owner'], owner) and type(previous['pid']) is int and 1 <= previous['pid'] <= 4194304 and
            type(previous['uid']) is int and previous['uid'] == owner['uid'] and
            type(previous['start_ticks']) is int and 0 < previous['start_ticks'] < 2**63 and
            previous['runtime_verified'] is False and _text(launch_sha, r'[0-9a-f]{64}')):
        return None
    expected = dict(owner=owner, passed=False, no_stop_or_kill=True, runtime_verified=False,
                    guard_code='SDK_PROC_UNKNOWN', failure_stage='original-pid',
                    error_kind='sequence_guard', SDK_removed_here=False)
    if not _same(result, expected):
        return None
    # This hash binds the complete typed result in memory, not a reread of its file.
    canonical = json.dumps(result, sort_keys=True, separators=(',', ':')).encode('ascii')
    return dict(schema=1, owner=dict(owner), stage='original-pid', guard_code='SDK_PROC_UNKNOWN',
                launch_record_raw_sha256=launch_sha,
                previous_launch_identity={key: previous[key] for key in ('pid', 'uid', 'start_ticks', 'runtime_verified')},
                cleanup_result_canonical_sha256=hashlib.sha256(canonical).hexdigest(), read_failure=read_failure,
                failure_only=True, diagnostic_only=True, automatic_retry=False, whole_cleanup_passed=False,
                current_process_identity_verified=False, process_absence_verified=False, runtime_verified=False)


def validate(value, owner, previous, launch_sha, result):
    if not (_keys(value, KEYS) and facts.validate(value['read_failure'])):
        return False
    observation = Observer()
    observation.fact = facts.capture(value['read_failure']['read_step'], True, True)
    expected = project(observation, owner, previous, launch_sha, result)
    return expected is not None and _same(value, expected)
