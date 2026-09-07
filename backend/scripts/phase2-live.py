"""使用现有配置执行有界的真实协议与 Agent 检查，不输出凭据。

必须显式传入 --execute；最多发起 5 次直接模型请求和一组 4 样本 Agent 评测，
每个样本最多 6 步、6000 Token。不创建配置，也不执行外部写入。
"""
import argparse, asyncio, json, sys
from pathlib import Path
from time import perf_counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

async def main(args):
    from app.container import container
    from app.contracts import ModelRequest, AgentBenchmarkRequest
    from app.providers.base import ProviderError
    from app.benchmarks import agent,service
    provider=container.providers.get(args.provider)
    adapter=provider.adapter; model=provider.config.default_model
    results={'provider_id':args.provider,'protocol':provider.config.provider_type.value,'model':model,'checks':{},
        'unconfigured_protocols':['openai_responses','anthropic_messages','ollama'],
        'not_tested':['provider_context_limit','cache_hit_miss','context_compression'],'max_direct_requests':5}
    def request(prompt='Reply OK only.', **changes):
        return ModelRequest(provider_id=args.provider,model=model,messages=[{'role':'user','content':prompt}],max_tokens=128,**changes)
    async def check(name, fn):
        started=perf_counter()
        try: results['checks'][name]=await asyncio.wait_for(fn(),60)
        except ProviderError as exc: results['checks'][name]={'passed':False,'error_code':exc.code}
        except Exception as exc: results['checks'][name]={'passed':False,'error_type':type(exc).__name__}
        results['checks'][name]['elapsed_ms']=round((perf_counter()-started)*1000,2)
        print(name,results['checks'][name],flush=True)
    async def discover():
        models=await adapter.list_models();return {'passed':bool(models),'count':len(models)}
    async def complete():
        turn=await adapter.complete(request());return {'passed':bool(turn.text),'input_tokens':turn.input_tokens,'output_tokens':turn.output_tokens}
    async def stream():
        events=[e async for e in adapter.stream(request())]
        names=[e.event.value for e in events]
        return {'passed':names.count('Done')==1 and 'TextDelta' in names,'events':sorted(set(names)),
                'usage_reported':'Usage' in names,'reasoning_observed':'ThinkingDelta' in names}
    async def cancel():
        iterator=adapter.stream(request('List the integers 1 through 1000.'))
        first=await anext(iterator);started=perf_counter();await iterator.aclose()
        return {'passed':True,'first_event':first.event.value,'close_ms':(perf_counter()-started)*1000,
                'scope':'client stream resource close; provider billing cessation not observable'}
    async def invalid_model():
        try: await adapter.complete(request().model_copy(update={'model':'notesagent-nonexistent-acceptance-model'}))
        except ProviderError as exc:return {'passed':True,'error_code':exc.code}
        return {'passed':False,'reason':'provider accepted unknown model'}
    try:
        for name,fn in [('discovery',discover),('normal_chat',complete),('stream_usage_reasoning',stream),('stream_cancel',cancel),('error_mapping',invalid_model)]:await check(name,fn)
        created=await agent.create_run(AgentBenchmarkRequest(dataset_id='agent-core-v1',provider_id=args.provider,model=model))
        await service.wait_for_run(created.run_id)
        results['agent']=service.get_report(created.run_id).model_dump(mode='json')
        print('agent',results['agent']['metrics'],flush=True)
        servers=container.mcp_servers.list()
        results['mcp']=[{'server_id':s.server_id,'name':s.name,'state':s.status.value if hasattr(s.status,'value') else s.status} for s in servers]
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        await container.agent.shutdown();container.mcp_servers.shutdown();container.plugins.shutdown()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--provider',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');args=p.parse_args()
    if not args.execute:p.error('--execute required; uses existing provider quota')
    asyncio.run(main(args))
