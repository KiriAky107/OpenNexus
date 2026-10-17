"""Minimal packaged Desktop startup for a NEW owned Android interoperability run.

The published installer is extracted, never rebuilt. Only exclusive hosted
Windows runners may start its normal identifier. Pair actions belong to the
driver; startup does not replay old Canvas, experiment or Sync scenarios.
"""
from contextlib import contextmanager
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

from android_desktop_handoff import HandoffError, canonical, context_bytes, require

DESKTOP_SOURCE = '931b96d6c4dab7a6c8f1d9ac4548dd0e51297c30'
INSTALLER_SHA256 = '1f51065722b93627fcc72ac605fb5930e5f995ab2d7dad9b310de88a0251c411'
VERSION = '0.6.0-beta6'
IDENTIFIER = 'cc.kronecker.notesagent'


def owned_root(env, context, *, platform=None):
    context_bytes(context)
    require((os.name if platform is None else platform) == 'nt'
            and env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_OS') == 'Windows'
            and env.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and env.get('GITHUB_REPOSITORY') == 'KiriAky107/OpenNexus'
            and env.get('GITHUB_RUN_ID') == str(context['run_id'])
            and env.get('GITHUB_SHA') == context['tools_commit']
            and context['desktop_commit'] == DESKTOP_SOURCE, 'DESKTOP_PAIR_HOST_REFUSED')
    workspace = Path(env.get('GITHUB_WORKSPACE', ''))
    require(workspace.is_absolute() and workspace.is_dir(), 'DESKTOP_PAIR_WORKSPACE_INVALID')
    workspace = workspace.absolute()
    require(workspace.resolve() == workspace, 'DESKTOP_PAIR_WORKSPACE_INVALID')
    root = workspace / '.build' / 'android-desktop-pair' / str(context['run_id'])
    for path in (root.parent.parent, root.parent, root):
        if path.exists() or path.is_symlink():
            directory_identity(path)
    return root


def directory_identity(path):
    value = path.stat(follow_symlinks=False)
    require(stat.S_ISDIR(value.st_mode) and not getattr(value, 'st_file_attributes', 0) & 0x400,
            'DESKTOP_PAIR_DIRECTORY_INVALID')
    return [value.st_dev, value.st_ino]


def original_marker(root, context):
    """Read a previously claimed run; never adopt a directory from its name."""
    marker = root / 'owner.json'
    info = marker.stat(follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not getattr(info, 'st_file_attributes', 0) & 0x400
            and 0 < info.st_size <= 2048, 'DESKTOP_PAIR_OWNER_INVALID')
    value = json.loads(marker.read_text('utf-8'))
    require(type(value) is dict and set(value) == {'context', 'root_identity'}
            and context_bytes(value['context']) == context_bytes(context)
            and value['root_identity'] == directory_identity(root), 'DESKTOP_PAIR_OWNER_INVALID')
    return value


def claim_root(env, context):
    root = owned_root(env, context)
    root.mkdir(parents=True, exist_ok=False)
    with (root / 'owner.json').open('xb') as stream:
        stream.write(canonical({'context': context, 'root_identity': directory_identity(root)}))
        stream.flush(); os.fsync(stream.fileno())
    return root


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def windows_drive_path(value, *, extended=False):
    """Match the installed Host's canonical drive form for this owned path.

    FreshProfile supplies the test pointer directly. The product's normal
    storage picker canonicalizes before writing that pointer; preserve the
    Windows extended prefix here too, rather than borrowing a user profile or
    accepting a pending restart. UNC/device/relative paths are not test roots.
    """
    raw = str(value).replace('/', '\\')
    if raw.startswith('\\\\?\\'):
        raw = raw[4:]
    require(re.match(r'^[A-Za-z]:\\', raw) is not None
            and not any(part in ('.', '..') for part in raw[3:].split('\\'))
            and not any(char in raw[2:] for char in '\x00:*?"<>|'), 'DESKTOP_PAIR_STORAGE_MISMATCH')
    normalized = ntpath.normpath(raw)
    return '\\\\?\\' + normalized if extended else ntpath.normcase(normalized)


def verify_storage(value, data):
    require(type(value) is dict and set(value) == {
        'active_path', 'default_path', 'configured_path', 'custom', 'restart_required'}, 'DESKTOP_PAIR_STORAGE_MISMATCH')
    expected = windows_drive_path(data)
    require(value['custom'] is True and value['restart_required'] is False
            and all(type(value[key]) is str and windows_drive_path(value[key]) == expected
                    for key in ('active_path', 'configured_path')), 'DESKTOP_PAIR_STORAGE_MISMATCH')


STARTUP_CHECKPOINTS = frozenset({'native-launch', 'webview-attach', 'frontend-bootstrap', 'core-ready',
    'storage-scope', 'core-version', 'workspace-readiness', 'owned-vault-open', 'startup-receipt', 'client-action'})


