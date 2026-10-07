"""Verify two private native desktop peers against the real HTTPS Sync service.

The accounts, files, certificates and profiles belong to this driver. The
service uses test SQLite/DiskObjects; this is not a production storage benchmark.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from check_community_tls import authority, certificate
from cryptography.hazmat.primitives import serialization
from windows_native_smoke import digest, verify


SOURCE = 'experiments/native-sync/课程 #%.py'
COPY = 'experiments/native-sync/本地副本.py'
REMOTE_SOURCE = ('# 中文源文件\r\nfrom pathlib import Path\r\n'
                 "Path('AUTO-RUN-MUST-NOT-EXIST').write_text('ran')\r\n").encode()
LOCAL_SOURCE = b'# preserved offline source\r\nprint("LOCAL_ONLY")\r\n'
FILES = {
    SOURCE: REMOTE_SOURCE,
    'experiments/native-sync/inputs/data.json': '{"说明":"中文 #%","value":6}\r\n'.encode(),
    'experiments/native-sync/inputs/表格.csv': '名称,值\r\n课程,6\r\n'.encode(),
    '课程/同步入口.md': '# 同步入口\r\n\r\n[源文件](../experiments/native-sync/课程%20%23%25.py)\r\n'.encode(),
    'attachments/同步成果/报告.md': '# 保留成果\r\n\r\n总计：6\r\n'.encode(),
}


class NativeSyncServer:
    def __init__(self, work: Path):
        repo = Path(__file__).resolve().parents[1]
        service = Path(os.environ['OPENNEXUS_SYNC_SERVER_DIR']).resolve(strict=True)
        allowed = {(repo.parent/'Sync-for-OpenNexus').resolve(), (repo/'.build/sync-server').resolve()}
        if service not in allowed or not work.resolve().is_relative_to((repo/'.build').resolve()):
            raise RuntimeError('Use the fixed Sync checkout and owned build workspace')
        self.service_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=service, text=True).strip()
        self.root = work/'sync-source'
        self.root.mkdir()
        (self.root/'.opennexus-test').write_text(uuid.uuid4().hex, 'ascii')
        ca = authority('OpenNexus owned native Sync')
        self.ca_path = self.root/'ca.pem'
        self.ca_path.write_bytes(ca[1].public_bytes(serialization.Encoding.PEM))
        certificate(self.root, 'leaf', ca)
        self.log = (work/'sync-source.log').open('wb')
        self.process = subprocess.Popen([
            str(service/'.venv/Scripts/python.exe'), str(repo/'scripts/fixtures/sync_native_server.py'), str(self.root),
        ], cwd=service, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()

        def receive():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({'unexpected_stdout': line.decode('utf-8', 'replace')})
            self.messages.put({'process_exit': self.process.wait()})

        threading.Thread(target=receive, daemon=True).start()
        try:
            self.ready = self.messages.get(timeout=30)
            if not self.ready.get('url', '').startswith('https://127.0.0.1:'):
                raise RuntimeError('Owned service did not start with HTTPS')
            if Path(self.ready['ca_path']).resolve() != self.ca_path.resolve():
                raise RuntimeError('Owned service returned another certificate')
        except BaseException:
            self.close()
            (work/'sync-source-startup-error.json').write_text(json.dumps({
                'owned_service_stopped': self.stopped, 'exit_code': self.process.returncode,
                'pid': self.process.pid, 'native_profile_not_created': True})+'\n', 'utf-8')
            raise

    def control(self, action, **fields):
        self.process.stdin.write((json.dumps({'action': action, **fields})+'\n').encode())
        self.process.stdin.flush()
        response = self.messages.get(timeout=15)
        if response.get('action') != action:
            (self.root.parent/'sync-source-control-error.json').write_text(
                json.dumps({'expected': action, 'response': response}, ensure_ascii=False, indent=2)+'\n', 'utf-8')
            raise RuntimeError('Owned control response differs; inspect sync-source-control-error.json')
        return response

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.log.close()
        self.stopped = self.process.poll() is not None


def status(page):
    return page.evaluate("()=>smokeInvoke('sync_status')")


def wait_status(page, condition, timeout=60):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        current = status(page)
        if condition(current):
            return current
        time.sleep(.2)
    raise TimeoutError('Native sync state did not converge: '+json.dumps(current, ensure_ascii=False))


def confirm(page):
    page.locator('.action-dialog').get_by_role('button', name='确定', exact=True).click()


def seed(page, vault: Path, *, offline=False):
    for path, content in FILES.items():
        if offline and path != SOURCE:
            continue
        target = vault/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(LOCAL_SOURCE if offline and path == SOURCE else content)
    page.evaluate("()=>smokePinia._s.get('workspace').refreshFileTree()")


def login(page, server, device):
    page.evaluate("()=>smokeRouter.push('/settings')")
    page.locator('.settings-nav').get_by_role('button', name='数据与凭据', exact=True).click()
    page.get_by_text('设备凭据保险库', exact=True).first.click()
    credentials = page.evaluate("()=>smokeInvoke('credentials_status')")
    if credentials['locked']:
        panel = page.locator('.credential-vault')
        panel.get_by_label('解锁口令', exact=True).fill('owned-native-sync-only-phrase')
        panel.get_by_role('button', name='解锁', exact=True).click()
        page.wait_for_function("async()=>!(await smokeInvoke('credentials_status')).locked")
    page.locator('.settings-nav').get_by_role('button', name='Sync', exact=True).click()
    panel = page.locator('.sync-settings')
    panel.get_by_label('服务器地址', exact=True).fill(server.ready['url'])
    panel.get_by_label('账户', exact=True).fill('native-fixture')
    panel.get_by_label('密码', exact=True).fill('controlled-fixture-password')
    panel.get_by_label('设备名称', exact=True).fill(device)
    if panel.locator('.test-http input').is_checked():
        raise RuntimeError('Native flow unexpectedly enabled plaintext HTTP')
    panel.get_by_role('button', name='登录', exact=True).click()
    panel.get_by_role('button', name='刷新远端库', exact=True).wait_for()
    if panel.get_by_label('密码', exact=True).input_value():
        raise RuntimeError('Sign-in did not clear the password field')
    return panel


def no_execution(page, vault):
    history = page.evaluate("()=>smokeInvoke('experiment_request',{request:{vault_id:smokePinia._s.get('workspace').vaultId,action:{kind:'history',limit:10,cursor:null}}})")
    if history['items'] or list(vault.rglob('AUTO-RUN-MUST-NOT-EXIST')):
        raise RuntimeError('Sync executed experiment source')
    return {'run_history_empty': True, 'execution_marker_absent': True}


def exercise_pair(payload: Path, version: str, identifier: str, work: Path):
    work.mkdir()
    server = NativeSyncServer(work)
    previous_ca = os.environ.get('SSL_CERT_FILE')
    os.environ['SSL_CERT_FILE'] = str(server.ca_path)
    peer_results = []
    peer_work = [work/'peer-a', work/'peer-b']
    remote = {}

    def archive_path(peer):
        # A new profile is claimed on every invocation; preserve it between peers.
        return Path(os.environ['APPDATA'])/(identifier+'.native-smoke-'+work.name+'-'+peer)

    def first(page, process, owned, vault):
        print('NATIVE_SYNC_PEER_A_LOGIN', flush=True)
        seed(page, vault)
        panel = login(page, server, 'Native peer A')
        panel.get_by_placeholder('新远端库名称', exact=True).fill('Native experiment peers')
        panel.get_by_role('button', name='创建远端库', exact=True).click()
        page.wait_for_function("()=>Boolean(document.querySelector('.sync-settings select')?.value)")
        remote['id'] = panel.locator('select').first.input_value()
        panel.get_by_role('button', name='上传到空远端', exact=True).click()
        confirm(page)
        wait_status(page, lambda s: bool(s['binding']))
        print('NATIVE_SYNC_PEER_A_BOUND', flush=True)
        panel.get_by_role('button', name='立即同步', exact=True).click()
        completed = wait_status(page, lambda s: bool(s.get('last_cycle_success_at')) and not s['running'] and s['pending'] == 0)
        state = server.control('state')
        for path, content in FILES.items():
            matches = [r for r in state['revisions'] if r['path'] == path]
            if len(matches) != 1 or matches[0]['hash'] != hashlib.sha256(content).hexdigest():
                raise RuntimeError('First peer failed to commit the exact source: '+path)
        panel.get_by_role('button', name='暂停同步', exact=True).click()
        wait_status(page, lambda s: s['paused'])
        page.screenshot(path=str(owned/'native-sync-upload.png'))
        print('NATIVE_SYNC_PEER_A_UPLOADED', flush=True)
        return {'uploaded_files': len(FILES), 'status': completed, 'no_execution': no_execution(page, vault), 'server': state}

    def second(page, process, owned, vault):
        print('NATIVE_SYNC_PEER_B_LOGIN', flush=True)
        seed(page, vault, offline=True)
        panel = login(page, server, 'Native peer B')
        panel.locator('select').first.select_option(remote['id'])
        panel.get_by_role('button', name='预览合并', exact=True).click()
        panel.get_by_role('button', name='确认合并并绑定', exact=True).wait_for()
        if (vault/SOURCE).read_bytes() != LOCAL_SOURCE or status(page)['binding']:
            raise RuntimeError('Merge preview changed local bytes or created a binding')
        panel.get_by_role('button', name='确认合并并绑定', exact=True).click()
        confirm(page)
        wait_status(page, lambda s: bool(s['binding']) and any(c['local_path'] == SOURCE for c in s['conflicts']))
        panel.get_by_role('button', name='立即同步', exact=True).click()
        wait_status(page, lambda s: bool(s.get('last_cycle_success_at')) and not s['running'])
        print('NATIVE_SYNC_PEER_B_MERGED', flush=True)
        if (vault/SOURCE).read_bytes() != LOCAL_SOURCE:
            raise RuntimeError('Sync overwrote the unresolved local source')
        panel.get_by_role('button', name='暂停同步', exact=True).click()
        wait_status(page, lambda s: s['paused'] and not s['running'])
        review = panel.locator('.sync-conflict').filter(has=page.locator('h3', has_text=SOURCE))
        review.get_by_role('button', name='读取正文与差异', exact=True).click()
        review.get_by_label('副本相对路径', exact=True).fill(COPY)
        review.get_by_role('button', name='另存本地副本并采用远端', exact=True).click()
        confirm(page)
        wait_status(page, lambda s: not any(c['local_path'] == SOURCE for c in s['conflicts']))
        for path, content in FILES.items():
            if (vault/path).read_bytes() != content:
                raise RuntimeError('Second peer failed byte verification: '+path)
        if (vault/COPY).read_bytes() != LOCAL_SOURCE:
            raise RuntimeError('Reviewed conflict failed to preserve the local source')
        page.screenshot(path=str(owned/'native-sync-reviewed-conflict.png'))
        print('NATIVE_SYNC_PEER_B_REVIEWED_CONFLICT', flush=True)
        no_run = no_execution(page, vault)
        # No backend mock: a real service returns 503 + Retry-After on changes.
        server.control('fault', enabled=True)
        panel.get_by_role('button', name='继续同步', exact=True).click()
        wait_status(page, lambda s: not s['paused'])
        panel.get_by_role('button', name='立即同步', exact=True).click()
        failed = wait_status(page, lambda s: s['failures'] >= 1 and not s['running'] and (s.get('retry_in') or 0) > 0)
        panel.get_by_text('自动重试等待', exact=False).wait_for()
        page.screenshot(path=str(owned/'native-sync-retry.png'))
        server.control('fault', enabled=False)
        succeeded = wait_status(page, lambda s: s['failures'] == 0 and not s['running'] and s['pending'] == 0, timeout=40)
        page.wait_for_function("""() => /待上传\\s*0/.test(document.querySelector('.sync-overview')?.textContent ?? '')
            && document.querySelector('.sync-activity p[role=status]')?.textContent === '本轮已完成'
            && !document.querySelector('.sync-settings > .error-banner')""", timeout=20000, polling=100)
        print('NATIVE_SYNC_PEER_B_RETRY_RECOVERED', flush=True)
        account = panel.locator('.sync-account')
        account.get_by_role('button', name='刷新设备与用量', exact=True).click()
        device = account.locator('li').filter(has_text='Native peer A')
        device.get_by_role('button', name='撤销访问', exact=True).click()
        confirm(page)
        device.get_by_text('已撤销', exact=False).wait_for()
        final = server.control('state')
        devices = {d['name']: d for d in final['devices']}
        if len(devices) != 2 or not devices['Native peer A']['revoked'] or devices['Native peer B']['revoked']:
            raise RuntimeError('Native device revocation differs from real service state')
        if not final['requests']['changes_503']:
            raise RuntimeError('The outage was never exercised')
        if 'HTTPS / TLS' not in account.inner_text():
            raise RuntimeError('Native account view did not show verified HTTPS transport')
        page.screenshot(path=str(owned/'native-sync-devices.png'))
        return {'verified_files': len(FILES), 'reviewed_copy_sha256': digest(vault/COPY),
                'preview_zero_writes': True, 'retry_state': failed, 'recovered_state': succeeded,
                'no_execution': no_run, 'recovery_ui_converged': True, 'server': final}

    try:
        for name, own, checks in zip(('a', 'b'), peer_work, (first, second)):
            result = verify(payload, version, identifier, own, False,
                            extra_checks=checks, profile_archive=archive_path(name))
            (own/'native-sync-peer-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', 'utf-8')
            peer_results.append(result)
        roots = [str((own/'data').resolve()) for own in peer_work]
        if len(set(roots)) != 2:
            raise RuntimeError('Native peer data roots were not distinct')
        return {'passed': True, 'native_desktop_peers': 2, 'private_data_roots': roots,
                'service_commit': server.service_commit, 'service_storage': server.ready['storage'],
                'transport': 'HTTPS with process-local owned CA; plaintext flag disabled',
                'peers': peer_results,
                'scope': 'Sequential native UI peers; no production storage or concurrent memory/bandwidth benchmark'}
    finally:
        if previous_ca is None:
            os.environ.pop('SSL_CERT_FILE', None)
        else:
            os.environ['SSL_CERT_FILE'] = previous_ca
        server.close()
        (work/'native-sync-cleanup.json').write_text(json.dumps({
            'owned_service_stopped': server.stopped, 'exit_code': server.process.returncode,
            'pid': server.process.pid})+'\n', 'utf-8')
