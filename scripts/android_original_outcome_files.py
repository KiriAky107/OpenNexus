"""Create-once owned UI permissions and one recorded concurrent source write."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import uuid

from android_auth_fixture import atomic_json
from android_desktop_handoff import require
from android_product_pair_android import hosted_scope
from android_runtime_probe import APP, DRIVER
import android_original_outcome_process as original

PERMISSIONS = ('stale-confirm', 'native-absence-checked', 'interrupt-confirm')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def scope(probe, context, nonce):
    hosted_scope(context, probe.args.serial, probe.owner)
    require(probe.args.commit == context['android_commit'] and probe.args.tools_commit == context['tools_commit']
            and type(nonce) is str and str(uuid.UUID(nonce)) == nonce, 'OUTCOME_FILE_SCOPE_REFUSED')
    root = Path(probe.output)
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    require(root.is_absolute() and root.is_dir() and root.resolve() == root and not root.is_symlink()
            and root.is_relative_to(workspace/'.build'), 'OUTCOME_FILE_OUTPUT_REFUSED')
    return root


def driver_body(nonce, phase, value):
    require(type(value) is dict and value.get('nonce') == nonce, 'OUTCOME_DRIVER_BODY_REFUSED')
    if phase == 'input':
        require(set(value) == {'nonce', 'initial_hash', 'concurrent_hash'}
                and all(type(value[key]) is str and re.fullmatch(r'[0-9a-f]{64}', value[key])
                        for key in ('initial_hash', 'concurrent_hash'))
                and value['initial_hash'] != value['concurrent_hash'], 'OUTCOME_DRIVER_BODY_REFUSED')
        name = 'owned-outcome-input.json'
    else:
        require(phase in PERMISSIONS and set(value) == {'nonce', 'phase', 'fingerprint'} and value['phase'] == phase
                and type(value['fingerprint']) is str and re.fullmatch(r'[0-9a-f]{64}', value['fingerprint']),
                'OUTCOME_DRIVER_BODY_REFUSED')
        name = 'owned-outcome-authorize-'+phase+'.json'
    return name, json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def publish_original_driver_file(probe, context, nonce, phase, value):
    root = scope(probe, context, nonce)
    name, body = driver_body(nonce, phase, value)
    intent = root / ('driver-publication-'+phase+'-intent.json')
    # This admission precedes even the private-file command. An unknown write
    # can only be read back through its original intent, never published again.
    atomic_json(intent, {'context': context, 'nonce': nonce, 'phase': phase, 'filename': name,
                        'body_sha256': sha(body), 'body': value, 'automatic_retry': False}, exclusive=True)
    target = 'no_backup/'+name
    temporary = target+'.'+nonce+'.tmp'
    script = 'umask 077; test -d no_backup && test ! -e '+target+' && test ! -e '+temporary+' && cat > '+temporary+' && mv -n '+temporary+' '+target
    outcome = {'nonce': nonce, 'phase': phase, 'outcome': 'unknown', 'returncode': None}
    try:
        result = probe.device('shell', '-T', shlex.join(('run-as', DRIVER, 'sh', '-c', script)), input=body, check=False)
        require(type(result.returncode) is int and not result.stderr.strip(), 'OUTCOME_DRIVER_PUBLICATION_UNCONFIRMED')
        outcome.update(outcome='confirmed', returncode=result.returncode)
    except Exception:
        pass
    atomic_json(root / ('driver-publication-'+phase+'-command.json'), outcome, exclusive=True)
    require(outcome['outcome'] == 'confirmed' and outcome['returncode'] == 0, 'OUTCOME_DRIVER_PUBLICATION_UNCONFIRMED')
    return outcome


def reconcile_original_driver_file(probe, context, nonce, phase, label):
    root = scope(probe, context, nonce)
    require(phase == 'input' or phase in PERMISSIONS, 'OUTCOME_DRIVER_BODY_REFUSED')
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,39}', label), 'OUTCOME_FILE_LABEL_REFUSED')
    intent_path = root / ('driver-publication-'+phase+'-intent.json')
    require(intent_path.is_file() and not intent_path.is_symlink() and intent_path.stat().st_size <= 4096,
            'OUTCOME_DRIVER_ORIGINAL_PUBLICATION_REFUSED')
    intent = json.loads(intent_path.read_text('utf-8'))
    name, body = driver_body(nonce, phase, intent['body'])
    require(intent['context'] == context and intent['filename'] == name and intent['body_sha256'] == sha(body)
            and intent['nonce'] == nonce and intent['phase'] == phase, 'OUTCOME_DRIVER_ORIGINAL_PUBLICATION_REFUSED')
    path = root / ('driver-publication-'+phase+'-observation-'+label+'.json')
    require(not path.exists(), 'OUTCOME_FILE_OBSERVATION_EXISTS')
    result = probe.shell('run-as', DRIVER, 'cat', 'no_backup/'+name, check=False)
    value = {'nonce': nonce, 'phase': phase, 'original_body_observed': result.returncode == 0
             and not result.stderr.strip() and result.stdout == body,
             'read_only_no_second_publication_or_UI_input': True, 'unknown_command_not_relabelled': True}
    atomic_json(path, value, exclusive=True)
    return value


def mutate_recorded_original_source(probe, context, manifest, old, new):
    root = original.scope(probe, context, manifest)
    require(type(old) is bytes and type(new) is bytes and 0 < len(old) <= 4096 and 0 < len(new) <= 4096
            and old != new, 'OUTCOME_CONCURRENT_BYTES_REFUSED')
    original.same_original(probe, manifest)
    require(type(probe.root) is str and re.fullmatch(r'(?:[A-Za-z0-9_.-]+/)+', probe.root)
            and '..' not in probe.root.split('/'), 'OUTCOME_ORIGINAL_PRIVATE_ROOT_REFUSED')
    intent = root / 'concurrent-source-original-intent.json'
    require(not intent.exists(), 'OUTCOME_CONCURRENT_SOURCE_ALREADY_REQUESTED')
    require(probe.original('runtime-note.md') == old, 'OUTCOME_ORIGINAL_SOURCE_CHANGED')
    pid = probe.shell('pidof', APP, check=False)
    require(pid.returncode == 0 and not pid.stderr.strip()
            and pid.stdout.strip() == str(manifest['original_pid']).encode('ascii'), 'OUTCOME_ORIGINAL_PID_CHANGED')
    for filename, body in (('concurrent-source-before.md', old), ('concurrent-source-planned.md', new)):
        with (root/filename).open('xb') as output:
            output.write(body); output.flush(); os.fsync(output.fileno())
    atomic_json(intent, {'context': context, 'nonce': manifest['nonce'], 'old_frozen_fingerprint': manifest['fingerprint'],
                        'original_app_pid': manifest['original_pid'], 'path': 'runtime-note.md',
                        'before_sha256': sha(old), 'concurrent_sha256': sha(new),
                        'automatic_retry': False, 'test_fixture_only': True}, exclusive=True)
    target = probe.root+'vault/runtime-note.md'
    temporary = target+'.'+manifest['nonce']+'.tmp'
    script = 'umask 077; test -f '+target+' && test ! -L '+target+' && test ! -e '+temporary+' && cat > '+temporary+' && mv '+temporary+' '+target
    outcome = {'nonce': manifest['nonce'], 'outcome': 'unknown', 'returncode': None}
    try:
        result = probe.device('shell', '-T', shlex.join(('run-as', APP, 'sh', '-c', script)), input=new, check=False)
        require(type(result.returncode) is int and not result.stderr.strip(), 'OUTCOME_CONCURRENT_SOURCE_UNCONFIRMED')
        outcome.update(outcome='confirmed', returncode=result.returncode)
    except Exception:
        pass
    atomic_json(root/'concurrent-source-original-command.json', outcome, exclusive=True)
    require(outcome['outcome'] == 'confirmed' and outcome['returncode'] == 0, 'OUTCOME_CONCURRENT_SOURCE_UNCONFIRMED')
    require(probe.original('runtime-note.md') == new, 'OUTCOME_CONCURRENT_SOURCE_BYTES_DIFFER')
    return outcome
