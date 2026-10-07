"""Use real signed Community packages through an owned native app window."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import uuid


class NativeCatalog:
    def __init__(self, work: Path):
        repo = Path(__file__).resolve().parents[1]
        service = Path(os.environ['OPENNEXUS_COMMUNITY_SERVER_DIR']).resolve(strict=True)
        if service not in {(repo.parent / 'Community-for-OpenNexus').resolve(), (repo / '.build/community-server').resolve()}:
            raise RuntimeError('Use the fixed isolated Community checkout')
        if not work.resolve().is_relative_to((repo / '.build').resolve()):
            raise RuntimeError('Native catalog data must remain in the owned build workspace')
        self.service_commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=service,text=True).strip()
        self.root = work / 'community-source'
        self.root.mkdir()
        (self.root / '.opennexus-test').write_text(uuid.uuid4().hex, 'ascii')
        self.log = (work / 'community-source.log').open('wb')
        self.process = subprocess.Popen([
            str(service / '.venv/Scripts/python.exe'),
            str(repo / 'scripts/fixtures/community_native_catalog.py'), str(self.root),
        ], cwd=service, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()
        def receive():
            for line in self.process.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: self.messages.put({'unexpected_stdout': line.decode('utf-8', 'replace')})
            self.messages.put({'process_exit': self.process.wait()})
        threading.Thread(target=receive, daemon=True).start()
        try:
            self.ready = self.messages.get(timeout=30)
            if not self.ready.get('url', '').startswith('https://127.0.0.1:'):
                raise RuntimeError('Owned source did not provide a loopback HTTPS URL')
            if Path(self.ready['ca_path']).resolve().parent != self.root.resolve():
                raise RuntimeError('Owned source returned another certificate path')
        except BaseException:
            self.close()
            (work/'community-source-startup-error.json').write_text(json.dumps({
                'owned_server_stopped':self.stopped,'exit_code':self.process.returncode,
                'pid':self.process.pid,'native_profile_not_created':True})+'\n','utf-8')
            raise

    def control(self, action, **fields):
        self.process.stdin.write((json.dumps({'action': action, **fields}) + '\n').encode())
        self.process.stdin.flush()
        response = self.messages.get(timeout=15)
        if response.get('action') != action:
            raise RuntimeError('Owned source control response differs')
        return response

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try: self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.log.close()
        self.stopped = self.process.poll() is not None


def installed(page):
    return page.evaluate("async()=>smokeInvoke('extension_installed',{vaultId:smokePinia._s.get('workspace').vaultId,offset:0,limit:20})")['items']


def run_imported_template(page, process, work: Path, vault: Path, source_hash: str):
    from windows_experiment_smoke import click_confirmation
    entry = 'experiments/native-community/课程 #%.py'
    history = page.evaluate("()=>smokeInvoke('experiment_request',{request:{vault_id:smokePinia._s.get('workspace').vaultId,action:{kind:'history',limit:10,cursor:null}}})")
    if history['items']:
        raise RuntimeError('Importing the template unexpectedly created a run')
    page.evaluate("async path=>{await smokeRouter.push('/workspace');await smokePinia._s.get('workspace').refreshFileTree();await smokePinia._s.get('editor').loadFile('/'+path);smokePinia._s.get('workspace').openFile('/'+path)}",entry)
    page.get_by_role('button', name='实验', exact=True).click()
    panel = page.locator('#experiment-panel')
    panel.wait_for()
    page.wait_for_function("()=>document.querySelector('#experiment-panel .panel-scroll')?.getAttribute('aria-busy')==='false'")
    panel.locator('.run-form select').select_option(entry)
    panel.get_by_text('输入文件与资源限制', exact=True).click()
    for path in ['inputs/data.json','inputs/表格.csv']:
        panel.locator(f'input[type=checkbox][value="experiments/native-community/{path}"]').check()
    panel.get_by_label('墙钟秒数', exact=True).fill('30')
    panel.get_by_label('CPU秒数', exact=True).fill('30')
    panel.locator('[data-action=prepare-run]').click()
    panel.locator('[data-action=confirm-run]').wait_for()
    approved = click_confirmation(page,process,'[data-action=confirm-run]','确认运行 / Confirm run',source_hash)
    page.wait_for_function("()=>['已完成','失败','已超限停止'].includes(document.querySelector('.run-detail h3')?.textContent)")
    if panel.locator('.run-detail > .section-heading > h3').inner_text() != '已完成':
        raise RuntimeError('Imported template execution failed: '+panel.locator('.run-detail').inner_text())
    evidence = page.evaluate("async()=>{const vault_id=smokePinia._s.get('workspace').vaultId;const history=await smokeInvoke('experiment_request',{request:{vault_id,action:{kind:'history',limit:10,cursor:null}}});const operation_id=history.items[0].operation_id;return {operation_id,record:await smokeInvoke('experiment_request',{request:{vault_id,action:{kind:'record',operation_id}}})}}")
    run = evidence['record']
    if run['summary']['request']['entry']['hash'] != source_hash or run['result']['exit_code'] != 0 or 'TEMPLATE_RUN:中文:6:' not in run['result']['logs']['stdout']['text']:
        raise RuntimeError('The actual run differs from the imported source and reviewed inputs')
    destination = vault/'课程成果/社区报告.md'
    if destination.exists():
        raise RuntimeError('Running the template unexpectedly imported its result')
    json_output=panel.locator('.outputs .output-item').filter(has_text='result.json')
    json_output.get_by_role('button',name='预览',exact=True).click()
    page.wait_for_function("()=>document.querySelector('.output-preview')?.textContent.includes('total')")
    report=panel.locator('.outputs .output-item').filter(has_text='报告.md')
    report.locator('input[type=checkbox]').check()
    report.get_by_label('知识库目标路径',exact=True).fill('课程成果/社区报告.md')
    panel.locator('[data-action=prepare-import]').click()
    panel.locator('[data-action=confirm-import]').wait_for()
    output_hash=next(item['sha256'] for item in run['result']['outputs']['summary']['files'] if item['path']=='报告.md')
    import_approved=click_confirmation(page,process,'[data-action=confirm-import]','确认导入 / Confirm import',output_hash)
    page.wait_for_function("()=>document.querySelector('.import-plan h3')?.textContent.includes('已完成')")
    if destination.read_bytes() != '# 课程成果\r\n总计：6\r\n'.encode('utf-8') or hashlib.sha256(destination.read_bytes()).hexdigest()!=output_hash:
        raise RuntimeError('The imported result lost its reviewed bytes or CRLF')
    panel.locator('.import-plan').get_by_role('button',name='打开成果',exact=True).click()
    panel.get_by_role('button',name='查看当前文件的成果来源',exact=True).click()
    panel.locator('.origins article').first.wait_for()
    if source_hash not in panel.locator('.origins article').first.inner_text():
        raise RuntimeError('The result provenance lost the imported template source hash')
    page.screenshot(path=str(work/'native-community-template-result.png'))
    return {'operation_id':evidence['operation_id'],'source_sha256':source_hash,'result':run['result'],'run_approved':approved,'import_approved':import_approved,'imported_sha256':output_hash,'origin_visible':True,'template_import_did_not_run':True,'run_did_not_import':True}


def exercise(page, process, work: Path, vault: Path, catalog: NativeCatalog):
    if vault.resolve() != work.resolve() / 'vault':
        raise RuntimeError('Community acceptance requires the owned test vault')
    results = {'online_https': False, 'applying_does_not_grant_execution': False,
        'browser_origin':page.evaluate('location.origin'),'browser_secure_context':page.evaluate('isSecureContext'),
        'source_request_failures':[]}
    def checkpoint():
        (work/'native-community-checkpoint.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n','utf-8')
    def request_failed(request):
        if request.url.startswith(catalog.ready['url']):
            results['source_request_failures'].append({'url':request.url,'failure':request.failure})
            checkpoint()
    page.on('requestfailed',request_failed)
    cdp=page.context.new_cdp_session(page)
    cdp.send('Network.enable')
    request_ids=set()
    cdp.on('Network.requestWillBeSent',lambda data:request_ids.add(data['requestId']) if data['request']['url'].startswith(catalog.ready['url']) else None)
    def failed(data):
        if data['requestId'] in request_ids:
            results['source_request_failures'].append({k:data.get(k) for k in ('errorText','blockedReason','corsErrorStatus')})
            checkpoint()
    cdp.on('Network.loadingFailed',failed)
    page.evaluate("()=>smokeRouter.push('/community')")
    page.locator('.community-page').wait_for()
    page.get_by_label('来源地址', exact=False).fill(catalog.ready['url'])
    page.get_by_role('button', name='检查来源与公钥', exact=True).click()
    review = page.get_by_role('dialog', name='核对来源公钥', exact=True)
    error=page.locator('.community-page [role=alert]')
    review.or_(error).first.wait_for()
    if error.count():
        checkpoint()
        raise RuntimeError('Real catalog discovery failed: '+error.inner_text())
    review.wait_for()
    if 'native-key' not in review.inner_text():
        raise RuntimeError('Real source signing key was not displayed')
    review.get_by_role('button', name='确认来源设置', exact=True).click()
    review.wait_for(state='hidden')
    page.get_by_role('button', name='搜索 / 刷新', exact=True).click()
    page.wait_for_function("()=>document.querySelectorAll('.community-card').length===30")
    observed = []
    for index in range(5):
        cards = page.locator('.community-card')
        observed.extend(cards.nth(n).inner_text() for n in range(cards.count()))
        if index < 4:
            page.locator('nav[aria-label="社区目录分页"]') .get_by_role('button', name='下一页', exact=True).click()
            page.wait_for_function("i=>document.querySelector('.catalog-pagination')?.textContent?.includes('第 '+i+' /')", arg=index+2)
    if len(observed) != 139 or len(set(observed)) != 139:
        raise RuntimeError('Native catalog pagination lost or duplicated packages')
    results['catalog_items'] = len(observed)
    results['online_https'] = True
    model_before = page.evaluate("()=>smokeApi('/api/local-models')")
    mcp_before = page.evaluate("()=>smokeApi('/api/mcp/servers')")['items']
    checkpoint()
    for kind in ['persona', 'model', 'mcp', 'template']:
        print('NATIVE_COMMUNITY install ' + kind, flush=True)
        page.get_by_label('关键词', exact=False).fill('原生 ' + kind)
        page.get_by_role('button', name='搜索 / 刷新', exact=True).click()
        card = page.locator('.community-card').filter(has_text='原生 ' + kind)
        card.wait_for()
        if card.count() != 1: raise RuntimeError('Expected one real signed package')
        card.click()
        detail = page.get_by_role('dialog', name='发行详情与安装', exact=True)
        detail.get_by_role('button', name='校验并暂存', exact=True).click()
        staged = page.locator('.desktop-packages .item-card').filter(has_text='examples/native-' + kind)
        source_error = page.locator('.community-panel [role=alert]')
        staged.or_(source_error).first.wait_for()
        if source_error.count():
            raise RuntimeError('Native staging ' + kind + ': ' + source_error.inner_text())
        staged.wait_for()
        detail.press('Escape')
        staged.get_by_role('button', name='查看安装预览', exact=True).click()
        install = page.get_by_role('dialog', name='桌面安装预览', exact=True)
        install.get_by_role('button', name='检查依赖、权限与配置', exact=True).click()
        confirm = install.get_by_role('button', name='确认安装并启用', exact=True)
        confirm.wait_for()
        confirm.click()
        page.wait_for_function("()=>document.querySelector('.desktop-packages')?.textContent?.includes('安装已完成')||Boolean(document.querySelector('dialog[open] [role=alert], [role=dialog] [role=alert], .desktop-packages [role=alert]'))")
        failure = install.locator('[role=alert]')
        if failure.count(): raise RuntimeError('Native install ' + kind + ': ' + failure.inner_text())
        install.press('Escape')
        row = next(item for item in installed(page) if item['release']['type'] == kind)
        if row['pending_operation'] is not None: raise RuntimeError('Installation remained pending')
        item = page.locator('.installed-packages .item-card').filter(has_text='原生 ' + kind)
        if kind == 'persona':
            item.get_by_role('button', name='应用人设', exact=True).click()
            dialog = page.get_by_role('dialog', name='应用社区人设', exact=True)
            dialog.get_by_label('应用目标', exact=False).select_option('workspace_persona')
            dialog.get_by_role('button', name='预览人设差异', exact=True).click()
            dialog.get_by_role('button', name='确认应用人设', exact=True).wait_for()
            dialog.get_by_role('button', name='确认应用人设', exact=True).click()
            dialog.wait_for(state='hidden')
            persona = page.evaluate("()=>smokeApi('/api/settings/persona')")
            if 'NATIVE_PERSONA:' not in json.dumps(persona): raise RuntimeError('The actual vault persona was not applied')
            results['persona'] = persona
        elif kind == 'template':
            item.get_by_role('button', name='导入模板', exact=True).click()
            dialog = page.get_by_role('dialog', name='导入社区模板', exact=True)
            dialog.get_by_label('实验目录', exact=True).fill('experiments/native-community')
            dialog.get_by_label('笔记路径（可选）', exact=True).fill('原生模板.md')
            dialog.get_by_role('button', name='预览文件差异', exact=True).click()
            dialog.locator('.file-review').first.wait_for()
            for article in dialog.locator('.file-review').all():
                article.locator('input[type=checkbox]').check()
                article.get_by_role('button', name='确认导入此文件', exact=True).click()
                article.get_by_text('此文件已导入', exact=True).wait_for()
            dialog.get_by_role('button', name='关闭', exact=True).click()
            paths = ['experiments/native-community/课程 #%.py', 'experiments/native-community/inputs/data.json', 'experiments/native-community/inputs/表格.csv', '原生模板.md']
            if not all((vault / path).is_file() for path in paths): raise RuntimeError('Real template import is incomplete')
            if 'TEMPLATE_RUN' not in (vault/paths[0]).read_text('utf-8'): raise RuntimeError('Imported source differs')
            results['template'] = {'files': paths, 'source_sha256': hashlib.sha256((vault/paths[0]).read_bytes()).hexdigest()}
            checkpoint()
            results['template']['independent_run_and_result_import'] = run_imported_template(page,process,work,vault,results['template']['source_sha256'])
            page.evaluate("()=>smokeRouter.push('/community')")
            page.locator('.community-page').wait_for()
        else:
            item.get_by_role('button', name='应用配置', exact=True).click()
            dialog = page.get_by_role('dialog', name='应用社区配置', exact=True)
            target = 'mcp:new' if kind == 'mcp' else 'model:local_runtime'
            dialog.get_by_label('应用目标', exact=False).select_option(target)
            dialog.get_by_role('button', name='预览配置差异', exact=True).click()
            dialog.get_by_role('button', name='确认应用配置', exact=True).wait_for()
            dialog.get_by_role('button', name='确认应用配置', exact=True).click()
            dialog.wait_for(state='hidden')
            if kind == 'model':
                actual = page.evaluate("()=>smokeApi('/api/local-models')")
                if actual['config']['embedding_model'] != 'bekko' or actual['config']['cpu_threads'] != 2:
                    raise RuntimeError('Reviewed model configuration was not applied')
                if actual['config']['version'] != model_before['config']['version'] + 1:
                    raise RuntimeError('Model configuration revision did not change')
                if actual['active_models'] or actual['queued_requests'] or actual['runtime_installed'] != model_before['runtime_installed']:
                    raise RuntimeError('Configuration application started or installed a model runtime')
                results[kind] = {'slot': row['slot'], 'target': target, 'configuration': actual['config'], 'no_runtime_started': True}
            else:
                actual = page.evaluate("()=>smokeApi('/api/mcp/servers')")['items']
                additions = [server for server in actual if server['server_id'] not in {server['server_id'] for server in mcp_before}]
                if len(additions) != 1:
                    raise RuntimeError('Reviewed MCP configuration did not create exactly one registry entry')
                server = additions[0]
                if server['transport'] != 'streamable_http' or server['url'] != 'https://catalog.example/mcp':
                    raise RuntimeError('Applied MCP configuration differs from the signed package')
                if server['enabled'] or server['trusted'] or server['tools_count'] or server['last_tested_at'] is not None or server['status'] != 'stopped':
                    raise RuntimeError('Applying MCP configuration granted execution or initiated a test')
                if server['secret_headers'].get('Authorization') is not False:
                    raise RuntimeError('The fixture secret declaration became a configured credential')
                results[kind] = {'slot': row['slot'], 'target': target, 'registry_entry': server, 'execution_not_granted': True}
        checkpoint()
    results['applying_does_not_grant_execution'] = results['model']['no_runtime_started'] and results['mcp']['execution_not_granted']
    page.screenshot(path=str(work / 'native-community-installed.png'))
    results['installed_packages'] = installed(page)
    return results
