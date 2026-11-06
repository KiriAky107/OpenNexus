"""Pure signed-association report correlation, never original runtime admission.

Supplied reports/bytes cannot prove private handles, first exit observation,
separate stopped syscalls or capture chronology. No collector gate or IO exists.
"""
import hashlib
import json
import shlex

import android_release_sync_quota_records as common
import android_release_sync_quota_validation as quota

PHASE = 'associate-bootstrap-signed-original'
ENTRY = 'ReleaseSyncAssociationBootstrapRuntimeProbe'
PROFILE = 'signed-association-bootstrap-completion-stop-v1'
COMPLETION_PROFILE = 'signed-association-bootstrap-completion-only-v1'
COMPLETION_KEYS = ('schema','capture_scope','identity','collection_id','original_lifetime','driver','intent_read')
INTENT_PATH = 'no_backup/owned-sync-association-intent.json'
LINES = (
    b'OPENNEXUS_SIGNED_ASSOCIATION_BOOTSTRAP_UI association_confirmed_UI_observed=true original_intent_written=true raw_UI_recorded=false native_original_state_verified=false',
    b'OPENNEXUS_SIGNED_ASSOCIATION_BOOTSTRAP_PHASE_OK associate-bootstrap-signed-original',
)
MAX = 2**63-1
LIMIT = 32768


def need(value):
    if not value:
        raise ValueError('SIGNED_ASSOCIATION_REPORT_CORRELATION_UNAVAILABLE')


def closed(value, keys):
    need(quota._closed(value, keys))


def exact(value, expected):
    need(quota._same(value, expected))


def number(value, maximum=MAX):
    need(type(value) is int and 0 <= value <= maximum)
    return value


def _known_ids(scope):
    return {v for v in scope.values() if type(v) is str and quota._uuid(v)}


def _fresh(identifier, used):
    need(quota._uuid(identifier) and identifier not in used)
    used.add(identifier)
    return identifier


def association_launch_scope(value):
    """Known pre-association identities only; no placeholders for GUI IDs."""
    operations = ('login_operation_id','setup_operation_id','save_operation_id')
    closed(value, ('schema','owner_run','avd','boot_id','api','uid','product','sync',
                   'remote_vault','android_device','setup_device',*operations))
    need(quota._pattern(value['owner_run'], r'[1-9][0-9]{0,17}-[1-9][0-9]{0,5}')
         and quota._same(value['schema'], 1) and quota._same(value['api'], 36)
         and quota._same(value['avd'], 'opennexus-ci-'+value['owner_run'])
         and quota._same(value['product'], quota.PRODUCT) and quota._same(value['sync'], quota.SYNC)
         and type(value['uid']) is int and 10000 <= value['uid'] <= 99999)
    need(all(quota._uuid(value[k]) for k in ('boot_id',*operations))
         and len({value[k] for k in ('boot_id',*operations)}) == len(operations)+1
         and all(quota._pattern(value[k], r'[0-9a-f]{32}') for k in ('remote_vault','android_device','setup_device'))
         and value['android_device'] != value['setup_device'])
    return dict(value)


def bounded_json(raw, bound):
    need(type(raw) is bytes and 0 < len(raw) <= bound)

    def pairs(items):
        value = dict(items)
        need(len(value) == len(items))
        return value

    def constant(_):
        need(False)

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('SIGNED_ASSOCIATION_REPORT_CORRELATION_UNAVAILABLE') from None


def _association_intent(raw, remote_vault):
    intent = bounded_json(raw, 2048)
    closed(intent, ('operation_id','preview_fingerprint','remote_vault'))
    need(quota._uuid(intent['operation_id']) and quota._pattern(intent['preview_fingerprint'], r'[0-9a-f]{64}'))
    exact(intent['remote_vault'], remote_vault)
    return intent


