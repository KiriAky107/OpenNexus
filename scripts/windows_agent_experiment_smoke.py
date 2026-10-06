"""Real frozen Core/Host/Agent UI checks in a verifier-owned synthetic vault."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from windows_experiment_smoke import click_confirmation


def exercise(page, process, work: Path, vault: Path):
    if vault.resolve() != work.resolve() / 'vault':
        raise RuntimeError('Agent checks require the verifier-owned synthetic vault')
    page.evaluate('''() => {
        window.agentSmokeApi=async (path,method='GET',body=null)=>{
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
        return page.evaluate('([path,method,body])=>agentSmokeApi(path,method,body)', [path, method, body])

    def host(kind, **args):
        return page.evaluate('([kind,args])=>smokeInvoke("experiment_request",{request:{vault_id:smokePinia._s.get("workspace").vaultId,action:{kind,...args}}})', [kind, args])

    source_dir = vault / 'experiments' / 'Agent'
    source_dir.mkdir(parents=True)
    report = '# Agent 原生成果\r\n\r\n中文😀 # %\r\n'.encode('utf-8')
    script = "from pathlib import Path\r\nPath('报告.md').write_bytes(" + repr(report) + ")\r\nprint('真实 Agent 运行成功')\r\n"
    entry = source_dir / '运行 #%.py'
    entry.write_bytes(script.encode('utf-8'))
    slow = source_dir / '停止.py'; slow.write_bytes(b'while True:\r\n    pass\r\n')
    failure = source_dir / '失败.py'; failure.write_bytes("raise RuntimeError('真实失败')\r\n".encode('utf-8'))
    destination = vault / '实验成果' / 'Agent #%.md'
    destination.parent.mkdir()
    before = '原内容不得被拒绝的导入覆盖\r\n'.encode('utf-8')
    destination.write_bytes(before)
    tree = page.evaluate('() => smokeInvoke("workspace_tree")')
    files = {item['path']:item for item in tree if not item.get('deleted')}
    entry_id = files['experiments/Agent/运行 #%.py']['file_id']

    def create(name, arguments):
        run = api('/api/agent/runs', 'POST', {'input':'/tool '+name+' '+json.dumps(arguments, ensure_ascii=False),
            'provider_id':'mock','model':'mock-1','allowed_tools':[name], 'max_steps':3,
            'tool_timeout_seconds':90,'run_timeout_seconds':180,'allow_network':False})
        return run['run_id']

    def until(operation, description, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = operation()
            if value:
                return value
            time.sleep(.1)
        raise TimeoutError(description)

    def wait(run):
        def terminal():
            value = api('/api/agent/runs/'+run)
            return value if value['status'] in ('completed', 'failed', 'cancelled') else None
        return until(terminal, 'Agent did not reach a terminal state')

    def review(run):
        page.evaluate('run=>smokeRouter.push("/agent/runs/"+run)', run)
        page.locator('.permission-review-content .experiment-review').wait_for()
        page.locator('.permission-review-content button.button-primary:not([disabled])').wait_for()

    def decide(run, title, evidence, yes):
        review(run)
        result = click_confirmation(page, process, '.permission-review-content button.button-primary', title, evidence, yes)
        return result, wait(run)

    print('AGENT_UI default denial and user proposal switches', flush=True)
    policy = api('/api/permissions/policy')
    assert policy['experiments.run'] == policy['experiments.import'] == 'deny'
    denied = wait(create('experiments.run', {'entry_file_id':entry_id}))
    assert denied['tool_results'][0]['error_code'] == 'PERMISSION_DENIED'
    assert host('history', limit=10, cursor=None)['items'] == []
    page.evaluate('() => smokeRouter.push("/settings")')
    page.get_by_role('button', name='权限', exact=True).click()
    page.get_by_label('允许 Agent 提出运行请求', exact=True).click()
    until(lambda: api('/api/permissions/policy')['experiments.run'] == 'confirm', 'Run proposal setting was not saved')
    page.get_by_label('允许 Agent 提出成果导入请求', exact=True).click()
    until(lambda: api('/api/permissions/policy')['experiments.import'] == 'confirm', 'Import proposal setting was not saved')
    evidence = hashlib.sha256(entry.read_bytes()).hexdigest()
    confirmations = []
    print('AGENT_UI native rejection and real execution', flush=True)
    rejected_id = create('experiments.run', {'entry_file_id':entry_id})
    answer, rejected = decide(rejected_id, '确认 Agent 运行 / Confirm Agent run', evidence, False)
    confirmations.append(answer)
    assert rejected['tool_results'][0]['success'] is False
    rejected_record = host('history', limit=10, cursor=None)['items'][0]
    assert rejected_record['state'] == 'rejected'
    completed_id = create('experiments.run', {'entry_file_id':entry_id})
    answer, completed = decide(completed_id, '确认 Agent 运行 / Confirm Agent run', evidence, True)
    confirmations.append(answer)
    tool_result = completed['tool_results'][0]
    assert tool_result['success'] is True
    execution = tool_result['output']
    assert execution['record']['state'] == 'completed'
    assert execution['record']['result']['exit_code'] == 0
    assert '真实 Agent 运行成功' in execution['record']['result']['logs']['stdout']['text']
    source_run = execution['operation_id']
    page.locator('.trace-disclosure > summary').click()
    page.locator('.experiment-event').filter(has_text='实验运行 · 已完成').wait_for()

    print('AGENT_UI separate import rejection and byte-exact approval', flush=True)
    arguments = {'source_run_id':source_run,'selections':[{'output_path':'报告.md','destination':'实验成果/Agent #%.md'}]}
    output_hash = hashlib.sha256(report).hexdigest()
    import_no = create('experiments.import', arguments)
    answer, rejected_import = decide(import_no, '确认 Agent 导入 / Confirm Agent import', output_hash, False)
    confirmations.append(answer)
    assert rejected_import['tool_results'][0]['success'] is False and destination.read_bytes() == before
    import_yes = create('experiments.import', arguments)
    review(import_yes)
    assert '原内容不得被拒绝的导入覆盖' in page.locator('.comparison-columns').inner_text()
    answer, imported = decide(import_yes, '确认 Agent 导入 / Confirm Agent import', output_hash, True)
    confirmations.append(answer)
    assert imported['tool_results'][0]['success'] is True and destination.read_bytes() == report
    item = imported['tool_results'][0]['output']['record']['items'][0]
    assert item['state'] == 'committed'
    origins = host('origins', file_id=item['entry']['file_id'], limit=10, cursor=None)
    assert origins['items'][0]['run_id'] == source_run and origins['items'][0]['source']['request']['entry']['hash'] == evidence

    print('AGENT_UI failure and cancellation report actual Host outcomes', flush=True)
    failure_id = create('experiments.run', {'entry_file_id':files['experiments/Agent/失败.py']['file_id']})
    answer, failed = decide(failure_id, '确认 Agent 运行 / Confirm Agent run', hashlib.sha256(failure.read_bytes()).hexdigest(), True)
    confirmations.append(answer)
    assert failed['tool_results'][0]['success'] is False
    assert failed['tool_results'][0]['output']['record']['state'] == 'failed'
    cancel_id = create('experiments.run', {'entry_file_id':files['experiments/Agent/停止.py']['file_id']})
    review(cancel_id)
    confirmations.append(click_confirmation(page, process, '.permission-review-content button.button-primary',
        '确认 Agent 运行 / Confirm Agent run', hashlib.sha256(slow.read_bytes()).hexdigest(), True))
    until(lambda: any(event['event'] == 'ExperimentState' and event['data']['state'] == 'running'
        for event in api('/api/agent/runs/'+cancel_id+'/trace?after_sequence=-1&limit=100')['items']),
        'Cancellation must wait for actual Host running evidence')
    page.get_by_role('button', name='取消运行', exact=True).click()
    assert wait(cancel_id)['status'] == 'cancelled'
    def cleaned():
        value = host('status')
        return value['cleanup'] is None and value['available']
    until(cleaned, 'Owned experiment cleanup did not complete', timeout=30)
    cancelled = host('history', limit=10, cursor=None)['items'][0]
    assert cancelled['state'] == 'cancelled'
    page.screenshot(path=str(work/'native-agent-experiments.png'))
    assert entry.read_bytes() == script.encode('utf-8') and destination.read_bytes() == report
    return {'passed':True,'real_frozen_core':True,'native_confirmations':confirmations,'agent_run_id':completed_id,
        'source_run_id':source_run,'import_run_id':import_yes,'source_sha256':evidence,'imported_sha256':output_hash,
        'default_denied':True,'user_proposal_switches':True,'rejection_preserved_destination':True,
        'real_failure_reported':True,'cancellation_waited_for_running':True,'cleanup_available':True}
