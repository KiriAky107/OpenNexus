"""Explicit isolated Demo: retrieval → read → three tasks, then real read-only MCP.

Approves only this run's tasks.write tickets. Requires the quality fixture Vault.
Calls public API contracts; never writes completion state into SQLite.
"""
import argparse, json, time
from pathlib import Path
from urllib.request import Request,urlopen

def main(args):
    def api(path,body=None):
        req=Request(args.base_url+'/api'+path,data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=60) as response:return json.load(response)
    directory=args.data_dir.resolve()
    if not (directory/'vault/.phase2-fixture').exists():raise SystemExit('Isolated fixture Vault required')
    core=json.loads((directory/'benchmarks/rag-phase2-v1.json').read_text(encoding='utf-8'))
    note=next(c for c in core['cases'] if c['case_id']=='deadlock-0')['expected_note_ids'][0]
    cases=[{'case_id':'retrieval-tasks','prompt':f'按顺序执行：1. 用 rag.search 搜索“死锁”，明确使用 mode=fts；2. 用 notes.read 读取笔记 {note}；3. 根据内容用 tasks.create 分别创建且仅创建三个任务，标题严格为“验收-互斥条件”、“验收-循环等待”、“验收-锁顺序”；4. 总结死锁条件并引用搜索来源。请不要调用其他工具，不重复创建。',
        'allowed_tools':['rag.search','notes.read','tasks.create'],
        'expected_tools':[{'name':'rag.search','arguments':{'query':'死锁','mode':'fts'}},{'name':'notes.read','arguments':{'note_id':note}}]+
            [{'name':'tasks.create','arguments':{'title':title}} for title in ['验收-互斥条件','验收-循环等待','验收-锁顺序']],
        'citation_required':True,'tasks_created':3,'output_contains':['死锁'],'tags':['rag','notes','tasks','permissions']}]
    tools=api('/tools')['items']; mcp=next((t for t in tools if t['name'].endswith('.web_search') and t['name'].startswith('mcp.')),None)
    if mcp:
        cases.append({'case_id':'mcp-search','prompt':f'调用一次 {mcp["name"]}，query 严格使用 Python official documentation tutorial。根据工具真实返回给出一句总结。',
            'allowed_tools':[mcp['name']], 'expected_tools':[{'name':mcp['name'],'arguments':{'query':'Python official documentation tutorial'}}], 'tags':['mcp','real-network']})
    dataset={'dataset_id':'agent-integration-v1','kind':'agent','version':'1.0.0','description':'Isolated phase2 cross-module Demo; existing MCP binding captured','cases':cases}
    (directory/'benchmarks/agent-integration-v1.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    providers=api('/providers')['items']; provider=next(p for p in providers if p['enabled'] and p['provider_type']!='mock')
    run=api('/benchmarks/agent/runs',{'dataset_id':dataset['dataset_id'],'provider_id':provider['provider_id'],'model':provider['default_model'],
        'max_steps':10,'timeout_seconds':150,'token_budget':10000,'allow_network':True})
    approved=set();deadline=time.monotonic()+360
    while run['status'] in ['queued','running']:
        if time.monotonic()>deadline:
            api('/benchmarks/runs/'+run['run_id']+'/cancel',{});raise RuntimeError('Demo deadline')
        active=run['config_snapshot'].get('active_agent_run_id')
        if active:
            trace=api('/agent/runs/'+active+'/trace')
            for event in trace['items']:
                data=event['data'];ticket=data.get('request_id')
                if event['event']=='PermissionRequired' and data.get('permission')=='tasks.write' and ticket not in approved:
                    api('/agent/runs/'+active+'/permissions/'+ticket,{'decision':'allow_once'});approved.add(ticket)
        time.sleep(.3);run=api('/benchmarks/runs/'+run['run_id'])
    report=api('/benchmarks/runs/'+run['run_id']+'/report')
    report['permission_approvals']=len(approved)
    report['mcp_present']=bool(mcp)
    report['tasks']= [{'task_id':t['task_id'],'title':t['title']} for t in api('/tasks')['items'] if t['title'].startswith('验收-')]
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'metrics':report['metrics'],'cases':report['cases'],'permission_approvals':len(approved)},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8017');p.add_argument('--data-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');args=p.parse_args()
    if not args.execute:p.error('--execute required; creates three tasks only in the isolated fixture application')
    if args.base_url not in {'http://127.0.0.1:8017','http://localhost:8017'}:p.error('Use the isolated local acceptance server on port 8017')
    main(args)