def capture_scope_from_intent(launch_scope, original_intent, *, collection_id, command_id):
    """Derive typed scope from supplied actual fields, without IO or hashing.

    The owning producer must separately bind driver completion, response and
    chronology. This pure derivation supplies no such original provenance.
    """
    base = association_launch_scope(launch_scope)
    used = _known_ids(base)
    _fresh(collection_id, used); _fresh(command_id, used)
    intent = _association_intent(original_intent, base['remote_vault'])
    _fresh(intent['operation_id'], used)
    return quota.bootstrap_scope(dict(base, association_operation_id=intent['operation_id'],
                                      preview_fingerprint=intent['preview_fingerprint']))


def approved_stdout(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 4096)
    # Permit the original Android instrumentation framing, not arbitrary text.
    lines = []
    for line in raw.splitlines():
        if not line:
            continue
        for prefix in (b'INSTRUMENTATION_STATUS: stream=', b'INSTRUMENTATION_RESULT: stream='):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        if line in (b'INSTRUMENTATION_STATUS_CODE: 1', b'INSTRUMENTATION_CODE: -1', b'INSTRUMENTATION_CODE: 0'):
            continue
        lines.append(line)
    need(lines == list(LINES))


def owner(value, life, identity, *, at_command=False):
    keys = ('verified_monotonic_ms','original_lifetime','owned_boot','original_driver_completed',
            'same_original_foreground_PID_UID_boot','app_resumed','root_uid','native_cycle_acknowledged')
    closed(value, keys + (('observed_after_private_command_intent',) if at_command else ()))
    at = number(value['verified_monotonic_ms'])
    expected = dict(verified_monotonic_ms=at, original_lifetime=life, owned_boot=identity,
        original_driver_completed=True, same_original_foreground_PID_UID_boot=True,
        app_resumed=True, root_uid=0, native_cycle_acknowledged=False)
    if at_command:
        expected['observed_after_private_command_intent'] = True
    exact(value, expected)
    return at


def _completion_value(value, original_intent, original_stdout, original_stderr):
    """Close only supplied completion fields/bytes, with no future stop/hash."""
    closed(value, COMPLETION_KEYS)
    exact(value['schema'], 1)
    scope = quota.bootstrap_scope(value['capture_scope'])
    identity, life = value['identity'], value['original_lifetime']
    common.owned_identity(identity, scope)
    common.lifetime(life, scope)
    ids = _known_ids(scope)
    collection = _fresh(value['collection_id'], ids)
    driver = value['driver']
    closed(driver, ('phase','entry','command_id','arguments','timeout_ms','started_monotonic_ms',
        'deadline_monotonic_ms','first_exit_seen_monotonic_ms','streams_read_started_monotonic_ms',
        'streams_read_returned_monotonic_ms','instrument_result','final_owner'))
    command_id = _fresh(driver['command_id'], ids)
    start = number(driver['started_monotonic_ms'], MAX-180000)
    deadline = start+180000
    exited = number(driver['first_exit_seen_monotonic_ms'])
    stream_start = number(driver['streams_read_started_monotonic_ms'])
    stream_end = number(driver['streams_read_returned_monotonic_ms'])
    final = owner(driver['final_owner'], life, identity)
    expected_driver = dict(phase=PHASE, entry=ENTRY, command_id=command_id,
        arguments=['shell','-T',shlex.join(('am','instrument','-w','-e','phase',PHASE,common.DRIVER+'/.'+ENTRY))],
        timeout_ms=180000, started_monotonic_ms=start, deadline_monotonic_ms=deadline,
        first_exit_seen_monotonic_ms=exited, streams_read_started_monotonic_ms=stream_start,
        streams_read_returned_monotonic_ms=stream_end,
        instrument_result=dict(returncode=0, unknown_result=False, automatic_retry=False,
            early_cleanup_not_success=False, no_final_product_force_stop_or_reopen=True),
        final_owner=driver['final_owner'])
    exact(driver, expected_driver)

    read = value['intent_read']
    closed(read, ('arguments','bound_bytes','timeout_ms','read_count','returncode','unknown_result',
        'automatic_retry','stderr_bytes','unvalidated_streams_exported','started_monotonic_ms','returned_monotonic_ms'))
    read_start = number(read['started_monotonic_ms'], MAX-1000)
    read_end = number(read['returned_monotonic_ms'])
    exact(read, dict(arguments=['shell','-T','run-as '+common.DRIVER+' sh -c '+shlex.quote(
        'test -f '+INTENT_PATH+' && test ! -L '+INTENT_PATH+' && cat '+INTENT_PATH)],
        bound_bytes=2048, timeout_ms=1000, read_count=1, returncode=0, unknown_result=False,
        automatic_retry=False, stderr_bytes=0, unvalidated_streams_exported=False,
        started_monotonic_ms=read_start, returned_monotonic_ms=read_end))
    need(start <= exited <= stream_start <= stream_end <= read_start <= read_end <= final <= deadline
         and read_end <= read_start+1000)
    intent = _association_intent(original_intent, scope['remote_vault'])
    expected_intent = dict(operation_id=scope['association_operation_id'],
        preview_fingerprint=scope['preview_fingerprint'], remote_vault=scope['remote_vault'])
    exact(intent, expected_intent)
    approved_stdout(original_stdout)
    need(type(original_stderr) is bytes and original_stderr == b'')
    need(len(common.encoded(value)) <= LIMIT and all(len(common.encoded(v)) <= 4096 for v in value.values()))
    return dict(scope=scope, identity=identity, life=life, ids=ids, collection=collection,
                command_id=command_id, intent=expected_intent, final=final)


