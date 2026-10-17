"""Owned hosted app stop: one command, then original read-only observations.

Not executed against a device until the full new fixed-package scope is wired.
No retry, credential access, callback rewrite, native resolution or profile clear.
"""
from pathlib import Path
import json,os,re,uuid
from android_auth_fixture import atomic_json
from android_desktop_handoff import context_bytes,require
from android_product_pair_android import hosted_scope,observe_device,observe_packages
from android_runtime_probe import APP


def scope(probe,context,manifest):
    # This must precede any file or ADB access, including failure cleanup.
    hosted_scope(context,probe.args.serial,probe.owner);context_bytes(context)
    require(probe.args.commit==context['android_commit'] and probe.args.tools_commit==context['tools_commit'],
            'OUTCOME_ORIGINAL_SOURCE_REFUSED')
    require(type(manifest) is dict and set(manifest)=={'nonce','fingerprint','original_pid','device','packages'},
            'OUTCOME_ORIGINAL_MANIFEST_REFUSED')
    require(type(manifest['nonce']) is str and str(uuid.UUID(manifest['nonce']))==manifest['nonce']
            and re.fullmatch(r'[0-9a-f]{64}',manifest['fingerprint'])
            and type(manifest['original_pid']) is int and manifest['original_pid']>0,
            'OUTCOME_ORIGINAL_MANIFEST_REFUSED')
    root=Path(probe.output)
    workspace=Path(os.environ['GITHUB_WORKSPACE']).resolve()
    require(root.is_absolute() and root.resolve()==root and root.is_dir() and not root.is_symlink()
            and root.is_relative_to(workspace/'.build'), 'OUTCOME_ORIGINAL_OUTPUT_REFUSED')
    packages=manifest['packages']
    require(type(packages) is list and len(packages)==3 and all(type(row) is dict for row in packages) and packages[0].get('package')==APP
            and packages[0].get('sha256')==probe.binding['sha256'], 'OUTCOME_ORIGINAL_PACKAGE_REFUSED')
    return root


def same_original(probe,manifest):
    require(observe_device(probe)==manifest['device'] and observe_packages(probe)==manifest['packages'],
            'OUTCOME_ORIGINAL_DEVICE_OR_PACKAGE_CHANGED')


def intent_body(context,manifest):
    return {'context':context,'nonce':manifest['nonce'],'fingerprint':manifest['fingerprint'],
            'original_pid':manifest['original_pid'],'APP':APP,'automatic_retry':False,
            'original_APK_sha256':manifest['packages'][0]['sha256'],
            'device':manifest['device'],'packages':manifest['packages']}


def reconcile_original_stop(probe,context,manifest,label):
    """Observe only this original command; a missing reply never creates a stop."""
    root=scope(probe,context,manifest)
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,39}',label),'OUTCOME_ORIGINAL_OBSERVATION_REFUSED')
    path=root/('original-native-stop-observation-'+label+'.json')
    require(not path.exists(),'OUTCOME_ORIGINAL_OBSERVATION_EXISTS')
    intent=root/'original-native-stop-intent.json'
    require(intent.is_file() and not intent.is_symlink() and intent.stat().st_size<=4096
            and json.loads(intent.read_text('utf-8'))==intent_body(context,manifest),'OUTCOME_ORIGINAL_INTENT_CHANGED')
    command_path=root/'original-native-stop-command.json';command=None
    if command_path.exists():
        require(command_path.is_file() and not command_path.is_symlink() and command_path.stat().st_size<=1024,
                'OUTCOME_ORIGINAL_COMMAND_RECORD_REFUSED')
        command=json.loads(command_path.read_text('utf-8'))
        require(command.get('nonce')==manifest['nonce'] and command.get('outcome') in ('confirmed','unknown'),
                'OUTCOME_ORIGINAL_COMMAND_RECORD_REFUSED')
        if command['outcome']=='confirmed':
            require(set(command)=={'nonce','outcome','returncode'} and type(command['returncode']) is int,
                    'OUTCOME_ORIGINAL_COMMAND_RECORD_REFUSED')
        else:
            require(set(command)=={'nonce','outcome','returncode','error_class'} and command['returncode'] is None
                    and command['error_class'] in ('TimeoutError','TimeoutExpired','OSError','RuntimeError','OtherError'),
                    'OUTCOME_ORIGINAL_COMMAND_RECORD_REFUSED')
    same_original(probe,manifest)
    result=probe.shell('pidof',APP,check=False)
    state=probe.device('get-state',check=False)
    connected=state.returncode==0 and state.stdout.strip()==b'device' and not state.stderr.strip()
    code=command['returncode'] if command and command['outcome']=='confirmed' else None
    proof={'original_pid':manifest['original_pid'],'original_pid_before_stop':manifest['original_pid'],
           'force_stop_returncode':code,'original_pidof_returncode':result.returncode,
           'original_pidof_stdout_empty':not result.stdout.strip(),'original_pidof_stderr_empty':not result.stderr.strip(),
           'owned_emulator_still_connected':connected,
           'same_original_immutable_installed_app':True}
    value={'nonce':manifest['nonce'],'command_outcome':command['outcome'] if command else 'unknown',
           'no_stop_or_other_input_sent_by_observation':True,'original_exit_proof':proof,
           'original_absence_observed':result.returncode==1 and not result.stdout.strip() and not result.stderr.strip() and connected,
           'stop_result_not_relabelled':True}
    atomic_json(path,value,exclusive=True)
    return value


def stop_original_once(probe,context,manifest,authorization):
    root=scope(probe,context,manifest)
    expected={'event':'owned-stop-authorized','nonce':manifest['nonce'],
              'original_pid':manifest['original_pid'],'fingerprint':manifest['fingerprint']}
    require(authorization==expected,'OUTCOME_ORIGINAL_STOP_AUTHORIZATION_REFUSED')
    require(not (root/'original-native-stop-intent.json').exists(),'OUTCOME_ORIGINAL_STOP_ALREADY_REQUESTED')
    same_original(probe,manifest)
    before=probe.shell('pidof',APP,check=False)
    require(before.returncode==0 and before.stdout.strip()==str(manifest['original_pid']).encode('ascii') and not before.stderr.strip(),
            'OUTCOME_ORIGINAL_PID_CHANGED')
    atomic_json(root/'original-native-stop-intent.json',intent_body(context,manifest),exclusive=True)
    # Exactly one fixed own-app command. Unknown delivery is preserved; never
    # issue another force-stop merely to obtain a known return code.
    try:
        result=probe.shell('am','force-stop',APP,check=False)
        require(type(result.returncode) is int and not result.stdout.strip() and not result.stderr.strip(),
                'OUTCOME_ORIGINAL_STOP_RESULT_REFUSED')
        command={'nonce':manifest['nonce'],'outcome':'confirmed','returncode':result.returncode}
    except Exception as error:
        command={'nonce':manifest['nonce'],'outcome':'unknown','returncode':None,
                 'error_class':type(error).__name__ if type(error).__name__ in
                 ('TimeoutError','TimeoutExpired','OSError','RuntimeError') else 'OtherError'}
    atomic_json(root/'original-native-stop-command.json',command,exclusive=True)
    return reconcile_original_stop(probe,context,manifest,'first')
