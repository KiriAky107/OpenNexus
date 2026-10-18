"""New owned stale-review and original native-completion interruption scope."""
import hashlib
import json
import re
import uuid

from android_auth_fixture import atomic_json
from android_desktop_handoff import canonical, require
from android_product_pair_android import canonical_uuid, hosted_scope, observe_device, observe_packages
from android_product_pair_clients import original_outcome_client
from android_runtime_probe import APP
from android_sync_conflict import COPY, DIRTY, IDS, LOCAL, REMOTE, validate_cold_original_read
from android_original_outcome_bridge_client import OriginalCompletionBridge
from android_original_outcome_driver_stop import stop_original_driver_once
from android_original_outcome_files import mutate_recorded_original_source, publish_original_driver_file
from android_original_outcome_instrumentation import OriginalOutcomeInstrumentation
from android_original_outcome_process import stop_original_once
from android_original_outcome_status import OUTCOME_STAGES


CONCURRENT = LOCAL + '明确保留的并发审核来源。\n'.encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def checkpoint(probe, value):
    require(value in OUTCOME_STAGES, 'OUTCOME_STAGE_REFUSED')
    probe.original_outcome_stage = value


def provider_original(provider):
    return {key: provider[key] for key in ('vault', 'rows', 'delivery')}


def check_frozen(value, nonce, expected_hash):
    require(type(value) is dict and set(value) == {'nonce', 'sequence', 'choice', 'destination', 'fingerprint',
                'expected_hash_from_parent_native_observation', 'expected_hash_displayed_by_dialog',
                'native_operation_ids_not_assigned_by_driver'}
            and value['nonce'] == nonce and value['sequence'] == 2 and value['choice'] == 'copy'
            and value['destination'] == COPY and type(value['fingerprint']) is str
            and re.fullmatch(r'[0-9a-f]{64}', value['fingerprint'])
            and value['expected_hash_from_parent_native_observation'] == expected_hash
            and value['expected_hash_displayed_by_dialog'] is False
            and value['native_operation_ids_not_assigned_by_driver'] is True, 'OUTCOME_ORIGINAL_FROZEN_REFUSED')
    return value['fingerprint']


def check_stage_intent(value, nonce, fingerprint):
    require(value == {'nonce': nonce, 'fingerprint': fingerprint}, 'OUTCOME_ORIGINAL_CONFIRM_INTENT_CHANGED')


def unchanged_pending(probe, label, native_original, provider, source, rows):
    native = probe.host_snapshot(label)
    require(native == native_original and not native['decisions'] and native['binding']['cursor'] == 2,
            'OUTCOME_PENDING_NATIVE_CHANGED')
    require(probe.original('runtime-note.md') == source
            and [row for row in probe.draft_rows() if row[0] == 'runtime-note.md'] == rows,
            'OUTCOME_PENDING_SOURCE_OR_DRAFT_CHANGED')
    current = probe.observe(label)
    require(provider_original(current) == provider_original(provider) and current['vault']['sequence'] == 2,
            'OUTCOME_PENDING_PROVIDER_CHANGED')
    return native, current


def completed_decision(native, frozen, receipt):
    require(len(native['decisions']) == 1, 'OUTCOME_ORIGINAL_DECISION_COUNT')
    decision = native['decisions'][0]
    require(decision['sequence'] == 2 and decision['choice'] == 'copy' and decision['destination'] == COPY
            and decision['expected_hash'] == sha(CONCURRENT) and decision['fingerprint'] == frozen['fingerprint']
            and decision['state'] == 'completed' and len({decision[key] for key in IDS}) == 5
            and all(canonical_uuid(decision[key]) for key in IDS),
            'OUTCOME_ORIGINAL_NATIVE_DECISION_CHANGED')
    heads = [row for row in native['heads'] if row['file_id'] == receipt['file_id']]
    require(native['binding']['cursor'] == 2 and len(heads) == 1 and heads[0]['revision'] == 2
            and heads[0]['path'] == receipt['path'] and heads[0]['hash'] == receipt['hash']
            and len(native['conflicts']) == 1 and native['conflicts'][0]['state'] == 'resolved',
            'OUTCOME_ORIGINAL_NATIVE_BOUNDARY_CHANGED')
    return decision


