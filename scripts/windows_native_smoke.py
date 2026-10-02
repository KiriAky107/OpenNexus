"""Run an extracted Host/Core in WebView2 with a system-only PATH and private data.

No installed application or real vault is used. Requires the locked backend
Playwright package, an installed WebView2 Runtime and an interactive Windows
session. This is payload startup validation, not an installation/upgrade matrix.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid


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


def verify(payload:Path, version:str, identifier:str, work:Path, dynamic_loader:bool):
    if os.name != 'nt':
        raise RuntimeError('Native payload startup requires Windows')
    from playwright.sync_api import sync_playwright
    host = payload/'OpenNexus.exe'
    running = subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'OpenNexus.exe' } | Select-Object -ExpandProperty ProcessId"],text=True).strip()
    if running:
        raise RuntimeError('Close existing OpenNexus processes before isolated native validation')
    work.mkdir(parents=True,exist_ok=True)
    work = work.resolve()
    # These directories are test-owned. The Host must start from the actual payload.
    data, vault = work/'data', work/'vault'
    data.mkdir(); vault.mkdir()
    (vault/'课程').mkdir()
    (vault/'课程/证据.md').write_text('# 启动证据\n\n合成测试笔记。\n',encoding='utf-8')
    (vault/'课程/引用.md').write_text('# 引用\n\n[证据](证据.md)\n\n[缺失](missing.md)\n',encoding='utf-8')
    (vault/'标志.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
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
    missing_loader_control = False
    if dynamic_loader:
        loader, hidden = payload/'WebView2Loader.dll', payload/'WebView2Loader.dll.native-hidden'
        loader.rename(hidden)
        process = None
        mode = ctypes.windll.kernel32.SetErrorMode(0x8007)
        try:
            process = subprocess.Popen([str(host)],cwd=payload,env=clean_env,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            if process.wait(timeout=20)&0xffffffff != 0xc0000135:
                raise RuntimeError('Dynamic Loader removal did not fail with 0xC0000135')
            missing_loader_control = True
        finally:
            stop(process); hidden.rename(loader); ctypes.windll.kernel32.SetErrorMode(mode)
    pointer = Path(os.environ['APPDATA'])/identifier/'storage-location.json'
    pointer.parent.mkdir(parents=True,exist_ok=True)
    original = pointer.read_bytes() if pointer.exists() else None
    temporary = json.dumps({'data_root':str(data)}).encode('utf-8')
    def restore():
        current = pointer.read_bytes() if pointer.exists() else None
        if current == temporary:
            if original is None: pointer.unlink()
            else: pointer.write_bytes(original)
        elif current != original:
            raise RuntimeError('Storage pointer changed concurrently; it was preserved for manual review')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); debug_port = sock.getsockname()[1]
    env = dict(clean_env,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f'--remote-debugging-port={debug_port}',
        WEBVIEW2_USER_DATA_FOLDER=str(work/'webview'))
    process = None
    pointer.write_bytes(temporary)
    try:
        with (work/'native-host.log').open('wb') as log:
            process = subprocess.Popen([str(host)],cwd=payload,env=env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            debug = wait_debug(f'http://127.0.0.1:{debug_port}/json/version',process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{debug_port}')
                page = browser.contexts[0].pages[0]
                page.set_default_timeout(60000)
                page.wait_for_function('Boolean(window.__TAURI_INTERNALS__ && document.querySelector("#app")?.__vue_app__)')
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
                if str(data).casefold().replace('/','\\') not in json.dumps(storage).casefold().replace('\\\\','\\'):
                    raise RuntimeError('Host did not use private test storage')
                restore()
                if page.evaluate("()=>smokeApi('/api/status')")['version'] != version:
                    raise RuntimeError('Packaged Core version does not match manifest')
                page.evaluate("async path=>{await smokePinia._s.get('workspace').openVault(path);await smokeRouter.push('/workspace')}",str(vault))
                page.wait_for_function("smokePinia._s.has('editor')")
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
                page.get_by_label('关闭窗口',exact=True).click();process.wait(timeout=30)
        if any(digest(vault/name)!=sha for name,sha in before.items()):
            raise RuntimeError('Startup smoke modified test document bytes')
        return {'passed':True,'version':version,'host_sha256':digest(host),
            'os':platform.win32_ver(),'os_build':sys.getwindowsversion().build,'architecture':platform.machine(),
            'webview':debug.get('Browser'),'sdk_paths_removed':True,'private_storage_verified':True,
            'storage_pointer_restored':True,'vault_documents_unchanged':True,
            'missing_loader_negative_control':missing_loader_control if dynamic_loader else 'not applicable: static Loader',
            'scope':'Extracted payload startup and exit; no installer/upgrade/uninstall or missing-Runtime validation'}
    finally:
        stop(process);restore()
