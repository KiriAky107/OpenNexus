"""Native quota recovery, session renewal and concurrent-cycle observation.

Uses only the verifier's fresh vault, credentials, profile and HTTPS fixture.
Memory observations cover the owned Host and renderer JS heap, not total system
or service memory. The delayed HTTPS fixture models latency, not a WAN link.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import time

from windows_native_smoke import verify
from windows_sync_smoke import NativeSyncServer, confirm, login, no_execution, status, wait_status


def observe_host(process):
    spec = importlib.util.spec_from_file_location('owned_host_memory', Path(__file__).with_name('measure-sync-memory.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monitor, stop = module.Monitor(), threading.Event()
    handle = monitor.kernel.OpenProcess(0x1000 | 0x0010, False, process.pid)
    if not handle or process.poll() is not None:
        if handle:
            monitor.kernel.CloseHandle(handle)
        raise RuntimeError('OWNED_LIVE_HOST_MEMORY_REQUIRED')
    state = {'samples': 0, 'errors': 0, 'peak_working_set_bytes': 0, 'peak_private_bytes': 0,
        'baseline_working_set_bytes': None, 'baseline_private_bytes': None, 'phases': []}

    def sample():
        try:
            while not stop.is_set() and process.poll() is None:
                counters = module.Counters(); counters.cb = ctypes.sizeof(counters)
                if monitor.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    if not state['samples']:
                        state['baseline_working_set_bytes'] = counters.WorkingSetSize
                        state['baseline_private_bytes'] = counters.PrivateUsage
                    state['samples'] += 1
                    state['peak_working_set_bytes'] = max(state['peak_working_set_bytes'], counters.PeakWorkingSetSize)
                    state['peak_private_bytes'] = max(state['peak_private_bytes'], counters.PrivateUsage)
                else:
                    state['errors'] += 1
                stop.wait(.05)
        finally:
            monitor.kernel.CloseHandle(handle)

    thread = threading.Thread(target=sample, daemon=True); thread.start()

    def finish():
        stop.set(); thread.join(timeout=5)
        if thread.is_alive() or not state['samples']:
            raise RuntimeError('OWNED_HOST_MEMORY_OBSERVATION_FAILED')
        return dict(state, peak_private_above_baseline_bytes=state['peak_private_bytes']-state['baseline_private_bytes'],
            final_private_above_baseline_bytes=state['phases'][-1]['private_bytes']-state['baseline_private_bytes'],
            scope='Owned Host process only; baseline after packaged startup, OS peak working set includes startup, private bytes sampled every 50ms')
    def mark(phase):
        counters = module.Counters(); counters.cb = ctypes.sizeof(counters)
        if not monitor.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise RuntimeError('OWNED_HOST_MEMORY_PHASE_FAILED')
        state['phases'].append({'phase': phase, 'private_bytes': counters.PrivateUsage,
            'working_set_bytes': counters.WorkingSetSize, 'os_peak_working_set_bytes': counters.PeakWorkingSetSize})
    finish.mark = mark
    return finish


def exercise(payload: Path, version: str, identifier: str, work: Path):
    work.mkdir()
    server = NativeSyncServer(work)
    previous_ca = os.environ.get('SSL_CERT_FILE')
    os.environ['SSL_CERT_FILE'] = str(server.ca_path)

    def checks(page, process, owned, vault):
        finish_memory = observe_host(process)
        try:
            files = {
                'experiments/resources/data.csv': ('name,value\r\n课程 #%,6\r\n' * 16_000).encode(),
                'attachments/resources/report.md': ('# 保留成果\r\n中文 #%\r\n' * 16_000).encode(),
            }
            for path, content in files.items():
                target = vault/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
            page.evaluate("()=>smokePinia._s.get('workspace').refreshFileTree()")
            finish_memory.mark('files_discovered')
            panel = login(page, server, 'Native resource peer')
            finish_memory.mark('credential_unlock_and_login')
            panel.get_by_placeholder('新远端库名称', exact=True).fill('Native quota and latency')
            panel.get_by_role('button', name='创建远端库', exact=True).click()
            page.wait_for_function("()=>Boolean(document.querySelector('.sync-settings select')?.value)")
            remote = panel.locator('select').first.input_value()
            server.control('quota', vault_id=remote, bytes=0)
            panel.get_by_role('button', name='上传到空远端', exact=True).click(); confirm(page)
            wait_status(page, lambda s: bool(s['binding']))
            if not status(page)['halted']:
                panel.get_by_role('button', name='立即同步', exact=True).click()
            failed = wait_status(page, lambda s: s['halted'] and not s['running'] and s['pending'] > 0)
            if failed['error'] != 'QUOTA_EXCEEDED':
                raise RuntimeError('The real quota failure was not preserved: '+json.dumps(failed))
            before = server.control('state')
            finish_memory.mark('quota_halted')
            if before['revisions'] or any(item['used'] for item in before['vaults']):
                raise RuntimeError('Quota failure committed remote bytes')
            page.screenshot(path=str(owned/'native-sync-quota.png'))
            server.control('quota', vault_id=remote, bytes=32*1024**2)
            server.control('latency', seconds=.2)
            expired = server.control('expire_access')
            if expired['expired_sessions'] != 1:
                raise RuntimeError('Unexpected fixture session count')
            start = time.perf_counter()
            # Exercise the actual main-window IPC gate; UI's busy state itself
            # prevents this many simultaneous clicks.
            cycles = page.evaluate("async()=>Promise.all(Array.from({length:8},()=>smokeInvoke('sync_run').then(()=>({ok:true}),error=>({ok:false,error:String(error)}))))")
            elapsed = time.perf_counter()-start
            if sum(item['ok'] for item in cycles) != 1 or any(not item['ok'] and item['error'] != 'SYNC_BUSY' for item in cycles):
                raise RuntimeError('Concurrent native cycles were not bounded: '+json.dumps(cycles))
            complete = wait_status(page, lambda s: not s['halted'] and not s['running'] and s['pending'] == 0 and not s['error'])
            finish_memory.mark('session_refresh_and_upload_complete')
            page.wait_for_function("()=>!document.querySelector('.sync-settings > .error-banner') && /待上传\\s*0/.test(document.querySelector('.sync-overview')?.textContent ?? '')")
            after = server.control('state')
            for path, content in files.items():
                matches = [item for item in after['revisions'] if item['path'] == path]
                if len(matches) != 1 or matches[0]['hash'] != hashlib.sha256(content).hexdigest() or (vault/path).read_bytes() != content:
                    raise RuntimeError('Native resource upload changed or duplicated a file: '+path)
            if after['requests']['refresh_success'] != 1 or after['requests']['max_active_upload_requests'] != 1:
                raise RuntimeError('Session rotation or upload concurrency differs from real service observations')
            account = panel.locator('.sync-account')
            account.get_by_role('button', name='刷新设备与用量', exact=True).click()
            page.screenshot(path=str(owned/'native-sync-quota-recovered.png'))
            heap = page.evaluate('()=>performance.memory?.usedJSHeapSize ?? null')
            result = {'passed': True, 'quota_failed_state': failed, 'recovered_state': complete, 'concurrent_calls': cycles,
                'service': after, 'elapsed_seconds': elapsed, 'source_bytes': sum(map(len, files.values())),
                'observed_upload_body_bytes': after['requests']['upload_body_bytes'], 'renderer_js_heap_bytes': heap,
                'no_execution': no_execution(page, vault), 'memory': finish_memory(),
                'scope': 'One native desktop, real quota 507, real access expiry / refresh, eight main-window RPC calls, 200ms HTTPS request latency; no OS sleep or production storage'}
            finish_memory = None
            (owned/'native-sync-resources.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', 'utf-8')
            return result
        finally:
            if finish_memory:
                finish_memory()

    try:
        result = verify(payload, version, identifier, work/'peer', False, extra_checks=checks,
            profile_archive=Path(os.environ['APPDATA'])/(identifier+'.native-smoke-'+work.name))
        result['sync_service_commit'] = server.service_commit
        return result
    finally:
        if previous_ca is None:
            os.environ.pop('SSL_CERT_FILE', None)
        else:
            os.environ['SSL_CERT_FILE'] = previous_ca
        server.close()
        (work/'native-sync-resources-cleanup.json').write_text(json.dumps({'owned_service_stopped':server.stopped,
            'exit_code':server.process.returncode,'pid':server.process.pid})+'\n', 'utf-8')
