"""Pure correlation for the new signed-quota original-record protocol.

No live driver, file reader, OriginalQuotaAdmission implementation, clock or
action is supplied. Closed caller records remain reports. Canonical metadata
hashes do not attest private file bytes, separate syscalls, Native cause or ACK.
"""
import hashlib
import json
import shlex

import android_release_sync_quota_validation as quota

APP = 'cc.kronecker.opennexus'
DRIVER = APP + '.testdriver'
PHASE = 'cycle-quota-signed-original'
ENTRY = 'ReleaseSyncQuotaRuntimeProbe'
LABEL = 'signed-quota-original'
RULE = 'complete-public-quota-alert-exact-v1'
MAX = 2**63 - 1
MAX_BUNDLE = 32768
EMPTY = hashlib.sha256(b'').hexdigest()
LINES = (
    b'OPENNEXUS_SIGNED_QUOTA_UI input_finished=true quota_error_UI_observed=true raw_UI_recorded=false native_original_state_verified=false',
    b'OPENNEXUS_SIGNED_QUOTA_PHASE_OK cycle-quota-signed-original',
)


def need(value):
    if not value:
        raise ValueError('SIGNED_QUOTA_ORIGINAL_RECORDS_UNAVAILABLE')


def exact(value, expected):
    need(quota._same(value, expected))


def closed(value, keys):
    need(quota._closed(value, keys))


def number(value, minimum=0, maximum=MAX):
    need(type(value) is int and minimum <= value <= maximum)
    return value


def until(start, duration):
    number(start, maximum=MAX-duration)
    return start + duration


def encoded(value):
    # Call only after the entire value has been closed and type-checked.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def owned_identity(value, scope):
    closed(value, ('avd', 'qemu', 'api', 'build_type', 'debuggable', 'boot_id'))
    need(type(value['build_type']) is str and value['build_type'] in ('eng', 'userdebug'))
    exact(value, dict(avd=scope['avd'], qemu='1', api='36', build_type=value['build_type'],
                      debuggable='1', boot_id=scope['boot_id']))


def lifetime(value, scope):
    closed(value, ('package', 'pid', 'uid', 'start_ticks'))
    number(value['pid'], 1, 2**31-1)
    number(value['start_ticks'], 1, 2**64-1)
    number(scope['uid'], 10000, 99999)
    exact(value, dict(package=APP, pid=value['pid'], uid=scope['uid'], start_ticks=value['start_ticks']))


def clock(value):
    closed(value, ('sample_start_monotonic_ms', 'sample_end_monotonic_ms', 'sampled_elapsed_ms'))
    start, end, elapsed = (number(value[k]) for k in
                          ('sample_start_monotonic_ms', 'sample_end_monotonic_ms', 'sampled_elapsed_ms'))
    need(start <= end <= until(start, 1000))
    return start, end, elapsed


def input_owner(value, life, *, at_command=False):
    keys = ('original_lifetime', 'same_original_foreground_PID_UID_boot', 'original_instrumentation_alive',
            'ready_age_ms', 'ready_age_bound_ms', 'native_cycle_acknowledged')
    closed(value, keys + (('observed_after_private_command_intent',) if at_command else ()))
    age = number(value['ready_age_ms'], maximum=2000)
    expected = dict(original_lifetime=life, same_original_foreground_PID_UID_boot=True,
                    original_instrumentation_alive=True, ready_age_ms=age, ready_age_bound_ms=2000,
                    native_cycle_acknowledged=False)
    if at_command:
        expected['observed_after_private_command_intent'] = True
    exact(value, expected)
    return age


def empty_result(value, *, invoked):
    expected = dict(returncode=0, automatic_retry=False, stdout_bytes=0, stdout_sha256=EMPTY,
                    stderr_bytes=0, stderr_sha256=EMPTY, unvalidated_streams_exported=False)
    if invoked:
        expected.update(unknown_result=False, device_command_invoked=True)
    exact(value, expected)


