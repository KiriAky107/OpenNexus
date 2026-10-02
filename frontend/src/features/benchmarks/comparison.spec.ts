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
const report=(cases:unknown[])=>({metrics:{},config_snapshot:{},cases})
it('ignores run identities and object-key ordering but preserves raw audit differences',()=>{
 const result=caseDifferences(report([{case_id:'a',success:false,agent_run_id:'old',collaboration_id:'c1',member_run_ids:['m1'],checks:{output:false,tools_selected:true}}]),report([{checks:{tools_selected:true,output:false},member_run_ids:['m2'],collaboration_id:'c2',agent_run_id:'new',success:false,case_id:'a'}]))[0]!
 expect(result).toMatchObject({changed:false,rawChanged:true,evidenceChanged:false,regressions:[]})
})
it('detects partial checks and tool accuracy regressions even when both tasks failed',()=>{
 const result=caseDifferences(report([{case_id:'a',success:false,tool_calls:2,expected_calls:2,selected_calls:2,accurate_calls:2,invalid_calls:0,checks:{output:false,tool_arguments:true}}]),report([{case_id:'a',success:false,tool_calls:2,expected_calls:2,selected_calls:1,accurate_calls:1,invalid_calls:1,checks:{output:false,tool_arguments:false}}]))[0]!
 expect(result.regressions).toEqual(expect.arrayContaining(['checks.tool_arguments','tool_argument_accuracy','tool_selection_accuracy','invalid_calls','invalid_tool_call_rate']))
 expect(result.regressions).not.toContain('success');expect(result.qualityChanged).toBe(true)
})
it('keeps missing/zero-denominator quality unknown and separates resources and retrieval evidence',()=>{
 const result=caseDifferences(report([{case_id:'a',success:true,tool_calls:0,expected_calls:0,accurate_calls:0,latency_ms:10,retrieved_note_ids:['n1']}]),report([{case_id:'a',success:true,tool_calls:0,expected_calls:0,latency_ms:20,retrieved_note_ids:['n2']}]))[0]!
 expect(result.regressions).toEqual([])
 expect(result.quality.find(row=>row.key==='tool_argument_accuracy')).toMatchObject({baseline:null,candidate:null,delta:null})
 expect(result.quality.find(row=>row.key==='accurate_calls')).toMatchObject({baseline:0,candidate:null,delta:null})
 expect(result.resources.find(row=>row.key==='latency_ms')).toMatchObject({delta:10,regressed:true,direction:'lower'})
 expect(result).toMatchObject({resourceChanged:true,evidenceChanged:true,changed:true})
})
