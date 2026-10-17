"""Resume the original hosted Android client; no install, setup or login replay.

This component is not an interoperability proof. The paired controller still
has to compare original provider/native/file bytes before publishing a stage.
All saved anchors stay in the exclusive private provider directory.
"""
import hashlib
import os
from pathlib import Path
import re
import uuid

from android_auth_fixture import SERVICE_SHA, atomic_json, owned_root, read_json
from android_desktop_handoff import context_bytes, require, validate_payload
from android_runtime_probe import APP, DRIVER, TEST_IME, require_hosted_emulator
from android_sync_runtime import SyncProbe


PACKAGES=(APP, DRIVER, TEST_IME)


def canonical_uuid(value):
    try:return type(value) is str and str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def auth_identity(auth, previous=None):
    require(type(auth) is dict and auth.get('integrity')=='ok'
            and auth.get('secret_payload_selected') is False, 'PAIR_ANDROID_AUTH_INVALID')
    rows=auth.get('intents');selection=auth.get('selection')
    require(type(rows) is list and 0<len(rows)<=64 and type(selection) is dict,
            'PAIR_ANDROID_AUTH_INVALID')
    by_id={r.get('operation_id'):r for r in rows if type(r) is dict}
    require(len(by_id)==len(rows) and all(canonical_uuid(x) for x in by_id), 'PAIR_ANDROID_AUTH_INVALID')
    current=by_id.get(selection.get('operation_id'))
    require(current is not None and current.get('phase')=='active'
            and type(selection.get('revision')) is int and selection['revision']>0
            and type(selection.get('generation')) is int and selection['generation']>0
            and all(selection.get(k)==current.get(k) for k in ('endpoint','account','generation')),
            'PAIR_ANDROID_AUTH_NOT_ACTIVE')
    require(re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com/',current.get('endpoint',''))
            and re.fullmatch(r'android-owned-a-[0-9a-f]{12}',current.get('account',''))
            and re.fullmatch(r'[0-9a-f]{32}',current.get('device_id','')),
            'PAIR_ANDROID_AUTH_SCOPE_INVALID')
    # Token rotation is a legitimate continuation. Require its actual original
    # native ancestry, not an unchanged selection that expires after 15 minutes.
    chain=[];node=current
    while True:
        require(node['operation_id'] not in chain and all(node.get(k)==current[k]
                for k in ('endpoint','account','device_id','device_name'))
                and type(node.get('generation')) is int and node['generation']>0
                and node.get('phase')=='active', 'PAIR_ANDROID_AUTH_CHAIN_INVALID')
        chain.append(node['operation_id'])
        if node.get('kind')=='login':
            require(node.get('previous_operation') is None, 'PAIR_ANDROID_AUTH_CHAIN_INVALID')
            break
        require(node.get('kind')=='refresh', 'PAIR_ANDROID_AUTH_CHAIN_INVALID')
        parent=by_id.get(node.get('previous_operation'))
        require(parent is not None and parent.get('generation')==node['generation']-1,
                'PAIR_ANDROID_AUTH_CHAIN_INVALID')
        node=parent
    require(set(chain)==set(by_id), 'PAIR_ANDROID_NEW_LOGIN_OR_UNRELATED_INTENT')
    result={'login_operation_id':node['operation_id'],'device_id':current['device_id'],
            'endpoint':current['endpoint'],'account':current['account'],
            'device_name':current['device_name'],'selection_revision':selection['revision'],
            'selection_generation':selection['generation'],'chain':list(reversed(chain))}
    if previous is not None:
        require(all(result[k]==previous[k] for k in ('login_operation_id','device_id','endpoint','account','device_name'))
                and result['selection_revision']>=previous['selection_revision']
                and result['selection_generation']>=previous['selection_generation']
                and result['chain'][:len(previous['chain'])]==previous['chain'],
                'PAIR_ANDROID_COLD_AUTH_REPLACED')
    return result