def safe_failure(error, checkpoint=None):
    # Playwright exception text can include the last fill argument. Never
    # publish that text, its repr or a chained exception after sign-in begins.
    name = type(error).__name__
    result = {'code': 'DESKTOP_PAIR_UI_FAILED', 'error_class': name if name in {
        'TimeoutError', 'Error', 'RuntimeError', 'HandoffError', 'OSError', 'AssertionError'} else 'OtherError',
        'original_exception_text_returned': False}
    if isinstance(error, HandoffError) and str(error) in {
            'DESKTOP_PAIR_STORAGE_MISMATCH', 'DESKTOP_PAIR_CORE_VERSION_MISMATCH',
            'DESKTOP_PAIR_CORE_NOT_READY', 'DESKTOP_PAIR_INITIAL_STATE_INVALID',
            'DESKTOP_PAIR_PICKER_UNAVAILABLE', 'DESKTOP_PAIR_PICKER_TARGET_INVALID',
            'DESKTOP_PAIR_PICKER_INPUT_REFUSED', 'DESKTOP_PAIR_PICKER_SELECTION_FAILED'}:
        result['checkpoint_code'] = str(error)
    if checkpoint is not None:
        result['failed_checkpoint'] = checkpoint if checkpoint in STARTUP_CHECKPOINTS else 'unknown'
    return result


def extract_installer(context, installer):
    root = owned_root(os.environ, context)
    original_marker(root, context)
    require(installer.resolve().is_relative_to(root.resolve()) and installer.is_file()
            and installer.stat().st_size == 105741178 and digest(installer) == INSTALLER_SHA256,
            'DESKTOP_PAIR_INSTALLER_INVALID')
    payload = root / 'payload'
    payload.mkdir(exist_ok=False)
    subprocess.run(['C:/Program Files/7-Zip/7z.exe', 'x', str(installer), '-o' + str(payload), '-y', '-bb0'],
                   stdout=subprocess.DEVNULL, check=True, timeout=120)
    require((payload / 'OpenNexus.exe').is_file() and (payload / 'WebView2Loader.dll').is_file()
            and (payload / 'core').is_dir(), 'DESKTOP_PAIR_PAYLOAD_INVALID')
    receipt = {'installer_sha256': INSTALLER_SHA256, 'source_commit': DESKTOP_SOURCE,
               'host_sha256': digest(payload / 'OpenNexus.exe'), 'loader_sha256': digest(payload / 'WebView2Loader.dll')}
    with (root / 'extraction.json').open('xb') as stream:
        stream.write(canonical(receipt)); stream.flush(); os.fsync(stream.fileno())
    return receipt


