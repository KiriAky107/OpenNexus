"""Run an extracted Host/Core in WebView2 with a system-only PATH and private data.

No installed application or real vault is used. Requires the locked backend
Playwright package, an installed WebView2 Runtime and an interactive Windows
session. This is payload startup validation, not an installation/upgrade matrix.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid


class FreshProfile:
    """Claim a new application profile; never borrow an existing user's profile.

    Keep the owned directory and marker for diagnostics. Only the pointer that
    this instance created may be removed, and only while its identity and bytes
    still match. There is no backup/restore path for a pre-existing pointer.
    """

    def __init__(self, appdata: Path, identifier: str, data: Path):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*){2,}", identifier):
            raise ValueError('Expected a plain reverse-DNS application identifier')
        if len(identifier) > 200 or any(part.casefold() in {'con', 'prn', 'aux', 'nul',
                *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
                for part in identifier.split('.')):
            raise ValueError('Unsafe application identifier')
        root = appdata.resolve(strict=True)
        self.path = root / identifier
        # Atomic mkdir rejects existing files, profiles, links and competing claims.
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise RuntimeError('Application profile already exists; it was left untouched. '
                'Use a dedicated test build with a fresh application identifier.') from error
        self.profile_identity = self.identity(self.path, directory=True)
        self.marker = self.path / '.opennexus-native-smoke-owner'
        self.owner = uuid.uuid4().hex.encode('ascii')
        with self.marker.open('xb') as stream:
            stream.write(self.owner)
        self.marker_identity = self.identity(self.marker)
        self.pointer = self.path / 'storage-location.json'
        self.temporary = json.dumps({'data_root': str(data.resolve(strict=True))}).encode('utf-8')
        with self.pointer.open('xb') as stream:
            stream.write(self.temporary)
        self.pointer_identity = self.identity(self.pointer)
        self.removed = False

    @staticmethod
    def identity(path: Path, directory=False):
        info = path.stat(follow_symlinks=False)
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Test profile contains an unexpected link or object')
        if not directory and info.st_nlink != 1:
            raise RuntimeError('Test profile file has an unexpected hard link')
        return info.st_dev, info.st_ino

    def remove_pointer(self):
        if self.removed:
            return
        if (self.identity(self.path, directory=True) != self.profile_identity
                or self.identity(self.marker) != self.marker_identity
                or self.marker.read_bytes() != self.owner
                or self.identity(self.pointer) != self.pointer_identity
                or self.pointer.read_bytes() != self.temporary):
            raise RuntimeError('Test profile changed concurrently; all current files were preserved')
        self.pointer.unlink()
        self.removed = True


def stop(process):
    if process and process.poll() is None:
        subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        process.wait(timeout=20)


def wait_debug(url, process, timeout=120):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        if process.poll() is not None:
            raise RuntimeError(f'Host exited during startup: {process.returncode:#x}')
        try:
            with urlopen(url,timeout=1) as response:
                return json.load(response)
        except OSError:
            time.sleep(.2)
    raise TimeoutError('WebView2 debug endpoint did not start; inspect native-host.log')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def preserve_owned_profile(profile: FreshProfile, destination: Path):
    """Archive this invocation's profile after exit, without deleting its data."""
    profile.remove_pointer()
    source = profile.path.resolve(strict=True)
    target = destination.absolute()
    # MSIX may map the profile into LocalCache while APPDATA still names Roaming.
    # Only translate the exact parent used by this invocation's fresh claim.
    if target.parent == profile.path.parent.absolute():
        target = source.parent / target.name
    if (source.name != profile.path.name or target.parent.resolve(strict=True) != source.parent
            or not target.name.startswith(source.name + '.native-smoke-') or os.path.lexists(target)
            or profile.identity(source, directory=True) != profile.profile_identity
            or profile.identity(source / profile.marker.name) != profile.marker_identity
            or (source / profile.marker.name).read_bytes() != profile.owner):
        raise RuntimeError('Owned profile archive failed identity or path checks')
    request = {'source': str(source), 'target': str(target), 'root': str(source.parent),
               'marker_sha256': hashlib.sha256(profile.owner).hexdigest()}
    # Use one shell for validation and the directory move, with literal paths.
    command = r'''
$ErrorActionPreference = 'Stop'
$claim = [Console]::In.ReadToEnd() | ConvertFrom-Json
$root = [IO.Path]::GetFullPath($claim.root)
$source = [IO.Path]::GetFullPath($claim.source)
$target = [IO.Path]::GetFullPath($claim.target)
if ([IO.Path]::GetDirectoryName($source) -ne $root -or
    [IO.Path]::GetDirectoryName($target) -ne $root -or
    (Test-Path -LiteralPath $target) -or
    (Test-Path -LiteralPath (Join-Path $source 'storage-location.json'))) { throw 'Unsafe profile move' }
$item = Get-Item -LiteralPath $source
$marker = Join-Path $source '.opennexus-native-smoke-owner'
$hasher = [Security.Cryptography.SHA256]::Create()
try { $markerHash = [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($marker))).Replace('-','').ToLowerInvariant() }
finally { $hasher.Dispose() }
if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    ((Get-Item -LiteralPath $marker).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    $markerHash -ne $claim.marker_sha256) {
    throw 'Profile ownership changed'
}
if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'OpenNexus.exe' }) { throw 'Host still running' }
[IO.Directory]::Move($source, $target)
'''
    moved = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                           input=json.dumps(request), text=True, capture_output=True)
    if moved.returncode:
        raise RuntimeError('Owned profile archive failed; current files were preserved: '+moved.stderr.strip())
    if (source.exists() or profile.identity(target, directory=True) != profile.profile_identity
            or profile.identity(target / profile.marker.name) != profile.marker_identity
            or (target / profile.marker.name).read_bytes() != profile.owner):
        raise RuntimeError('Preserved profile identity changed')
    return {**request, 'preserved': True, 'directory_identity': list(profile.profile_identity)}