def signal(value, name):
    closed(value, ('intent', 'result', 'readback'))
    path = 'no_backup/' + name
    args = ['shell', '-T', 'run-as ' + DRIVER + ' sh -c ' +
            shlex.quote('umask 077; test ! -e '+path+' && test ! -L '+path+' && cat > '+path)]
    exact(value['intent'], dict(arguments=args, bytes=7, automatic_retry=False))
    empty_result(value['result'], invoked=False)
    exact(value['readback'], dict(exact_static_bytes=True, bytes=7, private_signal_bytes_exported=False))


def stdout(value):
    need(type(value) is bytes and 0 < len(value) <= 4096)
    lines = []
    for line in value.splitlines():
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


def validate_completion(records, original_stdout):
    keys = ('schema', 'scope', 'identity', 'phase', 'entry', 'parent', 'context', 'context_write',
            'context_ready_signal', 'ready', 'original_lifetime', 'input_owner', 'input_owner_at_command',
            'input_clock', 'command_clock', 'host_window', 'touch_intent', 'touch_result', 'post_input_owner',
            'input_finish_signal', 'failed_ready', 'failed_clock', 'instrument_result', 'instrument_streams', 'final_clock',
            'final_owner', 'phase_result')
    closed(records, keys)
    exact(records['schema'], 1); exact(records['phase'], PHASE); exact(records['entry'], ENTRY)
    scope = quota._scope(records['scope'])
    need(quota._pattern(scope['owner_run'], r'[1-9][0-9]{0,17}-[1-9][0-9]{0,5}'))
    number(scope['window'], 1, 65535)
    owned_identity(records['identity'], scope)
    life = records['original_lifetime']; lifetime(life, scope)
    parent = dict(schema=1, phase=PHASE, parent_intent_id=scope['parent_id'],
                  input_intent_id=scope['input_id'], native_invocation_id_observed=False)
    exact(records['parent'], parent)
    context = dict(schema=1, phase=PHASE, product=quota.PRODUCT, sync=quota.SYNC,
                   owner_run=scope['owner_run'], parent_id=scope['parent_id'], input_id=scope['input_id'],
                   remote_vault=scope['remote_vault'], device_id=scope['android_device'],
                   parent_metadata_canonical_sha256=digest(parent))
    exact(records['context'], context)
    need(len(encoded(context)) <= 2048)
    exact(records['context_write'], dict(phase=PHASE, context_file_sha256=digest(context),
          same_writer_buffer_reported=True, original_buffer_write_returned=True,
          automatic_retry=False, native_cycle_acknowledged=False))
    signal(records['context_ready_signal'], 'owned-sync-quota-context-'+scope['parent_id']+'.finish')

    ready = records['ready']
    closed(ready, ('schema','phase','action','parent_intent_id','driver_intent_id','package','uid',
                  'window','original','visible','x','y','elapsed_ms'))
    at = number(ready['elapsed_ms'], maximum=MAX-36000)
    x, y = number(ready['x'], maximum=16384), number(ready['y'], maximum=16384)
    for key in ('original', 'visible'):
        rect = ready[key]
        need(type(rect) is list and len(rect) == 4)
        for n in rect:
            number(n, -16384, 16384)
        need(rect[0] <= x < rect[2] and rect[1] <= y < rect[3])
    a, b = ready['original'], ready['visible']
    need(a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]
         and x == (b[0]+b[2])//2 and y == (b[1]+b[3])//2)
    exact(ready, dict(schema=1, phase=PHASE, action='cycle-start', parent_intent_id=scope['parent_id'],
          driver_intent_id=scope['input_id'], package=APP, uid=scope['uid'], window=scope['window'],
          original=a, visible=b, x=x, y=y, elapsed_ms=at))
    age1 = input_owner(records['input_owner'], life)
    age2 = input_owner(records['input_owner_at_command'], life, at_command=True)
    first, end1, elapsed1 = clock(records['input_clock'])
    start2, end2, elapsed2 = clock(records['command_clock'])
    need(end1 <= start2 and age1 <= age2 and elapsed1 == at+age1 and elapsed2 == at+age2)
    deadline = until(first, 42000)
    need(end2 <= deadline)
    exact(records['host_window'], dict(original_ready_elapsed_ms=at, preinput_monotonic_ms=first,
          host_completion_deadline_monotonic_ms=deadline, driver_failure_bound_ms=36000,
          product_client_bound_ms=30000, ready_age_bound_ms=2000, clock_reset_on_marker=False,
          no_fault_injection=True, native_timeout_verified=False))
    exact(records['touch_intent'], dict(arguments=['shell','-T',shlex.join(('input','touchscreen','tap',str(x),str(y)))],
          package=APP, automatic_retry=False))
    empty_result(records['touch_result'], invoked=True)
    exact(records['post_input_owner'], dict(input_command_returned=True, native_cycle_acknowledged=False,
          observation_unavailable=False, original_instrumentation_alive=True, same_original_PID=True,
          app_resumed=True, same_owned_boot_identity=True))
    signal(records['input_finish_signal'], 'owned-sync-quota-touch-'+scope['input_id']+'.finish')
    failed = records['failed_ready']
    closed(failed, ('schema','phase','parent_intent_id','input_intent_id','package','uid','window',
                   'elapsed_ms','scene_scan_elapsed_ms','scene_counts','public_alert_rule_id',
                   'failed_cycle_UI_observed','quota_error_UI_observed','raw_UI_recorded','native_original_state_verified'))
    failed_at = number(failed['elapsed_ms'])
    need(elapsed2 <= failed_at <= at+36000)
    exact(failed, dict(schema=1, phase=PHASE, parent_intent_id=scope['parent_id'], input_intent_id=scope['input_id'],
          package=APP, uid=scope['uid'], window=scope['window'], elapsed_ms=failed_at, scene_scan_elapsed_ms=failed_at,
          scene_counts=[1,1,1,1,0,0,0,0], public_alert_rule_id=RULE, failed_cycle_UI_observed=True,
          quota_error_UI_observed=True, raw_UI_recorded=False, native_original_state_verified=False))
    start3, end3, elapsed3 = clock(records['failed_clock'])
    need(end2 <= start3 <= end3 <= deadline and failed_at <= elapsed3 <= until(failed_at, 2000))
    exact(records['instrument_result'], dict(returncode=0, unknown_result=False, automatic_retry=False,
          early_cleanup_not_success=False, no_final_product_force_stop_or_reopen=True))
    final = records['final_clock']
    closed(final, ('driver_exit_seen_monotonic_ms', 'final_owner_verified_monotonic_ms'))
    exit_at, owner_at = (number(final[k]) for k in ('driver_exit_seen_monotonic_ms','final_owner_verified_monotonic_ms'))
    need(end3 <= exit_at <= owner_at <= deadline)
    exact(records['final_owner'], dict(original_lifetime=life, owned_boot=records['identity'],
          original_driver_completed=True, same_original_foreground_PID_UID_boot=True,
          app_resumed=True, root_uid=0, native_cycle_acknowledged=False))
    exact(records['phase_result'], dict(schema=1, phase=PHASE, label=LABEL, pid=life['pid'], passed=True,
          original_parent_intent_id=scope['parent_id'], original_input_intent_id=scope['input_id'],
          quota_error_UI_observed=True, native_original_state_verified=False, client_receipt_known=False))
    stdout(original_stdout)
    exact(records['instrument_streams'], dict(stdout_bytes=len(original_stdout),
          stdout_sha256=hashlib.sha256(original_stdout).hexdigest(), stderr_bytes=0,
          stderr_sha256=EMPTY, raw_private_streams_exported=False))
    need(all(len(encoded(v)) <= 4096 for v in records.values()) and len(encoded(records)) <= MAX_BUNDLE)
    return dict(schema=1, phase=PHASE, scope=dict(scope), original_metadata_canonical_sha256=digest(records),
                completed_driver_reported=True, full_quota_scene_reported=True,
                original_files_independently_verified=False, live_deadlines_independently_verified=False,
                Native_cause_verified=False, native_apply_ACK=False, runtime_verified=False)