def validate_association_completion(value, original_intent, original_stdout, original_stderr):
    """Explicit detached completion-only report, never a stop/runtime gate.

    No cryptographic hash occurs, including success. Original bytes are not
    returned; typed intent metadata is not a private file hash or readback ACK.
    """
    context = _completion_value(value, original_intent, original_stdout, original_stderr)
    result = dict(schema=1, profile=COMPLETION_PROFILE, completion=value, association_intent=context['intent'],
        completion_only=True, completed_driver_reported=True, stop_reported=False,
        original_managed_process_independently_verified=False, raw_intent_reader_independently_verified=False,
        original_private_files_independently_verified=False, actual_separate_syscalls_verified=False,
        collector_bracketing_samples_verified=False, capture_before_quota_chronology_verified=False,
        Native_or_client_ACK=False, runtime_verified=False)
    raw = common.encoded(result)
    need(len(raw) <= LIMIT)
    return json.loads(raw)


def validate_association_reports(value, original_intent, original_stdout, original_stderr):
    """Exact full report: completion AND stop close before any hashing.

    True reported facts do not implement OriginalQuotaBootstrapAdmission or
    independently attest actions. Times are host-domain, not Android elapsed.
    """
    closed(value, (*COMPLETION_KEYS,'stop'))
    completion = {k:v for k,v in value.items() if k != 'stop'}
    context = _completion_value(completion, original_intent, original_stdout, original_stderr)
    scope, identity, life = (context[k] for k in ('scope','identity','life'))
    collection, command_id, ids = (context[k] for k in ('collection','command_id','ids'))
    final, expected_intent = context['final'], context['intent']

    stop = value['stop']
    closed(stop, ('schema','phase','driver_command_id','stop_intent_id','original_lifetime','owned_boot',
        'intent','intent_written_monotonic_ms','before_command_owner','at_command_owner','result','command_clock','observations'))
    stop_id = _fresh(stop['stop_intent_id'], ids)
    before = owner(stop['before_command_owner'], life, identity)
    at = owner(stop['at_command_owner'], life, identity, at_command=True)
    written = number(stop['intent_written_monotonic_ms'])
    clock = stop['command_clock']
    closed(clock, ('started_monotonic_ms','returned_monotonic_ms','observation_deadline_monotonic_ms'))
    stop_start = number(clock['started_monotonic_ms'], MAX-5000)
    returned = number(clock['returned_monotonic_ms'])
    exact(clock, dict(started_monotonic_ms=stop_start, returned_monotonic_ms=returned,
                      observation_deadline_monotonic_ms=stop_start+5000))
    need(final <= before <= written <= at <= stop_start <= returned <= stop_start+2000)
    common.empty_result(stop['result'], invoked=True)
    samples = stop['observations']
    need(type(samples) is list and len(samples) == 2)
    sample_ids, previous = [], returned
    for index, sample in enumerate(samples, 1):
        closed(sample, ('schema','sample_index','observation_id','stop_intent_id','package','original_pid','product_uid',
            'owned_boot','root_uid','sample_start_monotonic_ms','sample_end_monotonic_ms','original_pid_absent',
            'all_owned_package_processes_absent','user0_force_stopped','original_driver_completed',
            'raw_process_list_exported','native_cycle_acknowledged'))
        observation_id = _fresh(sample['observation_id'], ids)
        sample_ids.append(observation_id)
        begin, end = number(sample['sample_start_monotonic_ms']), number(sample['sample_end_monotonic_ms'])
        need(previous <= begin <= end <= stop_start+5000)
        previous = end
        exact(sample, dict(schema=1, sample_index=index, observation_id=observation_id, stop_intent_id=stop_id,
            package=common.APP, original_pid=life['pid'], product_uid=life['uid'], owned_boot=identity, root_uid=0,
            sample_start_monotonic_ms=begin, sample_end_monotonic_ms=end, original_pid_absent=True,
            all_owned_package_processes_absent=True, user0_force_stopped=True, original_driver_completed=True,
            raw_process_list_exported=False, native_cycle_acknowledged=False))
    exact(stop, dict(schema=1, phase=PHASE, driver_command_id=command_id, stop_intent_id=stop_id,
        original_lifetime=life, owned_boot=identity, intent=dict(arguments=['shell','-T',
            shlex.join(('am','force-stop','--user','0',common.APP))], original_driver_completed=True,
            snapshot_stop_only=True, automatic_retry=False), intent_written_monotonic_ms=written,
        before_command_owner=stop['before_command_owner'], at_command_owner=stop['at_command_owner'],
        result=stop['result'], command_clock=clock, observations=samples))
    # All retained objects are now closed. No unknown string/object is hashed,
    # stringified, repaired or emitted on any earlier refusal path.
    encoded = common.encoded(value)
    need(len(encoded) <= LIMIT and all(len(common.encoded(v)) <= 4096 for v in value.values()))
    result = dict(schema=1, profile=PROFILE, capture_scope=scope, identity=identity, collection_id=collection,
        driver_command_id=command_id, stop_intent_id=stop_id, observation_ids=sample_ids,
        association_intent_raw_sha256=hashlib.sha256(original_intent).hexdigest(),
        association_intent_metadata_canonical_sha256=common.digest(expected_intent),
        stdout_raw_sha256=hashlib.sha256(original_stdout).hexdigest(),
        stderr_raw_sha256=hashlib.sha256(original_stderr).hexdigest(),
        completion_metadata_canonical_sha256=common.digest(completion), stop_metadata_canonical_sha256=common.digest(stop),
        completed_driver_reported=True, once_stop_reported=True, two_distinct_stopped_samples_reported=True,
        original_managed_process_independently_verified=False, raw_intent_reader_independently_verified=False,
        original_private_files_independently_verified=False, actual_separate_syscalls_verified=False,
        collector_bracketing_samples_verified=False, capture_before_quota_chronology_verified=False,
        Native_or_client_ACK=False, runtime_verified=False)
    raw = common.encoded(result)
    need(len(raw) <= LIMIT)
    return json.loads(raw)


def parse_association_reports(raw, original_intent, original_stdout, original_stderr):
    """Strict bounded JSON transport; its digest is not a private file hash."""
    return validate_association_reports(bounded_json(raw, LIMIT), original_intent, original_stdout, original_stderr)
