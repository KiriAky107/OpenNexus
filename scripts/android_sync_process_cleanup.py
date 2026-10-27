"""One original held response cleanup; unknown controls are only reconciled.

No GUI, product IPC, request replay, process command or receipt repair. A cleanup release is
distinct from a passed Process and never establishes client receipt.
"""
from pathlib import Path
import time
import re
import uuid

from android_runtime_probe import require
from android_sync_fixture import arm_control, control_root, selected_target
from android_sync_stop_host import OriginalHeldRevision, canonical, closed_json, record, same_fields

LABEL = 'process-original-held'


def read_closed(path, *, optional=False):
    require(not path.is_symlink(), 'SYNC_PROCESS_CLEANUP_ALIAS')
    if optional and not path.exists(): return None
    require(path.is_file() and 0 < path.stat().st_size <= 4096, 'SYNC_PROCESS_CLEANUP_FILE_BOUND')
    return closed_json(path.read_bytes())


def wait_marker(read):
    previous = time.monotonic(); deadline = previous+2
    while True:
        now = time.monotonic()
        require(previous <= now <= deadline, 'SYNC_PROCESS_CLEANUP_CLOCK_OR_TIMEOUT'); previous = now
        value = read()
        finished = time.monotonic()
        require(now <= finished <= deadline, 'SYNC_PROCESS_CLEANUP_OBSERVATION_TOO_LATE')
        previous = finished
        if value is not None: return value
        time.sleep(.05)