def validate_snapshot_stop(records, original_stdout, stop):
    completion = validate_completion(records, original_stdout)
    keys = ('schema','phase','scope','stop_intent_id','completion_metadata_canonical_sha256',
            'original_lifetime','owned_boot','intent','before_command_owner','at_command_owner',
            'result','command_clock','observations')
    closed(stop, keys)
    scope, life, identity = records['scope'], records['original_lifetime'], records['identity']
    exact(stop['schema'], 1); exact(stop['phase'], PHASE); exact(stop['scope'], scope)
    exact(stop['original_lifetime'], life); exact(stop['owned_boot'], identity)
    exact(stop['completion_metadata_canonical_sha256'], completion['original_metadata_canonical_sha256'])
    stop_id = stop['stop_intent_id']; need(quota._uuid(stop_id))
    old_ids = {v for v in scope.values() if type(v) is str and quota._uuid(v)}
    need(stop_id not in old_ids)
    args = ['shell','-T',shlex.join(('am','force-stop','--user','0',APP))]
    exact(stop['intent'], dict(arguments=args, original_driver_completed=True, snapshot_stop_only=True, automatic_retry=False))
    owner = dict(original_lifetime=life, owned_boot=identity, original_driver_completed=True,
                 same_original_foreground_PID_UID_boot=True, app_resumed=True, root_uid=0, native_cycle_acknowledged=False)
    exact(stop['before_command_owner'], owner)
    exact(stop['at_command_owner'], dict(owner, observed_after_private_command_intent=True))
    empty_result(stop['result'], invoked=True)
    command = stop['command_clock']
    closed(command, ('started_monotonic_ms','returned_monotonic_ms','observation_deadline_monotonic_ms'))
    start, returned, deadline = (number(command[k]) for k in
                                ('started_monotonic_ms','returned_monotonic_ms','observation_deadline_monotonic_ms'))
    need(records['final_clock']['final_owner_verified_monotonic_ms'] <= start <= returned <= until(start, 2000)
         and deadline == until(start, 5000))
    samples = stop['observations']; need(type(samples) is list and len(samples) == 2)
    sample_ids, previous = set(), returned
    for index, sample in enumerate(samples, 1):
        closed(sample, ('schema','sample_index','observation_id','stop_intent_id','package','original_pid','product_uid',
                        'owned_boot','root_uid','sample_start_monotonic_ms','sample_end_monotonic_ms',
                        'original_pid_absent','all_owned_package_processes_absent','user0_force_stopped',
                        'original_driver_completed','raw_process_list_exported','native_cycle_acknowledged'))
        observation_id = sample['observation_id']
        need(quota._uuid(observation_id) and observation_id not in old_ids | sample_ids | {stop_id})
        sample_ids.add(observation_id)
        begin, end = number(sample['sample_start_monotonic_ms']), number(sample['sample_end_monotonic_ms'])
        need(previous <= begin <= end <= deadline); previous = end
        exact(sample, dict(schema=1, sample_index=index, observation_id=observation_id, stop_intent_id=stop_id,
              package=APP, original_pid=life['pid'], product_uid=life['uid'], owned_boot=identity, root_uid=0,
              sample_start_monotonic_ms=begin, sample_end_monotonic_ms=end, original_pid_absent=True,
              all_owned_package_processes_absent=True, user0_force_stopped=True, original_driver_completed=True,
              raw_process_list_exported=False, native_cycle_acknowledged=False))
    need(all(len(encoded(v)) <= 4096 for v in stop.values())
         and len(encoded(dict(schema=1, completion=records, stop=stop))) <= MAX_BUNDLE)
    return dict(completion, stop_intent_id=stop_id, stop_metadata_canonical_sha256=digest(stop),
                two_distinct_stopped_samples_reported=True, actual_separate_syscalls_verified=False,
                collector_bracketing_samples_verified=False)


def parse_original_records(raw, original_stdout, *, require_stop=False):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BUNDLE and type(require_stop) is bool)
    def pairs(items):
        result = dict(items)
        need(len(result) == len(items))
        return result
    def constant(_):
        need(False)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        closed(value, ('schema','completion','stop')); exact(value['schema'], 1)
        if value['stop'] is None:
            need(not require_stop)
            return validate_completion(value['completion'], original_stdout)
        return validate_snapshot_stop(value['completion'], original_stdout, value['stop'])
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('SIGNED_QUOTA_ORIGINAL_RECORDS_UNAVAILABLE') from None
