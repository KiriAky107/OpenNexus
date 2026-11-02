"""Optional facts from an existing rejected proc read; no IO or admission."""
from dataclasses import dataclass

STEPS = ('DIRECTORY_BEFORE_STAT', 'FIRST_STAT_READ', 'EXECUTABLE_READLINK',
         'SECOND_STAT_READ', 'DIRECTORY_AFTER_STAT')


def _valid_step(value):
    return type(value) is str and value in STEPS


@dataclass(frozen=True, slots=True)
class MissingProcRead:
    read_step: str


def capture(read_step, file_missing_observed, original_directory_exists):
    """Unknown or unexecuted comparisons never become false or an absence."""
    if (_valid_step(read_step) and file_missing_observed is True and
            original_directory_exists is True):
        return MissingProcRead(read_step)
    return None


def project(fact):
    if type(fact) is not MissingProcRead or not _valid_step(fact.read_step):
        return None
    return dict(schema=1, read_step=fact.read_step, file_missing_error_observed=True,
                same_original_directory_exists=True, operation_returned=False,
                original_guard_rejected=True, diagnostic_only=True,
                current_process_identity_verified=False, process_absence_verified=False,
                raw_proc_or_exception_recorded=False, runtime_verified=False)


def validate(value):
    keys = {'schema', 'read_step', 'file_missing_error_observed', 'same_original_directory_exists',
            'operation_returned', 'original_guard_rejected', 'diagnostic_only',
            'current_process_identity_verified', 'process_absence_verified',
            'raw_proc_or_exception_recorded', 'runtime_verified'}
    if not (type(value) is dict and all(type(key) is str for key in value) and set(value) == keys and
            type(value['schema']) is int and value['schema'] == 1 and _valid_step(value['read_step'])):
        return False
    for key in ('file_missing_error_observed', 'same_original_directory_exists',
                'original_guard_rejected', 'diagnostic_only'):
        if value[key] is not True:
            return False
    return all(value[key] is False for key in ('operation_returned', 'current_process_identity_verified',
               'process_absence_verified', 'raw_proc_or_exception_recorded', 'runtime_verified'))
