import{expect,it}from'vitest'
import{comparisonReason,metricDifferences,caseDifferences}from'./comparison'
import type{BenchmarkRun}from'@/services/benchmarkService'
const baseline:BenchmarkRun={id:'a',kind:'rag',datasetId:'d',datasetHash:'hash',status:'completed',progress:1,errorCode:null,configSnapshot:{vault_scope:'one'}}
it('requires matching kinds, dataset identities, hashes, scopes and complete states',()=>{
  expect(comparisonReason(baseline,{...baseline,id:'b'})).toBe('')
  for(const change of[{kind:'agent'},{datasetId:'other'},{datasetHash:'other'},{configSnapshot:{vault_scope:'two'}},{status:'cancelled'},{datasetHash:undefined}])expect(comparisonReason(baseline,{...baseline,id:'b',...change}as BenchmarkRun)).not.toBe('')
})
it('shows missing and non-finite metrics as unavailable while calculating real deltas',()=>{
  const rows=metricDifferences({fts:{recall:.8,missing:null,failed:2,nan:NaN}},{fts:{recall:.5,failed:1,new:0}})
  expect(rows.find(row=>row.key==='fts.recall')?.delta).toBeCloseTo(-.3)
  expect(rows.find(row=>row.key==='fts.missing')).toMatchObject({baseline:null,candidate:null,delta:null})
  expect(rows.find(row=>row.key==='fts.new')).toMatchObject({baseline:null,candidate:0,delta:null})
  expect(rows.find(row=>row.key==='fts.nan')?.delta).toBeNull()
})
it('aligns repeated RAG samples and Agent checks without treating missing cases as zero',()=>{
  const report=(cases:unknown[])=>({metrics:{},config_snapshot:{},cases})
  const rows=caseDifferences(report([{case_id:'a',mode:'fts',repeat:0,recall:1},{case_id:'b',repeat:0,success:true}]),report([{case_id:'a',mode:'fts',repeat:0,recall:.5},{case_id:'b',repeat:0,success:false},{case_id:'a',mode:'fts',repeat:1,recall:.5}]))
  expect(rows.find(row=>row.key==='fts:a:0')?.regressions).toEqual(['recall'])
  expect(rows.find(row=>row.key==='agent:b:0')?.regressions).toEqual(['success'])
  expect(rows.find(row=>row.key==='fts:a:1')).toMatchObject({baseline:undefined,regressions:[]})
})