@contextmanager
def start_product(context, stage):
    """Start only the exact extracted client, with owned data reused after cold exit."""
    root = owned_root(os.environ, context)
    original_marker(root, context)
    require(stage in (1, 2) and type(stage) is int, 'DESKTOP_PAIR_STAGE_INVALID')
    payload, data, vault = root / 'payload', root / 'data', root / 'vault'
    receipt = json.loads((root / 'extraction.json').read_text('utf-8'))
    require(receipt['source_commit'] == DESKTOP_SOURCE and receipt['installer_sha256'] == INSTALLER_SHA256
            and digest(payload / 'OpenNexus.exe') == receipt['host_sha256']
            and digest(payload / 'WebView2Loader.dll') == receipt['loader_sha256'], 'DESKTOP_PAIR_PAYLOAD_INVALID')
    # Ownership and hosted-run checks precede imports or process/profile access.
    from playwright.sync_api import sync_playwright
    from windows_native_smoke import FreshProfile, preserve_owned_profile, stop, wait_debug
    running = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'OpenNexus.exe' } | Select-Object -ExpandProperty ProcessId"], text=True)
    require(not running.strip(), 'DESKTOP_PAIR_EXISTING_PROCESS_REFUSED')
    attempt = root / ('stage-' + str(stage)); attempt.mkdir(exist_ok=False)
    if stage == 1:
        data.mkdir(); vault.mkdir()
        with (root / 'data-owner.json').open('xb') as stream:
            stream.write(canonical({'data': directory_identity(data), 'vault': directory_identity(vault)}))
            stream.flush(); os.fsync(stream.fileno())
    else:
        previous = json.loads((root / 'stage-1' / 'cleanup.json').read_text('utf-8'))
        require(previous['original_process_exited'] and previous['profile_preserved'], 'DESKTOP_PAIR_PREVIOUS_STAGE_UNKNOWN')
        owner = json.loads((root / 'data-owner.json').read_text('utf-8'))
        require(owner == {'data': directory_identity(data), 'vault': directory_identity(vault)}, 'DESKTOP_PAIR_DATA_CHANGED')
    with (attempt / 'start-intent.json').open('xb') as stream:
        stream.write(canonical({'context': context, 'stage': stage, 'payload': receipt,
                                'own_data_identity': directory_identity(data), 'own_vault_identity': directory_identity(vault)}))
        stream.flush(); os.fsync(stream.fileno())
    profile = FreshProfile(Path(os.environ['APPDATA']), IDENTIFIER, Path(windows_drive_path(data, extended=True)))
    process = None
    checkpoint = 'native-launch'
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = dict(os.environ, PATH=str(Path(os.environ['SystemRoot']) / 'System32'),
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=' + str(port),
                   WEBVIEW2_USER_DATA_FOLDER=str(root / 'webview'))
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
            env.pop(key, None)
        with (attempt / 'native-host.log').open('xb') as log:
            process = subprocess.Popen([str(payload / 'OpenNexus.exe')], cwd=payload, env=env,
                                       stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            checkpoint = 'webview-attach'
            wait_debug('http://127.0.0.1:' + str(port) + '/json/version', process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + str(port))
                page = browser.contexts[0].pages[0]; page.set_default_timeout(60000)
                checkpoint = 'frontend-bootstrap'
                page.wait_for_function('()=>Boolean(window.__TAURI_INTERNALS__ && document.querySelector("#app")?.__vue_app__)')
                page.evaluate('''() => {
                    window.pairInvoke=(name,args={})=>window.__TAURI_INTERNALS__.invoke(name,args);
                    const app=document.querySelector('#app').__vue_app__;
                    window.pairPinia=app.config.globalProperties.$pinia;
                    window.pairRouter=app.config.globalProperties.$router;
                }''')
                checkpoint = 'core-ready'
                until = time.monotonic() + 180
                while not page.evaluate("async()=>Boolean((await pairInvoke('host_capabilities')).core)"):
                    require(time.monotonic() < until, 'DESKTOP_PAIR_CORE_NOT_READY'); time.sleep(.2)
                checkpoint = 'storage-scope'
                storage = page.evaluate("()=>pairInvoke('storage_info')")
                verify_storage(storage, data)
                checkpoint = 'core-version'
                core_version = page.evaluate('''async () => {
                    const requestId=await pairInvoke('core_request_prepare',{timeoutMs:60000});
                    try {
                        const result=await pairInvoke('core_request',{request:{requestId,path:'/api/status',method:'GET',
                            body:null,bodyBase64:null,contentType:'application/json',idempotencyKey:null}});
                        return result.status===200 ? JSON.parse(result.body).version : null;
                    } finally { await pairInvoke('core_request_cancel',{requestId}).catch(()=>{}); }
                }''')
                require(core_version == VERSION, 'DESKTOP_PAIR_CORE_VERSION_MISMATCH')
                checkpoint = 'workspace-readiness'
                page.wait_for_function("()=>Boolean(window.pairPinia?._s.get('workspace')?.openVault && window.pairRouter)")
                profile.remove_pointer()
                checkpoint = 'owned-vault-open'
                if stage == 1:
                    from android_desktop_picker import choose_owned_vault
                    folder_choice = choose_owned_vault(page, context, process, vault, attempt)
                else:
                    page.evaluate("async path=>pairPinia._s.get('workspace').openVault(path)", str(vault))
                    folder_choice = {'reopened_original_native_grant': True, 'recent_grant_database_not_seeded': True}
                page.evaluate("()=>pairRouter.push('/workspace')")
                checkpoint = 'startup-receipt'
                with (attempt / 'startup.json').open('xb') as stream:
                    stream.write(canonical({'source_commit': DESKTOP_SOURCE, 'installer_sha256': INSTALLER_SHA256,
                                            'actual_core_version': core_version, 'private_storage_verified': True,
                                            'native_process_id': process.pid, 'identifier': IDENTIFIER, 'stage': stage,
                                            'owned_folder_choice': folder_choice}))
                    stream.flush(); os.fsync(stream.fileno())
                checkpoint = 'client-action'
                yield page, vault, attempt
    except Exception as error:
        with (attempt / 'failure.json').open('xb') as stream:
            stream.write(canonical(safe_failure(error, checkpoint))); stream.flush(); os.fsync(stream.fileno())
        raise RuntimeError('DESKTOP_PAIR_UI_FAILED') from None
    finally:
        stop(process)
        preserved = preserve_owned_profile(profile, Path(os.environ['APPDATA']) /
            (IDENTIFIER + '.native-smoke-android-pair-' + str(context['run_id']) + '-' + str(stage)))
        with (attempt / 'cleanup.json').open('xb') as stream:
            stream.write(canonical({'original_process_exited': process is None or process.poll() is not None,
                                    'profile_preserved': bool(preserved['preserved']), 'only_original_owned_process': True}))
            stream.flush(); os.fsync(stream.fileno())