class OriginalProcessControl:
    def __init__(self, owner, output, vault):
        require(type(vault) is str and re.fullmatch(r'[0-9a-f]{32}',vault), 'SYNC_PROCESS_CLEANUP_VAULT')
        require(isinstance(owner, Path) and owner.is_absolute() and owner.resolve() == owner
                and owner.is_dir() and not owner.is_symlink(), 'SYNC_PROCESS_CLEANUP_OWNER')
        require(isinstance(output, Path) and output.is_absolute() and output.resolve() == output
                and output.is_dir() and not output.is_symlink(), 'SYNC_PROCESS_CLEANUP_OUTPUT')
        self.owner, self.output, self.vault = owner, output, vault
        self.operation = self.held = None
        self.arm_attempted = self.cleanup_attempted = False

    def namespace(self):
        root = control_root(self.owner)
        require(all((root/name).is_dir() and not (root/name).is_symlink()
                    for name in ('control-intents','control-receipts','fault-events')),
                'SYNC_PROCESS_CLEANUP_NAMESPACE')
        require(same_fields(read_closed(root/'target.json'), dict(schema=1,vault_id=self.vault))
                and selected_target(self.owner) == self.vault, 'SYNC_PROCESS_CLEANUP_TARGET')
        return root

    def arm(self):
        require(not self.arm_attempted, 'SYNC_PROCESS_HOLD_NOT_REPEATED'); self.arm_attempted = True
        root = self.namespace()
        require(same_fields(read_closed(root/'control.json'), dict(revision=0,operation_id=None,mode='normal')),
                'SYNC_PROCESS_FRESH_ORIGINAL_CONTROL')
        operation = str(uuid.uuid4())
        record(self.output/'process-hold-control-intent.json', dict(operation_id=operation,mode='hold_revision',
            expected_revision=0,automatic_retry=False,native_or_server_operation_id=False))
        self.operation = operation  # Only the successfully frozen original ID.
        receipt = arm_control(self.owner,operation,'hold_revision',0)
        require(same_fields(receipt,dict(operation_id=operation,mode='hold_revision',expected_revision=0,result_revision=1)),
                'SYNC_PROCESS_ORIGINAL_HOLD_RESULT')
        record(self.output/'process-hold-control-receipt.json', receipt)
        self.held = OriginalHeldRevision(self.owner,self.vault,receipt)
        return receipt

    def cleanup(self):
        # The caller must freeze wholefalse before this independent cleanup.
        failure = read_closed(self.output/'process-component-failure.json')
        require(type(failure) is dict and failure.get('passed') is False and failure.get('automatic_retry') is False
                and failure.get('whole_process_component_passed') is False,
                'SYNC_PROCESS_CLEANUP_REQUIRES_ORIGINAL_FAILURE')
        require(not self.cleanup_attempted, 'SYNC_PROCESS_CLEANUP_NOT_REPEATED'); self.cleanup_attempted = True
        if self.operation is None:
            return self.result('no_owned_hold_attempt', released=False)
        root = self.namespace(); operation = self.operation
        require(canonical(operation) and same_fields(read_closed(self.output/'process-hold-control-intent.json'),
            dict(operation_id=operation,mode='hold_revision',expected_revision=0,automatic_retry=False,native_or_server_operation_id=False)),
            'SYNC_PROCESS_CLEANUP_ORIGINAL_HOLD_INTENT')
        hold = dict(operation_id=operation,mode='hold_revision',expected_revision=0,result_revision=1)
        original_intent = read_closed(root/'control-intents'/(operation+'.json'),optional=True)
        original_receipt = read_closed(root/'control-receipts'/(operation+'.json'),optional=True)
        current = read_closed(root/'control.json')
        require(original_intent is None or same_fields(original_intent,hold), 'SYNC_PROCESS_CLEANUP_CHANGED_HOLD_INTENT')
        require(original_receipt is None or same_fields(original_receipt,hold), 'SYNC_PROCESS_CLEANUP_CHANGED_HOLD_RECEIPT')
        record(self.output/'process-failure-hold-readonly.json', dict(operation_id=operation,
            original_control_intent_observed=original_intent is not None,original_control_receipt_observed=original_receipt is not None,
            same_original_hold_current=same_fields(current,dict(revision=1,operation_id=operation,mode='hold_revision')),
            control_writes_performed=False,whole_process_component_passed=False))
        if original_intent is None or original_receipt is None:
            # Even if the old state write happened, do not repair its missing
            # receipt or re-enter the CAS to produce a cleaner-looking result.
            return self.result('original_hold_result_unconfirmed',released=False)
        # Unknown arm results are readonly even if an internal receipt exists.
        # Do not reconstruct a holder or manufacture the missing public receipt.
        public = read_closed(self.output/'process-hold-control-receipt.json',optional=True)
        if self.held is None or public is None:
            return self.result('original_hold_result_unconfirmed',released=False)
        require(same_fields(public,hold) and self.held.control == hold,
                'SYNC_PROCESS_CLEANUP_ORIGINAL_PUBLIC_RECEIPT')
        held = self.held
        release_path = self.output/(LABEL+'-release-control-intent.json')
        attempted = held.release_attempted or release_path.exists() or release_path.is_symlink()
        if attempted:
            return self.observe_release(held,release_path,'original')
        require(same_fields(current,dict(revision=1,operation_id=operation,mode='hold_revision')),
                'SYNC_PROCESS_CLEANUP_NOT_ORIGINAL_HELD_STATE')
        marker = wait_marker(held.read_held)
        record(self.output/'process-failure-original-held-marker.json',marker)
        # One explicit cleanup action for the original already-executed response.
        # begin_release freezes its distinct intent and refuses unknown reentry.
        try: receipt = held.begin_release(self.output,'process-failure-cleanup')
        except Exception as error:
            record(self.output/'process-failure-cleanup-release-unknown.json',dict(error_type=type(error).__name__,
                automatic_retry=False,whole_process_component_passed=False))
            return self.observe_release(held,self.output/'process-failure-cleanup-release-control-intent.json','cleanup')
        value = wait_marker(held.read_released); self.released(value,operation)
        record(self.output/'process-failure-cleanup-released-marker.json',value)
        return self.result('one_explicit_cleanup_release',released=True,cleanup_control=receipt)

    def observe_release(self, held, path, kind):
        require(kind in ('original','cleanup'), 'SYNC_PROCESS_CLEANUP_READ_KIND')
        operation = self.operation; root = self.namespace()
        intent = read_closed(path,optional=True)
        if intent is None: return self.result(kind+'_release_intent_unavailable_no_retry',released=False)
        require(type(intent) is dict and canonical(intent.get('operation_id')) and same_fields(intent,
            dict(operation_id=intent['operation_id'],mode='release',expected_revision=1,held_operation_id=operation,
                 automatic_retry=False,client_receipt_known=False)), 'SYNC_PROCESS_CLEANUP_RELEASE_INTENT')
        expected = dict(operation_id=intent['operation_id'],mode='release',expected_revision=1,result_revision=2)
        internal = read_closed(root/'control-intents'/(intent['operation_id']+'.json'),optional=True)
        receipt = read_closed(root/'control-receipts'/(intent['operation_id']+'.json'),optional=True)
        require(internal is None or same_fields(internal,expected), 'SYNC_PROCESS_CLEANUP_CHANGED_RELEASE_INTENT')
        require(receipt is None or same_fields(receipt,expected), 'SYNC_PROCESS_CLEANUP_CHANGED_RELEASE_RECEIPT')
        current = read_closed(root/'control.json')
        released = same_fields(current,dict(revision=2,operation_id=intent['operation_id'],mode='release'))
        require(released or same_fields(current,dict(revision=1,operation_id=operation,mode='hold_revision')),
                'SYNC_PROCESS_CLEANUP_FOREIGN_CONTROL')
        record(self.output/('process-failure-'+kind+'-release-readonly.json'),dict(operation_id=intent['operation_id'],held_operation_id=operation,
            original_control_intent_observed=internal is not None,original_control_receipt_observed=receipt is not None,
            same_original_release_current=released,control_writes_performed=False,automatic_retry=False))
        if released:
            # Missing original receipt remains missing; no CAS/receipt repair.
            value = wait_marker(lambda:held.marker_json(root/'fault-events'/(operation+'.released.json')))
            self.released(value,operation)
            record(self.output/('process-failure-'+kind+'-released-marker.json'),value)
        return self.result(kind+'_release_readonly_reconciled' if released else kind+'_release_unconfirmed_no_retry',released=released)

    @staticmethod
    def released(value, operation):
        require(type(value) is dict and set(value) == {'operation_id','original_response_send_attempted','ASGI_send_returned','client_receipt_known'}
                and value['operation_id'] == operation and value['original_response_send_attempted'] is True
                and type(value['ASGI_send_returned']) is bool and value['client_receipt_known'] is False,
                'SYNC_PROCESS_CLEANUP_ORIGINAL_RELEASE_MARKER')

    def result(self, state, *, released, cleanup_control=None):
        value = dict(state=state,original_hold_operation_id=self.operation,original_response_release_observed=released,
            cleanup_control=cleanup_control,whole_process_component_passed=False,client_receipt_known=False,automatic_retry=False)
        record(self.output/'process-failure-control-cleanup.json',value)
        return value
