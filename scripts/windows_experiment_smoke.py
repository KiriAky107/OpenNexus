"""Exercise experiments in the owned payload WebView, without model API calls.

Only dialogs belonging to the Popen created by windows_native_smoke are clicked.
The expected title and source/output hash must be visible in that native dialog.
The synthetic vault is inside that verifier's private work directory.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import time


def answer_dialog(process, title: str, evidence: str, yes: bool):
    if os.name != 'nt' or not evidence or process.poll() is not None:
        raise RuntimeError('Native confirmation requires a live, owned Windows Host')
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetDlgItem.restype = wintypes.HWND
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = ctypes.c_ssize_t

    def owned(window):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        return pid.value == process.pid and process.poll() is None

    def text(window):
        value = ctypes.create_unicode_buffer(32768)
        user.GetWindowTextW(window, value, len(value))
        return value.value

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        matches = []
        @callback
        def inspect(window, _):
            kind = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(window, kind, len(kind))
            if owned(window) and kind.value == '#32770' and text(window) == title:
                content = []
                @callback
                def child(handle, _):
                    if owned(handle): content.append(text(handle))
                    return True
                user.EnumChildWindows(window, child, 0)
                if evidence in '\n'.join(content): matches.append(window)
            return True
        user.EnumWindows(inspect, 0)
        if len(matches) > 1:
            raise RuntimeError('Ambiguous owned confirmation dialog')
        if matches:
            button = user.GetDlgItem(matches[0], 6 if yes else 7)  # IDYES / IDNO
            if not button or not owned(matches[0]) or not owned(button):
                raise RuntimeError('Owned confirmation button was not found')
            user.SendMessageW(button, 0x00F5, 0, 0)  # BM_CLICK, never a Core approval API
            return {'title': title, 'answer': 'yes' if yes else 'no', 'evidence': evidence}
        if process.poll() is not None:
            raise RuntimeError('Owned Host exited before confirmation')
        time.sleep(.1)
    raise TimeoutError('Expected owned native confirmation dialog did not appear')


def click_confirmation(page, process, selector, title, evidence, yes=True):
    with ThreadPoolExecutor(max_workers=1) as executor:
        answer = executor.submit(answer_dialog, process, title, evidence, yes)
        page.locator(selector).click()
        return answer.result(timeout=50)


def exercise(page, process, work: Path, vault: Path):
    if vault.resolve() != (work.resolve() / 'vault'):
        raise RuntimeError('Experiment UI checks require the verifier-owned synthetic vault')
    print('EXPERIMENT_UI source editing', flush=True)
    folder = vault / 'experiments' / '课程'
    folder.mkdir(parents=True)
    source_path = 'experiments/课程/示例 #%.py'
    source = '\r\n'.join([
        'import csv, json', 'from pathlib import Path',
        "data = json.loads((Path(__file__).parent / '输入 #%.json').read_text(encoding='utf-8'))",
        "rows = list(csv.reader((Path(__file__).parent / '数据.csv').read_text(encoding='utf-8').splitlines()))",
        "result = {'sum': sum(data['values']), 'rows': len(rows) - 1, 'label': '中文成果'}",
        "Path('结果.json').write_bytes(json.dumps(result, ensure_ascii=False).encode('utf-8'))",
        "Path('报告.md').write_bytes(('# 原生验收报告\\n\\n合计：' + str(result['sum']) + '\\n').encode('utf-8'))",
        "print('隔离运行成功', result['sum'])", '',
    ])
    entry = vault / source_path
    entry.write_bytes(source.encode('utf-8'))
    (folder / '输入 #%.json').write_bytes(b'{"values":[1,2,3]}\r\n')
    (folder / '数据.csv').write_bytes('姓名,值\r\n甲,1\r\n乙,2\r\n'.encode('utf-8'))
    (folder / '停止.py').write_bytes(b'while True:\r\n    pass\r\n')
    page.wait_for_function("async () => (await smokeInvoke('workspace_tree')).some(f => f.path === 'experiments/课程/示例 #%.py')")
    page.evaluate("async () => {await smokePinia._s.get('workspace').refreshFileTree(); await smokePinia._s.get('editor').loadFile('/experiments/课程/示例 #%.py'); smokePinia._s.get('workspace').openFile('/experiments/课程/示例 #%.py')}")
    editor = page.locator('.cm-content').first
    editor.wait_for(); editor.click(); page.keyboard.press('Control+End')
    page.keyboard.insert_text('# 原生输入中文😀')
    page.keyboard.press('Control+z'); page.keyboard.press('Control+Shift+z')
    page.evaluate("() => smokePinia._s.get('editor').save()")
    expected = (source + '# 原生输入中文😀').encode('utf-8')
    if entry.read_bytes() != expected:
        raise RuntimeError('Source editing did not preserve UTF-8, CRLF and undo/redo bytes')
    source_hash = hashlib.sha256(expected).hexdigest()
    page.get_by_role('button', name='实验', exact=True).click()
    panel = page.locator('#experiment-panel')
    panel.wait_for()
    page.wait_for_function("() => document.querySelector('#experiment-panel .panel-scroll')?.getAttribute('aria-busy') === 'false'")
    if panel.locator('[role=alert]').count():
        raise RuntimeError('Native experiment initialization failed: ' + panel.locator('[role=alert]').inner_text())
    panel.locator('.run-form select').select_option(source_path)
    panel.get_by_text('输入文件与资源限制', exact=True).click()
    panel.locator('input[type=checkbox][value="experiments/课程/输入 #%.json"]').check()
    panel.locator('input[type=checkbox][value="experiments/课程/数据.csv"]').check()
    panel.get_by_label('墙钟秒数', exact=True).fill('30')
    panel.get_by_label('CPU秒数', exact=True).fill('30')
    panel.locator('[data-action=prepare-run]').click()
    panel.locator('[data-action=confirm-run]').wait_for()
    print('EXPERIMENT_UI native run rejection', flush=True)
    rejected = click_confirmation(page, process, '[data-action=confirm-run]',
        '确认运行 / Confirm run', source_hash, False)
    page.wait_for_function("() => document.querySelector('.run-detail h3')?.textContent === '已拒绝'")
    panel.locator('[data-action=prepare-run]').click()
    panel.locator('[data-action=confirm-run]').wait_for()
    print('EXPERIMENT_UI native run confirmation', flush=True)
    approved = click_confirmation(page, process, '[data-action=confirm-run]',
        '确认运行 / Confirm run', source_hash)
    page.wait_for_function("() => ['已完成','失败','已超限停止'].includes(document.querySelector('.run-detail h3')?.textContent)")
    if panel.locator('.run-detail > .section-heading > h3').inner_text() != '已完成':
        raise RuntimeError('Actual experiment failed: ' + panel.locator('.run-detail').inner_text())
    run = page.evaluate("async () => {const vault_id=smokePinia._s.get('workspace').vaultId;const history=await smokeInvoke('experiment_request',{request:{vault_id,action:{kind:'history',limit:10,cursor:null}}});return await smokeInvoke('experiment_request',{request:{vault_id,action:{kind:'record',operation_id:history.items[0].operation_id}}})}")
    if run['summary']['request']['entry']['hash'] != source_hash or run['result']['exit_code'] != 0:
        raise RuntimeError('Persisted run evidence differs from the approved source')
    if '隔离运行成功 6' not in run['result']['logs']['stdout']['text']:
        raise RuntimeError('Real stdout was not retained')
    destination = vault / '实验成果' / '原生验收报告.md'
    if destination.exists(): raise RuntimeError('Run unexpectedly imported an output')
    print('EXPERIMENT_UI output preview and separate import confirmation', flush=True)
    json_output = panel.locator('.outputs .output-item').filter(has_text='结果.json')
    json_output.get_by_role('button', name='预览', exact=True).click()
    page.wait_for_function("() => document.querySelector('.output-preview')?.textContent.includes('中文成果')")
    report_output = panel.locator('.outputs .output-item').filter(has_text='报告.md')
    report_output.locator('input[type=checkbox]').check()
    report_output.get_by_label('知识库目标路径', exact=True).fill('实验成果/原生验收报告.md')
    panel.locator('[data-action=prepare-import]').click()
    panel.locator('[data-action=confirm-import]').wait_for()
    output_hash = next(f['sha256'] for f in run['result']['outputs']['summary']['files'] if f['path'] == '报告.md')
    import_rejected = click_confirmation(page, process, '[data-action=confirm-import]',
        '确认导入 / Confirm import', output_hash, False)
    page.wait_for_function("() => document.querySelector('.import-plan h3')?.textContent.includes('已拒绝')")
    if destination.exists(): raise RuntimeError('Rejected import modified the vault')
    panel.locator('[data-action=prepare-import]').click()
    panel.locator('[data-action=confirm-import]').wait_for()
    import_approved = click_confirmation(page, process, '[data-action=confirm-import]',
        '确认导入 / Confirm import', output_hash)
    page.wait_for_function("() => document.querySelector('.import-plan h3')?.textContent.includes('已完成')")
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != output_hash:
        raise RuntimeError('Imported artifact does not match its reviewed output')
    panel.locator('.import-plan').get_by_role('button', name='打开成果', exact=True).click()
    panel.get_by_role('button', name='查看当前文件的成果来源', exact=True).click()
    panel.locator('.origins article').first.wait_for()
    if source_hash not in panel.locator('.origins article').first.inner_text():
        raise RuntimeError('Visible provenance does not identify the approved source')
    page.screenshot(path=str(work / 'native-experiment.png'))
    panel.locator('.run-form select').select_option('experiments/课程/停止.py')
    panel.locator('[data-action=prepare-run]').click()
    panel.locator('[data-action=confirm-run]').wait_for()
    print('EXPERIMENT_UI cancellation', flush=True)
    loop_hash = hashlib.sha256((folder / '停止.py').read_bytes()).hexdigest()
    stop_approved = click_confirmation(page, process, '[data-action=confirm-run]',
        '确认运行 / Confirm run', loop_hash)
    page.wait_for_function("() => document.querySelector('.run-detail > .section-heading > h3')?.textContent === '运行中'")
    panel.locator('[data-action=stop-run]').click()
    page.wait_for_function("() => document.querySelector('.run-detail h3')?.textContent === '已取消'")
    page.wait_for_function("async () => {const s=await smokeInvoke('experiment_request',{request:{vault_id:smokePinia._s.get('workspace').vaultId,action:{kind:'status'}}});return s.cleanup===null && s.available}", timeout=30000)
    status = page.evaluate("() => smokeInvoke('experiment_request',{request:{vault_id:smokePinia._s.get('workspace').vaultId,action:{kind:'status'}}})")
    if status['cleanup'] is not None or not status['available']:
        raise RuntimeError('Cancelled run left pending cleanup or disabled execution')
    return {'passed': True, 'source_utf8_crlf_undo_redo': True, 'source_hash': source_hash,
        'run_rejected': rejected, 'run_approved': approved, 'real_result': run['result'],
        'run_did_not_import': True, 'json_preview': True, 'import_rejected': import_rejected,
        'import_approved': import_approved, 'imported_sha256': output_hash,
        'origin_visible': True, 'cancel_approved': stop_approved, 'cancel_cleanup_complete': True,
        'scope': 'Owned synthetic vault and payload; no model API, service deployment or installer matrix'}