def verify(payload:Path, version:str, identifier:str, work:Path, dynamic_loader:bool, *, extra_checks=None, profile_archive=None):
    if os.name != 'nt':
        raise RuntimeError('Native payload startup requires Windows')
    from playwright.sync_api import sync_playwright
    host = payload/'OpenNexus.exe'
    running = subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'OpenNexus.exe' } | Select-Object -ExpandProperty ProcessId"],text=True).strip()
    if running:
        raise RuntimeError('Existing OpenNexus processes were left running; '
            'native validation requires a separate test session')
    work.mkdir(parents=True,exist_ok=True)
    work = work.resolve()
    # These directories are test-owned. The Host must start from the actual payload.
    data, vault = work/'data', work/'vault'
    data.mkdir(); vault.mkdir()
    (vault/'课程').mkdir()
    (vault/'课程/证据.md').write_text('# 启动证据\n\n合成测试笔记。\n',encoding='utf-8')
    (vault/'课程/引用.md').write_text('# 引用\n\n[证据](证据.md)\n\n[缺失](missing.md)\n',encoding='utf-8')
    logo = Path(__file__).resolve().parents[1] / 'frontend/public/branding/png/opennexus-512.png'
    (vault/'标志.png').write_bytes(logo.read_bytes())
    (vault/'研究.canvas').write_text(json.dumps({'nodes':[
        {'id':'text','type':'text','text':'# 启动验证','x':0,'y':0,'width':240,'height':180},
        {'id':'note','type':'file','file':'课程/证据.md','x':320,'y':0,'width':240,'height':180},
        {'id':'image','type':'file','file':'标志.png','x':640,'y':0,'width':120,'height':180}],
        'edges':[{'id':'link','fromNode':'text','toNode':'note','fromSide':'right','toSide':'left'}],
        'custom':{'preserve':'unknown'}},ensure_ascii=False),encoding='utf-8')
    before = {path.relative_to(vault).as_posix():digest(path) for path in vault.rglob('*') if path.is_file()}
    with sqlite3.connect(data/'host-state.sqlite3') as conn:
        conn.execute('CREATE TABLE recent_vaults(path TEXT PRIMARY KEY NOT NULL,vault_id TEXT NOT NULL,name TEXT NOT NULL,ordering INTEGER NOT NULL)')
        conn.execute('INSERT INTO recent_vaults VALUES(?,?,?,1)',(str(vault),str(uuid.uuid4()),vault.name))
    clean_env = dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'))
    for name in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX'):
        clean_env.pop(name,None)
    profile = FreshProfile(Path(os.environ['APPDATA']), identifier, data)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); debug_port = sock.getsockname()[1]
    env = dict(clean_env,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f'--remote-debugging-port={debug_port}',
        WEBVIEW2_USER_DATA_FOLDER=str(work/'webview'))
    process = None
    missing_loader_control = False
    try:
        if dynamic_loader:
            loader, hidden = payload/'WebView2Loader.dll', payload/'WebView2Loader.dll.native-hidden'
            loader.rename(hidden)
            mode = ctypes.windll.kernel32.SetErrorMode(0x8007)
            try:
                process = subprocess.Popen([str(host)],cwd=payload,env=clean_env,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                if process.wait(timeout=20)&0xffffffff != 0xc0000135:
                    raise RuntimeError('Dynamic Loader removal did not fail with 0xC0000135')
                missing_loader_control = True
            finally:
                stop(process); hidden.rename(loader); ctypes.windll.kernel32.SetErrorMode(mode)
        with (work/'native-host.log').open('wb') as log:
            process = subprocess.Popen([str(host)],cwd=payload,env=env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            debug = wait_debug(f'http://127.0.0.1:{debug_port}/json/version',process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{debug_port}')
                page = browser.contexts[0].pages[0]
                page.set_default_timeout(60000)
                page.wait_for_function('() => Boolean(window.__TAURI_INTERNALS__ && document.querySelector("#app")?.__vue_app__)')
                errors = []
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.evaluate('''() => {
                    window.smokeInvoke=(name,args={})=>window.__TAURI_INTERNALS__.invoke(name,args);
                    window.smokeApi=async path=>{
                        const requestId=await smokeInvoke('core_request_prepare',{timeoutMs:60000});
                        try {const response=await smokeInvoke('core_request',{request:{requestId,path,method:'GET',body:null,bodyBase64:null,contentType:'application/json',idempotencyKey:null}});
                            if(response.status>=400)throw new Error('Core response '+response.status);return JSON.parse(response.body);
                        }finally{await smokeInvoke('core_request_cancel',{requestId}).catch(()=>{});}
                    };
                    const app=document.querySelector('#app').__vue_app__;
                    window.smokePinia=app.config.globalProperties.$pinia;
                    window.smokeRouter=app.config.globalProperties.$router;
                }''')
                until = time.monotonic()+180
                while not page.evaluate("async()=> (await smokeInvoke('host_capabilities')).core"):
                    if time.monotonic()>=until: raise TimeoutError('Packaged Core did not start')
                    time.sleep(.2)
                storage = page.evaluate("()=>smokeInvoke('storage_info')")
                experiments = page.evaluate("()=>smokeInvoke('host_capabilities')").get('experiments', {})
                if not experiments.get('runtime_available') or experiments.get('enabled') is not True:
                    raise RuntimeError('Host did not discover the verified packaged experiment runtime')
                if str(data).casefold().replace('/','\\') not in json.dumps(storage).casefold().replace('\\\\','\\'):
                    raise RuntimeError('Host did not use private test storage')
                profile.remove_pointer()
                if page.evaluate("()=>smokeApi('/api/status')")['version'] != version:
                    raise RuntimeError('Packaged Core version does not match manifest')
                page.evaluate("async path=>{await smokePinia._s.get('workspace').openVault(path);await smokeRouter.push('/workspace')}",str(vault))
                page.wait_for_function("() => smokePinia._s.has('editor')")
                page.evaluate("async()=>{await smokePinia._s.get('editor').loadFile('/研究.canvas');smokePinia._s.get('workspace').openFile('/研究.canvas')}")
                page.locator('.canvas-node').first.wait_for()
                if page.locator('.canvas-node').count()!=3: raise RuntimeError('Canvas did not render all test nodes')
                page.get_by_label('显示右侧栏',exact=True).click();page.locator('.canvas-inspector').wait_for()
                page.get_by_label('收起右侧栏',exact=True).click()
                page.get_by_role('button',name='反向链接',exact=True).click()
                dialog=page.get_by_role('dialog',name='反向链接与失效链接',exact=True)
                dialog.wait_for();dialog.press('Escape')
                if errors: raise RuntimeError('Native UI errors: '+str(errors))
                page.screenshot(path=str(work/'native-canvas.png'))
                try:
                    extra = extra_checks(page, process, work, vault) if extra_checks else None
                except Exception as error:
                    # Preserve the actual failing UI before Playwright closes
                    # its connection; diagnostics contain this synthetic vault.
                    try:
                        page.screenshot(path=str(work/'native-ui-failure.png'), timeout=5000)
                        (work/'native-ui-failure.txt').write_text(page.locator('#app').inner_text(), encoding='utf-8')
                        state = page.evaluate("()=>({url:location.href,route:smokeRouter.currentRoute.value.name,editor_path:smokePinia._s.get('editor').currentFilePath,search:window.nativeSearchDiagnosis ?? null,alerts:Array.from(document.querySelectorAll('[role=alert]'),e=>e.textContent)})")
                        state['error_type'] = type(error).__name__
                        (work/'native-ui-failure.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n','utf-8')
                    except Exception:
                        pass
                    raise
                if errors: raise RuntimeError('Native UI errors: '+str(errors))
                page.get_by_label('关闭窗口',exact=True).click();process.wait(timeout=30)
        if any(digest(vault/name)!=sha for name,sha in before.items()):
            raise RuntimeError('Startup smoke modified test document bytes')
        return {'passed':True,'version':version,'host_sha256':digest(host),
            'os':platform.win32_ver(),'os_build':sys.getwindowsversion().build,'architecture':platform.machine(),
            'webview':debug.get('Browser'),'sdk_paths_removed':True,'private_storage_verified':True,
            'fresh_application_profile':True,'existing_profile_touched':False,
            'storage_pointer_removed':profile.removed,'vault_documents_unchanged':True,
            'packaged_experiment_runtime':experiments,
            'additional_checks':extra,
            'missing_loader_negative_control':missing_loader_control if dynamic_loader else 'not applicable: static Loader',
            'scope':'Extracted payload startup and exit; no installer/upgrade/uninstall or missing-Runtime validation'}
    finally:
        stop(process);profile.remove_pointer()
        if profile_archive is not None:
            preserved = preserve_owned_profile(profile, Path(profile_archive))
            (work/'native-profile-preserved.json').write_text(json.dumps(preserved, indent=2)+'\n', 'utf-8')
