"""在显式隔离的 APP_DATA_DIR 中执行可复现的真实模型质量验证。

使用应用自身的索引与评测服务，不注入向量或伪造完成记录。
运行前必须已有本地权重和运行环境，推理过程不会下载模型。
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

async def main(args):
    from app.config import get_settings
    from app.knowledge.parser import parse_note
    from app.services import index_service
    from app.benchmarks import service
    from app.contracts import IndexRebuildRequest, RAGRunRequest
    settings=get_settings()
    if not all(os.getenv(name) for name in ['APP_DATA_DIR','APP_DB_PATH','APP_VAULT_PATH']):
        raise SystemExit('Explicit isolated APP_DATA_DIR/APP_DB_PATH/APP_VAULT_PATH required')
    fixture=Path(__file__).resolve().parents[1]/'data/benchmarks/corpus/phase2-v1.json'
    payload=json.loads(fixture.read_text(encoding='utf-8')); cases=[]
    settings.vault_path.mkdir(parents=True,exist_ok=True)
    if list(settings.vault_path.glob('*.md')) and not (settings.vault_path/'.phase2-fixture').exists():
        raise SystemExit('Refusing to overwrite a non-fixture vault')
    now=datetime(2026,9,7,tzinfo=timezone.utc)
    for note in payload['notes']:
        name=note['id']+'.md'; markdown='# '+note['title']+'\n\n'+note['text']+'\n'
        (settings.vault_path/name).write_text(markdown,encoding='utf-8')
        parsed=parse_note(markdown=markdown,file_path=name,folder='',created_at=now,updated_at=now)
        for index,query in enumerate(note['queries']):
            cases.append({'case_id':note['id']+'-'+str(index),'query':query,'expected_note_ids':[parsed.note_id],
                'expected_block_ids':[parsed.blocks[-1].block_id],'citation_required':True,
                'tags':['keyword' if index==0 else 'paraphrase',note['id']]})
    (settings.vault_path/'.phase2-fixture').touch()
    dataset={'dataset_id':'rag-phase2-v1','kind':'rag','version':payload['version'],'description':payload['description'],'cases':cases}
    settings.benchmark_datasets_path.mkdir(parents=True,exist_ok=True)
    (settings.benchmark_datasets_path/'rag-phase2-v1.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    if not args.reuse_index:
        job=await index_service.rebuild(IndexRebuildRequest())
        if job.status != 'completed': raise RuntimeError('Index did not complete: '+str(job.status))
    from app import repository
    expected_blocks = {bid for case in cases for bid in case['expected_block_ids']}
    if {hit.block_id for hit in repository.get_block_hits(list(expected_blocks))} != expected_blocks:
        raise RuntimeError('Frozen corpus does not match the index; rerun without --reuse-index')
    reports={}
    for label, mode, fusion, rerank, k in [('fts','fts','rrf',False,60),('vector','vector','rrf',False,60),
        ('hybrid-weighted','hybrid','weighted',False,60),('hybrid-rrf','hybrid','rrf',False,60),
        ('hybrid-rerank','hybrid','rrf',True,60),('rrf-k20','hybrid','rrf',False,20)]:
        run=await service.create_rag_run(RAGRunRequest(dataset_id='rag-phase2-v1',modes=[mode],repeat=2,
            retrieval={'top_k':5,'fusion':fusion,'rerank':rerank,'rrf_k':k,'rerank_candidates':20},
            metadata={'corpus_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'split':'development; no held-out production claim'}))
        await service.wait_for_run(run.run_id)
        reports[label]=service.get_report(run.run_id).model_dump(mode='json')
        print(label, json.dumps(reports[label]['metrics']),flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    from app.container import container
    await container.agent.shutdown(); container.mcp_servers.shutdown(); container.plugins.shutdown()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--reuse-index',action='store_true')
    asyncio.run(main(parser.parse_args()))
