"""Two bounded live calls for context summary + answer; never changes saved config."""
import argparse, asyncio, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
async def main(args):
    from app.container import container
    from app.contracts import ModelRequest, ModelContextPolicy
    from app.providers.factory import ProviderFactory
    from app.providers.context_budget import prepare_context, estimate
    from app.providers.base import ProviderError
    from app.services.usage_service import connection
    provider=container.providers.get(args.provider)
    model=provider.config.default_model
    request=ModelRequest(provider_id=args.provider,model=model,max_tokens=1024,messages=[
        {'role':'user','content':'项目事实：笔记保存在 Vault，导出使用点击时的快照。'*50},
        {'role':'assistant','content':'已记录。'}, {'role':'user','content':'请保持中文。'},
        {'role':'assistant','content':'好的。'}, {'role':'user','content':'笔记保存在什么地方？一句话回答。'}])
    original=request.model_dump()
    config=provider.config.model_copy(deep=True)
    config.context_policies=[ModelContextPolicy(model=model,context_window=8192,output_reserve=512,threshold=.1,mode='detect')]
    calls=0
    async def complete(value):
        nonlocal calls
        calls+=1
        return await provider.adapter.complete(value)
    results={'model':model,'configured_test_window':8192,'vendor_max_context_tested':False}
    try:
        try: await prepare_context(request,config,complete)
        except ProviderError as exc: results['detect']={'error_code':exc.code,'network_calls':calls}
        config.context_policies[0].mode='compress'
        config.context_policies[0].prompt='把以下历史资料压缩成一句中文，只保留笔记存储位置和导出快照规则。'
        prepared=await asyncio.wait_for(prepare_context(request,config,complete),60)
        turn=await asyncio.wait_for(complete(prepared),60)
        results['compression']={'passed':'vault' in (turn.text or '').lower(),'before_estimate':estimate(request),
            'after_estimate':estimate(prepared),'archive_unchanged':request.model_dump()==original,'network_calls':calls,
            'answer_input_tokens':turn.input_tokens,'answer_output_tokens':turn.output_tokens}
        with connection() as conn:
            rows=[json.loads(row[0]) for row in conn.execute('SELECT counters_json FROM model_usage WHERE provider_id=?',(args.provider,))]
        results['observed_provider_cache']={'requests':len(rows),'reporting_requests':sum(x.get('cache_hit_tokens') is not None for x in rows),
            'positive_hit_requests':sum((x.get('cache_hit_tokens') or 0)>0 for x in rows),
            'positive_miss_requests':sum((x.get('cache_miss_tokens') or 0)>0 for x in rows),
            'scope':'provider reported usage across this isolated acceptance session; not deterministic cache control'}
    finally:
        args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=False))
        await container.agent.shutdown();container.mcp_servers.shutdown();container.plugins.shutdown()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--provider',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');args=p.parse_args()
    if not args.execute:p.error('--execute required; two requests use existing quota')
    asyncio.run(main(args))