def run_original_outcome(probe, setup, prepared, device, conflicted, provider, receipt, rows, local, auth, sealed):
    context = {'run_id': int(__import__('os').environ['GITHUB_RUN_ID']), 'tools_commit': probe.args.tools_commit,
               'android_commit': probe.args.commit, 'desktop_commit': '931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'}
    hosted_scope(context, probe.args.serial, probe.owner)
    checkpoint(probe, 'start')
    client = original_outcome_client(probe.args.commit, probe.args.build_run_id)
    require(probe.binding['sha256'] == client.apk_sha256
            and len(rows) == 1 and rows[0][1].encode() == DIRTY and rows[0][2] > rows[0][3],
            'OUTCOME_ACTUAL_FIXED_CLIENT_OR_DRAFT_REFUSED')
    pid = probe.shell('pidof', APP, check=False)
    require(pid.returncode == 0 and not pid.stderr.strip() and re.fullmatch(rb'[1-9][0-9]{0,9}', pid.stdout.strip()),
            'OUTCOME_ORIGINAL_PID_REFUSED')
    nonce = str(uuid.uuid4()); original_pid = int(pid.stdout.strip())
    device_original, packages = observe_device(probe), observe_packages(probe)
    checkpoint(probe, 'input')
    publish_original_driver_file(probe, context, nonce, 'input',
        {'nonce': nonce, 'initial_hash': sha(LOCAL), 'concurrent_hash': sha(CONCURRENT)})
    continuous = OriginalOutcomeInstrumentation(probe, context, nonce, original_pid, device_original, packages)
    checkpoint(probe, 'old-frozen')
    continuous.next_ready('old-frozen'); old = continuous.read_ready('old-frozen')
    old_fingerprint = check_frozen(old, nonce, sha(LOCAL))
    manifest = {'nonce': nonce, 'fingerprint': old_fingerprint, 'original_pid': original_pid,
                'device': device_original, 'packages': packages}
    unchanged_pending(probe, 'outcome-old-frozen', conflicted, provider, LOCAL, rows)
    checkpoint(probe, 'concurrent-write')
    mutate_recorded_original_source(probe, context, manifest, LOCAL, CONCURRENT)
    unchanged_pending(probe, 'outcome-concurrent-before-confirm', conflicted, provider, CONCURRENT, rows)
    checkpoint(probe, 'stale-confirm')
    publish_original_driver_file(probe, context, nonce, 'stale-confirm',
        {'nonce': nonce, 'phase': 'stale-confirm', 'fingerprint': old_fingerprint})
    continuous.next_ready('stale-confirm-intent')
    check_stage_intent(continuous.read_ready('stale-confirm-intent'), nonce, old_fingerprint)
    checkpoint(probe, 'known-rejection')
    continuous.next_ready('known-stale-rejection'); refused = continuous.read_ready('known-stale-rejection')
    require(refused == {'nonce': nonce, 'fingerprint': old_fingerprint, 'expected_public_code': 'SYNC_CONFLICT_CHANGED',
                       'known_rejection_not_unknown_result': True, 'native_rows_not_proven_by_UI': True},
            'OUTCOME_KNOWN_NATIVE_REJECTION_REFUSED')
    unchanged_pending(probe, 'outcome-stale-refused', conflicted, provider, CONCURRENT, rows)
    checkpoint(probe, 'native-absence')
    atomic_json(probe.output/'outcome-real-stale-rejection-proof.json', {'nonce': nonce, 'passed': True,
        'original_fingerprint': old_fingerprint, 'actual_UI_public_code': 'SYNC_CONFLICT_CHANGED',
        'original_native_resolution_rows': 0, 'provider_sequence': 2,
        'failed_operation_ID_invented': False, 'unknown_result_or_runtime_failure_claimed': False,
        'original_saved_sha256': sha(LOCAL), 'retained_concurrent_sha256': sha(CONCURRENT),
        'dirty_draft_unchanged': True}, exclusive=True)
    publish_original_driver_file(probe, context, nonce, 'native-absence-checked',
        {'nonce': nonce, 'phase': 'native-absence-checked', 'fingerprint': old_fingerprint})
    checkpoint(probe, 'new-frozen')
    continuous.next_ready('new-frozen'); current = continuous.read_ready('new-frozen')
    fingerprint = check_frozen(current, nonce, sha(CONCURRENT))
    require(fingerprint != old_fingerprint, 'OUTCOME_NEW_REVIEW_NOT_NEW')
    unchanged_pending(probe, 'outcome-new-frozen', conflicted, provider, CONCURRENT, rows)
    manifest = dict(manifest, fingerprint=fingerprint)
    baseline = {'binding_id': conflicted['binding']['id'], 'remote_sha256': sha(REMOTE), 'draft_sha256': sha(DIRTY),
                'draft_generation': rows[0][2], 'draft_saved_generation': rows[0][3],
                'provider_metadata_sha256': sha(canonical(provider_original(provider)))}
    checkpoint(probe, 'compiled-renderer')
    debugger = OriginalCompletionBridge(probe, context, manifest, sha(CONCURRENT), baseline)
    checkpoint(probe, 'debugger-armed')
    debugger.expect('armed')
    checkpoint(probe, 'interrupt-confirm')
    publish_original_driver_file(probe, context, nonce, 'interrupt-confirm',
        {'nonce': nonce, 'phase': 'interrupt-confirm', 'fingerprint': fingerprint})
    continuous.next_ready('interrupt-confirm-intent')
    check_stage_intent(continuous.read_ready('interrupt-confirm-intent'), nonce, fingerprint)
    checkpoint(probe, 'original-paused')
    debugger.expect('paused')
    checkpoint(probe, 'completed-native')
    native = probe.host_snapshot('outcome-original-completed-paused')
    decision = completed_decision(native, current, receipt)
    require(probe.original('runtime-note.md') == REMOTE and probe.original(COPY) == CONCURRENT
            and [row for row in probe.draft_rows() if row[0] == 'runtime-note.md'] == rows,
            'OUTCOME_ORIGINAL_COMPLETED_BYTES_DIFFER')
    after = probe.observe('outcome-original-completed-paused')
    require(provider_original(after) == provider_original(provider), 'OUTCOME_ORIGINAL_LOCAL_DECISION_TRANSFERRED')
    native_proof = {'original_pid': original_pid,
        'native': {'binding_id': native['binding']['id'], 'previous_resolution_rows': 0, 'resolution_rows': 1, 'decision': decision},
        'files': {'source_sha256': sha(REMOTE), 'copy_sha256': sha(CONCURRENT), 'draft_sha256': sha(DIRTY),
                  'draft_generation': rows[0][2], 'draft_saved_generation': rows[0][3]},
        'provider': {'sequence': 2, 'original_metadata_sha256': sha(canonical(provider_original(after)))}}
    debugger.send('native-before-stop', native_proof)
    authorization = debugger.expect('owned-stop-authorized')
    checkpoint(probe, 'stop-original-app')
    stopped = stop_original_once(probe, context, manifest, authorization)
    require(stopped['original_absence_observed'], 'OUTCOME_ORIGINAL_APP_STOP_UNPROVEN')
    debugger.send('original-exited', stopped['original_exit_proof'])
    bridge_exit = debugger.expect('original-exited')
    checkpoint(probe, 'close-original-debugger')
    debugger.close_after_original_exit(stopped['original_exit_proof'])
    checkpoint(probe, 'stop-original-driver')
    driver_exit = stop_original_driver_once(probe, context, manifest, continuous.driver_pid,
                                           stopped['original_exit_proof'], bridge_exit)
    require(driver_exit['cold_original_entry_permitted'], 'OUTCOME_ORIGINAL_DRIVER_STOP_UNPROVEN')
    continuous.finish_after_driver_exit(driver_exit)
    require(probe.host_snapshot('outcome-before-cold') == native, 'OUTCOME_ORIGINAL_COMPLETED_NATIVE_CHANGED')
    checkpoint(probe, 'cold-original')
    probe.sync_phase('cold-original-decision')
    cold_pid = probe.shell('pidof', APP, check=False)
    require(cold_pid.returncode == 0 and not cold_pid.stderr.strip()
            and re.fullmatch(rb'[1-9][0-9]{0,9}', cold_pid.stdout.strip()) and int(cold_pid.stdout.strip()) != original_pid,
            'OUTCOME_COLD_NOT_NEW_ORIGINAL_PROCESS')
    seen = probe.original_conflict_driver('cold-receipt')
    cold_native = probe.host_snapshot('outcome-cold-original-read')
    validate_cold_original_read(native, cold_native, decision, seen)
    require(provider_original(probe.observe('outcome-cold-original-read')) == provider_original(provider),
            'OUTCOME_COLD_ORIGINAL_READ_TRANSFERRED')
    checkpoint(probe, 'copy-cycle')
    probe.sync_phase('cycle-conflict-copy')
    pushed = probe.host_snapshot('outcome-copy-uploaded'); uploaded = probe.observe('outcome-copy-uploaded')
    require(pushed['decisions'] == native['decisions'] and pushed['binding']['cursor'] == uploaded['vault']['sequence'] == 3
            and len(uploaded['rows']['revisions']) == 3, 'OUTCOME_COPY_ORIGINAL_BOUNDARY_CHANGED')
    revision = uploaded['rows']['revisions'][-1]
    require(revision['operation_id'] == decision['copy_id'] and revision['path'] == COPY and revision['base_revision'] == 0
            and revision['device_id'] == device and revision['hash'] == sha(CONCURRENT) and revision['size'] == len(CONCURRENT)
            and revision['file_id'] != receipt['file_id'], 'OUTCOME_COPY_ORIGINAL_ACK_CHANGED')
    jobs = [row for row in pushed['jobs'] if row['operation_id'] == decision['copy_id']]
    require(len(jobs) == 1 and jobs[0]['state'] == 'acked' and jobs[0]['remote_revision'] == 3 and jobs[0]['error'] is None
            and any(row['operation_id'] == local['operation_id'] and row['state'] == 'archived' for row in pushed['jobs'])
            and not any(row['hash'] == sha(DIRTY) for row in uploaded['rows']['revisions']), 'OUTCOME_COPY_ORIGINAL_NATIVE_ACK_MISSING')
    checkpoint(probe, 'cold-draft')
    probe.phase('conflict-draft-reopen')
    require(probe.host_snapshot('outcome-final') == pushed and probe.original('runtime-note.md') == REMOTE
            and probe.original(COPY) == CONCURRENT and [row for row in probe.draft_rows() if row[0] == 'runtime-note.md'] == rows
            and probe.snapshot('outcome-final-auth') == auth and probe.ciphertext_fingerprints() == sealed,
            'OUTCOME_COLD_ORIGINAL_DRAFT_SOURCE_COPY_AUTH_CHANGED')
    from android_sync_runtime import read_original_object
    checkpoint(probe, 's3-originals')
    for row, body in ((receipt, REMOTE), (revision, CONCURRENT)):
        require(read_original_object(setup.endpoint, prepared['vault']['vault_id'], row['hash'], setup.session()['access_token']) == body,
                'OUTCOME_ACTUAL_S3_ORIGINAL_BYTES_DIFFER')
    checkpoint(probe, 'complete')
    atomic_json(probe.output/'sync-original-native-outcome-proof.json', {'passed': True, 'context': context,
        'APK_sha256': client.apk_sha256, 'build_run': client.build_run, 'original_app_pid': original_pid,
        'cold_app_pid': int(cold_pid.stdout.strip()), 'nonce': nonce, 'old_fingerprint': old_fingerprint,
        'new_fingerprint': fingerprint, 'original_native_decision': decision, 'original_copy_revision': revision,
        'actual_original_known_stale_rejection': 'SYNC_CONFLICT_CHANGED',
        'actual_completed_native_reply_held_before_application_callback': True,
        'actual_original_compiled_renderer_resource_hashes_and_declared_code_presence': True,
        'original_app_and_driver_exit_before_cold_UI': True, 'original_five_IDs_and_native_fingerprint_retained': True,
        'cold_UI_review_digest_display_not_claimed': True, 'one_original_copy_upload_and_S3_bytes': True,
        'dirty_draft_bytes_not_uploaded': True, 'fake_native_error_or_callback_rewrite': False,
        'lost_HTTP_reply_or_native_current_SYNC_REVIEW_CHANGED_bug_claimed': False,
        'desktop_pair_physical_phone_upgrade_signing_release_or_all_A2_A6_complete': False}, exclusive=True)