def native_identity(native, auth, previous=None):
    require(type(native) is dict and native.get('integrity')=='ok'
            and native.get('credentials_selected') is False, 'PAIR_ANDROID_NATIVE_INVALID')
    binding=native.get('binding');association=native.get('association')
    require(type(binding) is dict and canonical_uuid(binding.get('id'))
            and binding.get('state')=='active' and type(binding.get('cursor')) is int
            and 0<=binding['cursor']<=9007199254740991
            and re.fullmatch(r'[0-9a-f]{32}',binding.get('remote_vault',''))
            and type(association) is dict and association.get('state')=='bound'
            and association.get('remote_vault')==binding['remote_vault']
            and association.get('auth_operation_id')==auth['login_operation_id'],
            'PAIR_ANDROID_BINDING_INVALID')
    result={'binding_id':binding['id'],'remote_vault_id':binding['remote_vault'],
            'cursor':binding['cursor'],'association':association}
    if previous is not None:
        require(all(result[k]==previous[k] for k in ('binding_id','remote_vault_id','association'))
                and result['cursor']>=previous['cursor'], 'PAIR_ANDROID_COLD_BINDING_REPLACED')
    return result


def hosted_scope(context, serial, owner):
    # No local phone/ADB/file access may precede these guards.
    context_bytes(context);require_hosted_emulator(serial)
    require(str(context['run_id'])==os.environ.get('GITHUB_RUN_ID')
            and context['tools_commit']==os.environ.get('GITHUB_SHA')
            and Path(owner).resolve()==owned_root(), 'PAIR_ANDROID_HOST_SCOPE_INVALID')


def observe_packages(probe):
    observed=[]
    for package in PACKAGES:
        lines=probe.shell('pm','path',package).stdout.decode('ascii').splitlines()
        require(len(lines)==1 and lines[0].startswith('package:'), 'PAIR_ANDROID_PACKAGE_INVALID')
        path=lines[0].removeprefix('package:')
        require(re.fullmatch(r'/data/app/[A-Za-z0-9_+=./-]+/base\.apk',path)
                and '..' not in path.split('/'), 'PAIR_ANDROID_PACKAGE_INVALID')
        fields=probe.shell('sha256sum',path).stdout.decode('ascii').split()
        require(len(fields)==2 and fields[1]==path and re.fullmatch(r'[0-9a-f]{64}',fields[0]),
                'PAIR_ANDROID_PACKAGE_INVALID')
        uid_lines=probe.shell('pm','list','packages','-U',package).stdout.decode('ascii').splitlines()
        matches=[m for line in uid_lines if (m:=re.fullmatch(re.escape('package:'+package)+r' uid:([0-9]+)',line))]
        require(len(matches)==1 and int(matches[0][1])>=10000, 'PAIR_ANDROID_PACKAGE_INVALID')
        observed.append({'package':package,'installed_path':path,'sha256':fields[0],'uid':int(matches[0][1])})
    require(len({p['uid'] for p in observed})==3, 'PAIR_ANDROID_PACKAGE_UID_INVALID')
    return observed


def observe_device(probe):
    require(probe.device('get-state').stdout.strip()==b'device'
            and probe.shell('getprop','ro.kernel.qemu').stdout.strip()==b'1'
            and probe.shell('getprop','ro.build.version.sdk').stdout.strip()==b'36',
            'PAIR_ANDROID_DEVICE_INVALID')
    avd=probe.device('emu','avd','name').stdout.decode('ascii').splitlines()[0]
    require(avd=='opennexus-ci-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'],
            'PAIR_ANDROID_AVD_REPLACED')
    boot_id=probe.shell('cat','/proc/sys/kernel/random/boot_id').stdout.decode('ascii').strip()
    require(canonical_uuid(boot_id), 'PAIR_ANDROID_DEVICE_INVALID')
    return {'avd':avd,'boot_id':boot_id}


