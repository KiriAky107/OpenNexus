"""Exercise chat and collaboration consent in an owned native WebView2 payload.

Uses the offline Mock Provider through the real composer, frozen Core and Host.
Only the synthetic vault owned by windows_native_smoke may be changed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote

from windows_experiment_smoke import click_confirmation


def exercise(page, process, work: Path, vault: Path, *, require_default_policy=True):
    if vault.resolve() != work.resolve() / 'vault':
        raise RuntimeError('Chat checks require the verifier-owned synthetic vault')
    page.evaluate('''() => {
        window.chatSmokeApi=async (path,method='GET',body=null)=>{
            const requestId=await smokeInvoke('core_request_prepare',{timeoutMs:60000});
            try {
                const response=await smokeInvoke('core_request',{request:{requestId,path,method,
                    body,bodyBase64:null,contentType:'application/json',
                    idempotencyKey:method==='GET'?null:crypto.randomUUID()}});
                const value=JSON.parse(response.body);
                if(response.status>=400)throw new Error('Core '+response.status+' '+JSON.stringify(value));
                return value;
            } finally {await smokeInvoke('core_request_cancel',{requestId}).catch(()=>{});}
        };
    }''')

    def api(path, method='GET', body=None):
        return page.evaluate('([path,method,body])=>chatSmokeApi(path,method,body)', [path, method, body])

    def host(kind, **args):
        return page.evaluate('([kind,args])=>smokeInvoke("experiment_request",{request:{vault_id:smokePinia._s.get("workspace").vaultId,action:{kind,...args}}})', [kind, args])

    def until(operation, description, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = operation()
            if value:
                return value
            time.sleep(.15)
        raise TimeoutError(description)

    def completed(run):
        value = api('/api/agent/runs/' + run)
        return value if value['status'] in ('completed', 'failed', 'cancelled') else None

    def chat_idle():
        return page.evaluate('()=>!smokePinia._s.get("chat").isStreaming && !smokePinia._s.get("chat").isPreparing')

    def tool(name, args):
        return '/tool ' + name + ' ' + json.dumps(args, ensure_ascii=False)

    def send(name, args):
        previous = page.evaluate('()=>smokePinia._s.get("chat").messages.at(-1)?.message_id || null')
        page.locator('.composer textarea').fill(tool(name, args))
        print('CHAT_SEND_STATE', json.dumps(page.evaluate('''()=>{
            const s=smokePinia._s.get('chat');return {provider:s.selectedProviderId,model:s.selectedModel,
                can_send:s.canSend,at_latest:s.atLatest,window_busy:s.windowBusy,messages:s.messages.length};
        }''')), flush=True)
        page.locator('.composer-actions').get_by_role('button', name='发送', exact=True).click()
        return until(lambda: page.evaluate('''previous => {
            const message=smokePinia._s.get('chat').messages.at(-1);
            if(!message || message.role!=='assistant' || message.message_id===previous)return null;
            const calls=message.tool_calls;
            return calls?.find(call=>call.result)?.result || null;
        }''', previous), 'Chat did not expose a real delegated object')

    def delegate(name, args):
        value = json.loads(send('agent.create', {'input': tool(name, args), 'tools': [name]}))
        assert value.get('run_id'), value
        return value['run_id']

    def finish(run):
        result = until(lambda: completed(run), 'Delegated run did not finish')
        until(chat_idle, 'Chat did not finish after the real Agent result')
        return result

    def review():
        selector = '.permission-modal .permission-review-content'
        page.locator(selector + ' button.button-primary:not([disabled])').wait_for()
        return selector

    def open_chat():
        page.evaluate('()=>smokeRouter.push("/chat")')
        page.locator('.composer textarea').wait_for()
        until(lambda: page.evaluate('()=>!smokePinia._s.get("provider").isLoading && !smokePinia._s.get("skill").isLoading && smokePinia._s.get("chat").canSend'), 'Chat configuration did not finish loading')
        page.evaluate('async()=>{await smokePinia._s.get("chat").loadConversations();await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}')
        # Production intentionally hides the development provider from settings.
        # Expose only the real Core's offline provider in this owned QA renderer;
        # requests, streaming, delegation and permission handling stay real.
        page.evaluate('''async () => {
            const config=await chatSmokeApi('/api/providers/mock');
            const models=await chatSmokeApi('/api/providers/mock/models');
            const store=smokePinia._s.get('provider');
            const caps=values=>Object.fromEntries(values.map(value=>[value,true]));
            if(!store.providers.some(p=>p.provider_id==='mock'))store.providers.push({
                ...config,default_model:config.default_model || '',capabilities:caps(config.capabilities)});
            store.modelsByProvider.mock=models.items.map(model=>({model_id:model.model,
                name:model.display_name,capabilities:caps(model.capabilities)}));
        }''')
        if not page.locator('#chat-settings').is_visible():
            page.get_by_role('button', name='展开聊天设置', exact=True).click()
        page.locator('#chat-settings .field select').first.select_option('mock')
        page.get_by_label('模型 ID', exact=True).select_option('mock-1')
        page.get_by_label('允许管理与委托智能体', exact=True).check()
        page.get_by_label('检索知识库', exact=True).uncheck()

    confirmations = []
    source_path = 'experiments/原生聊天/运行 #%.py'
    folder = vault / 'experiments/原生聊天'
    folder.mkdir(parents=True)
    (folder / '输入 #%.json').write_bytes(b'{"values":[1,2,3]}\r\n')
    (folder / '数据.csv').write_bytes('姓名,值\r\n甲,1\r\n乙,2\r\n'.encode('utf-8'))
    (folder / '停止.py').write_bytes(b'while True:\r\n    pass\r\n')
    report = ('# 聊天原生成果\r\n\r\n中文😀 合计：6\r\n\r\n'
        '[实验源](../' + quote(source_path, safe='/') + ')\r\n').encode('utf-8')
    source = '\r\n'.join(['import csv, json', 'from pathlib import Path',
        "data = json.loads((Path(__file__).parent / '输入 #%.json').read_text(encoding='utf-8'))",
        "rows = list(csv.reader((Path(__file__).parent / '数据.csv').read_text(encoding='utf-8').splitlines()))",
        "assert sum(data['values']) == 6 and len(rows) == 3",
        "Path('报告.md').write_bytes(" + repr(report) + ')',
        "print('真实聊天运行成功', sum(data['values']))", ''])
    destination = vault / '实验成果/聊天 #%.md'
    destination.parent.mkdir(exist_ok=True)
    before = '拒绝导入不得覆盖原内容\r\n'.encode('utf-8')
    destination.write_bytes(before)
    open_chat()
    baseline = host('history', limit=50, cursor=None)['items']
    if require_default_policy:
        policy = api('/api/permissions/policy')
        assert policy['experiments.run'] == policy['experiments.import'] == 'deny' and baseline == []

    print('CHAT_UI reviewed source write; no execution from saving', flush=True)
    write = delegate('experiments.files.write', {'path': source_path, 'content': source})
    selector = review()
    assert source_path in page.locator(selector).inner_text()
    assert not (vault / source_path).exists() and host('history', limit=50, cursor=None)['items'] == baseline
    page.locator(selector).get_by_role('button', name='允许本次', exact=True).click()
    written = finish(write)
    assert written['tool_results'][0]['success'] and (vault / source_path).read_bytes() == source.encode('utf-8')
    files = {item['path']: item for item in page.evaluate('()=>smokeInvoke("workspace_tree")') if not item.get('deleted')}
    entry_id = files[source_path]['file_id']
    args = {'entry_file_id': entry_id, 'input_file_ids': [files['experiments/原生聊天/输入 #%.json']['file_id'],
        files['experiments/原生聊天/数据.csv']['file_id']]}
    assert host('history', limit=50, cursor=None)['items'] == baseline
    if require_default_policy:
        denied = finish(delegate('experiments.run', args))
        assert denied['tool_results'][0]['error_code'] == 'PERMISSION_DENIED'
        assert host('history', limit=50, cursor=None)['items'] == baseline
    page.evaluate('()=>smokeRouter.push("/settings")')
    page.get_by_role('button', name='权限', exact=True).click()
    if api('/api/permissions/policy')['experiments.run'] != 'confirm':
        page.get_by_label('允许 Agent 提出运行请求', exact=True).click()
    until(lambda: api('/api/permissions/policy')['experiments.run'] == 'confirm', 'Run proposals not saved')
    if api('/api/permissions/policy')['experiments.import'] != 'confirm':
        page.get_by_label('允许 Agent 提出成果导入请求', exact=True).click()
    until(lambda: api('/api/permissions/policy')['experiments.import'] == 'confirm', 'Import proposals not saved')
    open_chat()
    evidence = hashlib.sha256(source.encode('utf-8')).hexdigest()

    print('CHAT_UI native rejection and real selected-input execution', flush=True)
    rejected = delegate('experiments.run', args)
    selector = review()
    confirmations.append(click_confirmation(page, process, selector + ' button.button-primary',
        '确认 Agent 运行 / Confirm Agent run', evidence, False))
    assert finish(rejected)['tool_results'][0]['success'] is False
    assert host('history', limit=20, cursor=None)['items'][0]['state'] == 'rejected'
    run = delegate('experiments.run', args)
    selector = review()
    assert '输入 #%.json' in page.locator(selector).inner_text() and '数据.csv' in page.locator(selector).inner_text()
    confirmations.append(click_confirmation(page, process, selector + ' button.button-primary',
        '确认 Agent 运行 / Confirm Agent run', evidence))
    result = finish(run)['tool_results'][0]
    assert result['success'] and result['output']['record']['state'] == 'completed'
    assert '真实聊天运行成功 6' in result['output']['record']['result']['logs']['stdout']['text']
    source_run = result['output']['operation_id']
    assert destination.read_bytes() == before

    print('CHAT_UI separate native import approval; stable source and result links', flush=True)
    selections = {'source_run_id': source_run, 'selections': [{'output_path': '报告.md', 'destination': '实验成果/聊天 #%.md'}]}
    output_hash = hashlib.sha256(report).hexdigest()
    rejected_import = delegate('experiments.import', selections)
    selector = review()
    assert '拒绝导入不得覆盖原内容' in page.locator(selector).inner_text()
    confirmations.append(click_confirmation(page, process, selector + ' button.button-primary',
        '确认 Agent 导入 / Confirm Agent import', output_hash, False))
    assert not finish(rejected_import)['tool_results'][0]['success'] and destination.read_bytes() == before
    imported_run = delegate('experiments.import', selections)
    selector = review()
    confirmations.append(click_confirmation(page, process, selector + ' button.button-primary',
        '确认 Agent 导入 / Confirm Agent import', output_hash))
    imported = finish(imported_run)['tool_results'][0]['output']['record']['items'][0]
    assert imported['state'] == 'committed' and destination.read_bytes() == report
    origin = host('origins', file_id=imported['entry']['file_id'], limit=10, cursor=None)['items'][0]
    assert origin['run_id'] == source_run and origin['source']['request']['entry']['file_id'] == entry_id
    page.locator('.experiment-event').filter(has_text='成果导入').last.get_by_role('button', name='打开导入文件', exact=True).click()
    until(lambda: page.evaluate('()=>smokePinia._s.get("editor").currentFilePath') == '/实验成果/聊天 #%.md', 'Imported file did not open')
    # The real rendered note link must preserve encoded literal #/% characters.
    page.locator('.ProseMirror a').filter(has_text='实验源').first.click(modifiers=['Control'])
    until(lambda: page.evaluate('()=>smokePinia._s.get("editor").currentFilePath') == '/' + source_path, 'Experiment note link did not open')
    renamed = 'experiments/原生聊天/改名 #%.py'
    page.evaluate('([path,destination,expected])=>smokeInvoke("workspace_rename",{path,destination,expected})', [source_path, renamed, evidence])
    page.evaluate('''([oldPath,newPath])=>{
        smokePinia._s.get('workspace').renamePath('/'+oldPath,'/'+newPath,newPath.split('/').at(-1));
        smokePinia._s.get('editor').renameFilePath('/'+oldPath,'/'+newPath);
    }''', [source_path, renamed])
    page.evaluate('()=>smokePinia._s.get("workspace").refreshFileTree()')
    assert host('file_path', file_id=entry_id) == renamed
    assert host('source_preview', operation_id=source_run, path=source_path)['current_path'] == renamed
    open_chat()
    source_button = page.locator('.experiment-event').filter(has_text='实验运行').get_by_role('button', name='打开源文件', exact=False).last
    source_button.click()
    until(lambda: page.evaluate('()=>smokePinia._s.get("editor").currentFilePath') == '/' + renamed, 'Renamed event source did not open')
    open_chat()

    print('GROUP_UI plan approval never grants run consent; finite delegated budget', flush=True)
    definition = api('/api/agent/definitions', 'POST', {'name': '原生群组运行', 'provider_id': 'mock', 'model': 'mock-1',
        'tools': ['experiments.run'], 'token_budget': None, 'max_steps': 3})
    reader = api('/api/agent/definitions', 'POST', {'name': '原生群组汇总', 'provider_id': 'mock', 'model': 'mock-1',
        'tools': [], 'token_budget': None, 'max_steps': 3})
    plan = {'title': '原生实验分工', 'members': [
        {'member_id': 'runner', 'agent_id': definition['id'], 'input': tool('experiments.run', args)},
        {'member_id': 'summary', 'agent_id': reader['id'], 'input': '核对真实上游结果', 'depends_on': ['runner']}],
        'max_concurrency': 2, 'token_budget': 24000, 'max_tool_calls': 10, 'timeout_seconds': 90}
    group_id = json.loads(send('agent.collaborate', plan))['collaboration_id']
    until(chat_idle, 'Plan chat did not finish')
    group_card = page.locator('.collaboration-card').filter(has_text='原生实验分工').last
    group_card.get_by_role('button', name='确认分工并开始', exact=True).wait_for()
    planned = api('/api/agent/collaborations/' + group_id)
    assert planned['status'] == 'awaiting_confirmation' and all(member['run_id'] is None for member in planned['members'])
    count = len(host('history', limit=50, cursor=None)['items'])
    group_card.get_by_role('button', name='确认分工并开始', exact=True).click()
    group_card.locator('.permission-review-content button.button-primary:not([disabled])').wait_for()
    pending = api('/api/agent/collaborations/' + group_id)
    member = next(member for member in pending['members'] if member['member_id'] == 'runner')
    assert pending['plan']['token_budget'] == 24000
    assert pending['member_budgets']['runner'] is None
    assert api('/api/agent/runs/' + member['run_id'])['collaboration_id'] == group_id
    assert next(member for member in pending['members'] if member['member_id'] == 'summary')['run_id'] is None
    assert host('history', limit=50, cursor=None)['items'][0]['state'] == 'awaiting_confirmation'
    assert len(host('history', limit=50, cursor=None)['items']) == count + 1
    confirmations.append(click_confirmation(page, process, '.collaboration-card .permission-review-content button.button-primary',
        '确认 Agent 运行 / Confirm Agent run', evidence))
    group = until(lambda: (value if (value := api('/api/agent/collaborations/' + group_id))['status'] == 'completed' else None), 'Group did not finish')
    group_run = api('/api/agent/runs/' + group['members'][0]['run_id'])
    assert group_run['tool_results'][0]['success'] and group_run['tool_results'][0]['output']['record']['state'] == 'completed'
    summary_run = api('/api/agent/runs/' + group['members'][1]['run_id'])
    assert group['members'][1]['status'] == 'completed' and summary_run['definition_snapshot']['config']['tools'] == []
    assert summary_run['tool_results'] == [] and group['token_usage'] <= group['plan']['token_budget']
    assert destination.read_bytes() == report

    print('GROUP_UI cancel pending permission and downstream work', flush=True)
    slow_args = {'entry_file_id': files['experiments/原生聊天/停止.py']['file_id']}
    plan['title'] = '取消待确认实验分工'
    plan['members'][0]['input'] = tool('experiments.run', slow_args)
    cancelled_id = json.loads(send('agent.collaborate', plan))['collaboration_id']
    until(chat_idle, 'Cancel plan chat did not finish')
    cancel_card = page.locator('.collaboration-card').filter(has_text=plan['title']).last
    cancel_card.get_by_role('button', name='确认分工并开始', exact=True).click()
    cancel_card.locator('.permission-review-content button.button-primary:not([disabled])').wait_for()
    cancel_card.get_by_role('button', name='停止整个协作', exact=True).click()
    cancelled = until(lambda: (value if (value := api('/api/agent/collaborations/' + cancelled_id))['status'] == 'cancelled' else None), 'Group cancellation did not finish')
    assert all(member['status'] == 'cancelled' for member in cancelled['members'])
    assert host('status')['available'] and host('status')['cleanup'] is None
    assert all(item['state'] != 'running' for item in host('history', limit=50, cursor=None)['items'])
    assert (vault / renamed).read_bytes() == source.encode('utf-8') and destination.read_bytes() == report
    page.screenshot(path=str(work / 'native-chat-groups.png'))
    return {'passed': True, 'real_composer': True, 'real_frozen_core': True,
        'offline_provider_exposed_only_in_owned_qa_renderer': True, 'native_confirmations': confirmations,
        'chat_write_run': write, 'chat_execution_run': run, 'source_run_id': source_run, 'chat_import_run': imported_run,
        'group_id': group_id, 'cancelled_group_id': cancelled_id, 'entry_file_id': entry_id,
        'source_sha256': evidence, 'imported_sha256': output_hash, 'crlf_preserved': True,
        'selected_json_csv_inputs': True, 'default_run_denied': require_default_policy, 'saving_did_not_run': True,
        'rejected_import_preserved_original': True, 'encoded_note_link_opened': True,
        'renamed_event_source_opened': True, 'group_plan_independent_of_run_consent': True,
        'manual_unlimited_member_uses_finite_group_budget': True, 'downstream_tools_ceiling': True,
        'cancelled_pending_permission_and_downstream': True}
