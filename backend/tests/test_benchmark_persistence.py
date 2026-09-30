from uuid import uuid4
from app.benchmarks import service, storage, datasets
from app.contracts import BenchmarkRun, BenchmarkReport, BenchmarkEvent

def run(id,kind='rag',status='completed'):
    return BenchmarkRun(run_id=id,kind=kind,dataset_id='same',dataset_hash='hash',status=status,
                        created_at=service._now(),config_snapshot={'vault_scope':datasets.current_scope(),'model':'original'})

def test_saved_run_report_and_evidence_survive_cache_eviction_and_reopen():
    record=run('saved')
    report=BenchmarkReport(run_id=record.run_id,kind=record.kind,dataset_id=record.dataset_id,dataset_hash=record.dataset_hash,
        status=record.status,config_snapshot=record.config_snapshot,metrics={'fts':{'recall_at_k':.75,'citation_hit_rate':None}},
        cases=[{'case_id':'one','mode':'fts','repeat':0,'latency_ms':5,'recall':.75}])
    event=BenchmarkEvent(run_id=record.run_id,event='RunCompleted',sequence=0,timestamp=service._now(),data={'metrics':report.metrics})
    storage.save(record,report,event)
    service._forget(record.run_id)
    assert service.get_run(record.run_id)==record
    assert service.get_report(record.run_id)==report
    assert service.get_events(record.run_id)==[event]
    listed,total=service.list_runs()
    assert total==1 and listed[0].run_id==record.run_id
    assert service.get_report(record.run_id).metrics['fts']['citation_hit_rate'] is None

def test_restart_marks_unfinished_run_interrupted_and_preserves_partial_case_evidence():
    record=run('unfinished',status='running')
    event=BenchmarkEvent(run_id=record.run_id,event='CaseCompleted',sequence=0,timestamp=service._now(),data={'case_id':'one','mode':'fts','repeat':0,'latency_ms':5,'recall':.75})
    storage.save(record,event=event)
    listed,total=service.list_runs(status=record.status)
    assert listed==[] and total==0
    finished=service.get_run(record.run_id)
    assert finished.status.value=='failed' and finished.error_code=='BENCHMARK_INTERRUPTED'
    report=service.get_report(record.run_id)
    assert len(report.cases)==1 and report.metrics=={}
    assert service.get_events(record.run_id)[-1].event.value=='RunFailed'
    assert service.cancel_run(record.run_id).status.value=='failed'

def test_persistent_benchmark_scope_isolated_between_vault_databases(monkeypatch):
    from app.config import get_settings
    from app import host_bridge
    monkeypatch.setenv('APP_ENVIRONMENT','desktop');get_settings.cache_clear()
    first=host_bridge.vault_id.set(str(uuid4()))
    try:
        storage.save(run('private'))
        assert service.get_run('private') is not None
        second=host_bridge.vault_id.set(str(uuid4()))
        try:
            assert service.get_run('private') is None
            assert service.get_report('private') is None
            assert service.get_events('private')==[]
        finally:host_bridge.vault_id.reset(second)
        assert service.get_run('private') is not None
    finally:host_bridge.vault_id.reset(first)