def capture_anchor(probe, context):
    hosted_scope(context,probe.args.serial,probe.owner)
    require(probe.args.commit==context['android_commit'] and probe.args.tools_commit==context['tools_commit']
            and probe.root is not None, 'PAIR_ANDROID_SOURCE_INVALID')
    device=observe_device(probe);packages=observe_packages(probe)
    expected=read_json(probe.output/'test-ime-packages.json')['packages']
    require(packages[0]['sha256']==probe.binding['sha256']
            and all(any(x['package']==p['package'] and x['sha256']==p['sha256'] and x['uid']==p['uid'] for x in expected)
                    for p in packages[1:]), 'PAIR_ANDROID_ORIGINAL_TOOLS_INVALID')
    auth=auth_identity(probe.snapshot('pair-original-auth'))
    native=native_identity(probe.host_snapshot('pair-original-native'),auth)
    value={'schema':1,'context':context,'serial':probe.args.serial,'activity':probe.activity,
           'native_root':probe.root,'device':device,'packages':packages,'auth':auth,'native':native}
    atomic_json(probe.owner/'pair-resume-anchor.private.json',value,exclusive=True)
    return value


def resume_probe(context,args,apk,binding,owner):
    hosted_scope(context,args.serial,owner)
    anchor=read_json(owner/'pair-resume-anchor.private.json')
    require(anchor.get('schema')==1 and anchor.get('context')==context
            and args.commit==context['android_commit'] and args.tools_commit==context['tools_commit']
            and anchor.get('serial')==args.serial, 'PAIR_ANDROID_RESUME_ORIGINAL_INVALID')
    # Construct only a new evidence/metadata-copy directory. In particular,
    # Probe.run/boot/install/build_driver, Setup and auth login are not called.
    probe=SyncProbe(args,apk,binding,owner)
    require(observe_device(probe)==anchor['device'] and observe_packages(probe)==anchor['packages']
            and binding['sha256']==anchor['packages'][0]['sha256'], 'PAIR_ANDROID_INSTALLED_ORIGINAL_REPLACED')
    activity=probe.shell('cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN',
                         '-c','android.intent.category.LAUNCHER',APP).stdout.decode('ascii').strip().splitlines()[-1]
    require(activity==anchor['activity'] and activity.startswith(APP+'/'), 'PAIR_ANDROID_ACTIVITY_REPLACED')
    probe.activity=activity;probe.discover_private_root()
    require(probe.root==anchor['native_root'], 'PAIR_ANDROID_NATIVE_ROOT_REPLACED')
    auth=auth_identity(probe.snapshot('pair-resumed-auth'),anchor['auth'])
    native_identity(probe.host_snapshot('pair-resumed-native'),auth,anchor['native'])
    return probe,anchor


def read_frozen_fixture(owner,context,serial):
    """Read the same setup receipt even if its independent setup token expired."""
    hosted_scope(context,serial,owner)
    state=read_json(owner/'owner-state.json');device=read_json(owner/'device-private.json')
    vault=read_json(owner/'sync-setup/vault-receipt.json')
    target=read_json(owner/'sync-control/target.json')
    require(state.get('source')==SERVICE_SHA and state.get('result')=='ready' and state.get('fixture')=='sync'
            and device.get('endpoint')==state.get('endpoint')
            and type(device.get('accounts')) is list and len(device['accounts'])==2
            and target.get('schema')==1 and target.get('vault_id')==vault.get('vault_id'),
            'PAIR_ANDROID_ORIGINAL_FIXTURE_CHANGED')
    account=device['accounts'][0]
    payload={'endpoint':state['endpoint']+'/','username':account.get('username'),
             'password':account.get('password'),'vault_id':vault.get('vault_id'),'vault_name':vault.get('name')}
    return validate_payload(payload)


def original_pair_ui_phase(probe,context,phase,*,keep_running=False):
    hosted_scope(context,probe.args.serial,probe.owner)
    require(phase in ('pair-note-reopen','pair-note-return','pair-canvas-reopen','pair-canvas-return'),
            'PAIR_ANDROID_UI_PHASE_INVALID')
    # Persist before the first input, including a read-only open. An unknown
    # edit/Save result must be examined, never replayed with another driver ID.
    atomic_json(probe.output/(phase+'-original-intent.json'),
                {'context':context,'phase':phase,'driver_dispatch_id':str(uuid.uuid4()),
                 'native_operation_id':False,'automatic_retry':False},exclusive=True)
    if phase=='pair-note-return':
        probe.enable_test_ime()
        try:probe.phase(phase,keep_running=keep_running)
        finally:probe.restore_test_ime()
    else:probe.phase(phase,keep_running=keep_running)
