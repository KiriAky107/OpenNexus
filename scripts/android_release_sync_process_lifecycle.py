"""Readonly process originals and failure policy with no implicit product stop.

This module is used only by the dedicated process-loss orchestrator. The normal
auth CLI/failure hooks must not run after an unknown lifecycle command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from android_release_observation import APP, DATABASES, StoppedReleaseObserver, exclusive
from android_release_sync_process_snapshot import NOTE, STAGES, project_process
from android_runtime_probe import require
from android_sync_stop_host import closed_json, record, same_fields

LIMIT = 32 * 1024**2
STEPS = frozenset(('prepare','original-note-Save','original-login','independent-setup-and-association',
    'original-hold-control','original-process-loss-GUI','killed-original-projection','killed-new-PID-read',
    'explicit-original-changes-reconcile','final-new-PID-cold-read'))
ERRORS = frozenset(('RuntimeError','TimeoutError','FileExistsError','FileNotFoundError','PermissionError',
    'ValueError','TypeError','KeyError','OSError','AssertionError','JSONDecodeError','UnicodeDecodeError'))


def error_kind(error):
    name = type(error).__name__
    return name if name in ERRORS else 'Exception'


def exact_directory(path):
    require(isinstance(path, Path) and path.is_absolute() and path.resolve() == path
            and path.is_dir() and all(not p.is_symlink() for p in (path,*path.parents)),
            'SYNC_PROCESS_LIFECYCLE_CANONICAL_DIRECTORY')
    return path


def publish_original(raw, output, identity, *, include_note):
    """Export only the complete approved snapshot, never observer raw streams."""
    exact_directory(raw)
    expected = {db+s for db in DATABASES for s in ('','-wal','-shm')}
    if include_note: expected.add('vault/'+NOTE)
    manifest_path = raw/'snapshot.json'
    require(manifest_path.is_file() and not manifest_path.is_symlink() and 0 < manifest_path.stat().st_size <= 16384,
            'SYNC_PROCESS_ORIGINAL_MANIFEST_BOUND')
    encoded = manifest_path.read_bytes(); manifest = closed_json(encoded)
    require(type(manifest) is dict and set(manifest) == {'package','identity','files','app_stopped_before_and_after',
        'credential_store_copied','SQL_executed_on_device','normal_phone_permissions_verified'}
        and same_fields({k:v for k,v in manifest.items() if k != 'files'},dict(package=APP,identity=identity,
            app_stopped_before_and_after=True,credential_store_copied=False,SQL_executed_on_device=False,
            normal_phone_permissions_verified=False)) and type(manifest['files']) is dict and set(manifest['files']) == expected,
            'SYNC_PROCESS_ORIGINAL_SCOPE_OR_OWNER')
    admitted = {'snapshot.json'}
    for name,item in manifest['files'].items():
        path = raw/name
        require(not path.is_symlink(), 'SYNC_PROCESS_ORIGINAL_LINK')
        if item is None:
            require(name.endswith(('-wal','-shm')) and not path.exists(), 'SYNC_PROCESS_ORIGINAL_ABSENT_CHANGED')
            continue
        require(type(item) is dict and set(item) == {'bytes','sha256'} and type(item['bytes']) is int
            and 0 <= item['bytes'] <= LIMIT and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}',item['sha256'])
            and path.is_file() and path.stat().st_size == item['bytes'] and path.resolve() == path,
            'SYNC_PROCESS_ORIGINAL_FILE_BOUND')
        with path.open('rb') as stream: sha = hashlib.file_digest(stream,'sha256').hexdigest()
        require(sha == item['sha256'], 'SYNC_PROCESS_ORIGINAL_HASH')
        admitted.add(name)
    entries = list(raw.rglob('*'))
    require(all(not p.is_symlink() and ((p.is_file() and p.relative_to(raw).as_posix() in admitted)
                or (p.is_dir() and p.relative_to(raw).as_posix() in {'vault','vault/.ainote'}))
                for p in entries) and {p.relative_to(raw).as_posix() for p in entries if p.is_file()} == admitted,
            'SYNC_PROCESS_ORIGINAL_UNAPPROVED_ENTRY')
    require(output.is_absolute() and output.resolve() == output and not output.exists()
            and all(not p.is_symlink() for p in (output,*output.parents))
            and not output.is_relative_to(raw) and not raw.is_relative_to(output), 'SYNC_PROCESS_NEW_PUBLIC_ORIGINAL')
    output.mkdir(parents=True)
    for name in sorted(admitted):
        target = output/name; target.parent.mkdir(parents=True,exist_ok=True)
        # The retained private original is bounded and verified before export.
        data = (raw/name).read_bytes()
        if name == 'snapshot.json': require(data == encoded,'SYNC_PROCESS_MANIFEST_CHANGED')
        else: require(len(data) == manifest['files'][name]['bytes'] and hashlib.sha256(data).hexdigest() == manifest['files'][name]['sha256'],
                      'SYNC_PROCESS_ORIGINAL_CHANGED_DURING_EXPORT')
        exclusive(target,data)
    require(manifest_path.read_bytes() == encoded, 'SYNC_PROCESS_MANIFEST_CHANGED_AFTER_EXPORT')
    for name,item in manifest['files'].items():
        original = raw/name
        if item is None:
            require(not original.exists() and not original.is_symlink(), 'SYNC_PROCESS_ORIGINAL_ABSENT_CHANGED_AFTER_EXPORT')
        else:
            require(original.is_file() and not original.is_symlink() and original.stat().st_size == item['bytes'],
                    'SYNC_PROCESS_ORIGINAL_CHANGED_AFTER_EXPORT')
            with original.open('rb') as stream: sha = hashlib.file_digest(stream,'sha256').hexdigest()
            require(sha == item['sha256'], 'SYNC_PROCESS_ORIGINAL_CHANGED_AFTER_EXPORT')
    return manifest


def collect_original(subject, label, *, stage=None):
    """No stop, status/open, GUI, root request or credential copy is issued here."""
    require(label in STAGES or label == 'failure', 'SYNC_PROCESS_COLLECTOR_LABEL')
    require((label == 'failure' and stage is None) or (label == stage and stage in STAGES), 'SYNC_PROCESS_COLLECTOR_STAGE')
    tools = exact_directory(subject.tools.absolute()); output = exact_directory(subject.output.absolute())
    require(not tools.is_relative_to(output) and not output.is_relative_to(tools), 'SYNC_PROCESS_PRIVATE_OBSERVER_OUTSIDE_ARTIFACT')
    private = tools/('process-original-observer-'+label)
    observer = StoppedReleaseObserver(subject.device,subject.args.serial,private,[NOTE])
    if label == 'failure': observer.allowed = {db+s for db in DATABASES for s in ('','-wal','-shm')}
    observer.identity = subject.observer.identity
    require(subject.identity is not None and observer.observe_identity() == subject.identity == observer.identity,
            'SYNC_PROCESS_COLLECTOR_OWNER_CHANGED')
    require(observer.text('id','-u') == '0', 'SYNC_PROCESS_COLLECTOR_ROOT_UNAVAILABLE')
    # Both methods reject running products; neither invokes a lifecycle command.
    root = observer.discover_data_root(); observer.copy_stopped(root,'snapshot')
    raw = private/'snapshot'; public = output/'content-originals'/label/'snapshot'
    publish_original(raw,public,subject.identity,include_note=label != 'failure')
    if label == 'failure': return public
    if label == 'killed': subject.process_raw_killed_preserved = True
    value = project_process(public,tools/('process-working-'+label),stage=stage)
    record(output/(label+'-content-projection.json'),value)
    return value,(public/'vault'/NOTE).read_bytes()


def freeze_failure(subject, error):
    """The original wholefalse record precedes cleanup and all observations."""
    stage = getattr(subject,'process_stage','prepare')
    stage = stage if stage in STEPS else 'unclassified'
    control = getattr(subject,'process_control',None)
    operation = getattr(control,'operation',None)
    if operation is not None:
        require(type(operation) is str and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',operation),
                'SYNC_PROCESS_FAILURE_ORIGINAL_CONTROL_ID')
    value = dict(passed=False,stage=stage,error_type=error_kind(error),original_hold_operation_id=operation,
        automatic_retry=False,whole_process_component_passed=False,Native_RAM_invocation_id_observed=False,
        no_implicit_product_stop_or_reopen=True)
    record(subject.output/'process-component-failure.json',value)
    return value


def handle_failure(subject, error):
    require(not getattr(subject,'process_failure_handled',False),'SYNC_PROCESS_FAILURE_NOT_REPEATED')
    subject.process_failure_handled = True
    freeze_failure(subject,error)
    control = getattr(subject,'process_control',None)
    if control is not None:
        try: control.cleanup()
        except Exception as failure:
            record(subject.output/'process-failure-control-cleanup-unavailable.json',dict(error_type=error_kind(failure),
                whole_process_component_passed=False,automatic_retry=False,client_receipt_known=False))
    # Retain whatever is actually stopped without creating that state. Unknown
    # lifecycle results never authorize another force-stop or product reopen.
    try: collect_original(subject,'failure')
    except Exception as failure:
        record(subject.output/'process-failure-original-unavailable.json',dict(error_type=error_kind(failure),
            whole_process_component_passed=False,product_stop_or_reopen_issued=False))
        return
    # Only after a stopped DB-only original has been retained may the existing
    # private auth copier/stat-hash observer and bounded provider reads run.
    for label,read in (('auth',lambda:subject.snapshot('process-failure-auth')),
                       ('cipher-fingerprints',subject.observer.ciphertext_fingerprints),
                       ('provider',lambda:subject.observe('process-failure')),
                       ('sync-provider',lambda:subject.sync_provider('process-failure'))):
        try:
            value = read()
            if label == 'cipher-fingerprints': record(subject.output/'process-failure-cipher-fingerprints.json',value)
        except Exception as failure:
            record(subject.output/('process-failure-'+label+'-unavailable.json'),dict(error_type=error_kind(failure),
                whole_process_component_passed=False,automatic_retry=False))


def run_guarded(subject, action):
    try: return action()
    except Exception as error:
        handle_failure(subject,error)
        raise


def main(probe_type, argv=None):
    """Dedicated CLI: no normal auth main/failure hook and no final stop."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','sdk','java','output'): parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args(argv); probe = None
    try:
        probe = probe_type(args.inputs,args.sdk,args.java,args.output)
        result = probe.run()
        require(result['passed'] is True and result['physical_phone_verified'] is False and result['published'] is False,
                'SYNC_PROCESS_CLI_RESULT')
        print(json.dumps({k:result[k] for k in ('kind','passed','abi','physical_phone_verified','published')}))
        return 0
    except Exception as error:
        if probe is not None and not getattr(probe,'process_failure_handled',False):
            try: handle_failure(probe,error)
            except Exception as failure:
                # A pre-existing exclusive record is preserved, never repaired.
                try: record(probe.output/'process-failure-policy-unavailable.json',dict(error_type=error_kind(failure),
                        passed=False,automatic_retry=False,no_implicit_product_stop_or_reopen=True))
                except Exception: pass
        print('RELEASE_SYNC_PROCESS_FAILED_'+error_kind(error))
        return 1
