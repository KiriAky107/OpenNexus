import type { BenchmarkRun, BenchmarkReport } from '@/services/benchmarkService'
export function comparisonReason(a?:BenchmarkRun,b?:BenchmarkRun){
  if(!a||!b)return '请选择基线和候选运行。'
  if(a.id===b.id)return '请选择两次不同的运行。'
  if(a.kind!==b.kind)return '评测类型不同，不能直接比较。'
  if(a.configSnapshot?.vault_scope!==b.configSnapshot?.vault_scope)return '知识库作用域不同，不能直接比较。'
  if(a.datasetId!==b.datasetId)return '数据集 ID 不同，不能直接比较。'
  if(!a.datasetHash||!b.datasetHash)return '运行缺少数据集内容哈希，不能直接比较。'
  if(a.datasetHash!==b.datasetHash)return '数据集内容哈希不同，不能直接比较。'
  if(a.status!=='completed'||b.status!=='completed')return `只有两次完整运行可直接比较；当前状态为 ${a.status} / ${b.status}。`
  return ''
}
export function flatten(value:Record<string,unknown>,prefix=''):Record<string,unknown>{
  return Object.fromEntries(Object.entries(value).flatMap(([key,value])=>value!==null&&typeof value==='object'&&!Array.isArray(value)?Object.entries(flatten(value as Record<string,unknown>,prefix+key+'.')):[[prefix+key,value]]))
}
export function metricDifferences(a:Record<string,unknown>,b:Record<string,unknown>){
  const left=flatten(a),right=flatten(b),number=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?value:null
  return [...new Set([...Object.keys(left),...Object.keys(right)])].sort().map(key=>{const baseline=number(left[key]),candidate=number(right[key]);return{key,baseline,candidate,delta:baseline===null||candidate===null?null:candidate-baseline}})
}
export function configDifferences(a:Record<string,unknown>,b:Record<string,unknown>){
  const left=flatten(withoutIdentity(a) as Record<string,unknown>),right=flatten(withoutIdentity(b) as Record<string,unknown>)
  return [...new Set([...Object.keys(left),...Object.keys(right)])].sort().filter(key=>canonical(left[key])!==canonical(right[key])).map(key=>({key,baseline:left[key],candidate:right[key]}))
}
const identity = new Set(['run_id','agent_run_id','collaboration_id','member_run_ids','active_agent_run_id','active_collaboration_id'])
function withoutIdentity(value:unknown):unknown {
  if(Array.isArray(value))return value.map(withoutIdentity)
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>!identity.has(key)).map(([key,value])=>[key,withoutIdentity(value)]))
  return value
}
function canonical(value:unknown):string|undefined {
  return JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item)
}
const qualityFields = ['recall','reciprocal_rank','hit_at_1','hit_at_5','citation_hit','success','selected_calls','accurate_calls','invalid_calls']
const resourceFields = ['latency_ms','token_usage','steps','tool_calls']
export function metricCategory(key:string) {
  return /(?:latency|token_usage|steps|cost|tool_calls)(?:$|_)/.test(key) ? '耗时与成本' : '质量与样本'
}
function finite(value:unknown):number|null { return typeof value==='boolean'?Number(value):typeof value==='number'&&Number.isFinite(value)?value:null }
function qualityValues(value:Record<string,unknown>|undefined) {
  if(!value)return {}
  const result:Record<string,number|null> = Object.fromEntries(qualityFields.map(key=>[key,key==='citation_hit'&&value.citation_applicable===false?null:finite(value[key])]))
  if(value.checks&&typeof value.checks==='object')for(const [key,check] of Object.entries(value.checks))result[`checks.${key}`]=typeof check==='boolean'?Number(check):null
  const calls=finite(value.tool_calls),expected=finite(value.expected_calls)
  const denominator=calls!==null&&expected!==null?Math.max(calls,expected):null
  for(const [key,count] of [['tool_selection_accuracy','selected_calls'],['tool_argument_accuracy','accurate_calls']] as const){const n=finite(value[count]);result[key]=n!==null&&denominator!==null&&denominator>0?n/denominator:null}
  const invalid=finite(value.invalid_calls)
  result.invalid_tool_call_rate=invalid!==null&&calls!==null&&calls>0?invalid/calls:null
  return result
}
function valueDifferences(left:Record<string,unknown>,right:Record<string,unknown>,lower = new Set<string>()) {
  return [...new Set([...Object.keys(left),...Object.keys(right)])].sort().map(key=>{
    const baseline=finite(left[key]),candidate=finite(right[key]),delta=baseline===null||candidate===null?null:candidate-baseline
    return {key,baseline,candidate,delta,direction:lower.has(key)?'lower' as const:'higher' as const,regressed:delta!==null&&(lower.has(key)?delta>0:delta<0)}
  })
}
export function caseDifferences(a:BenchmarkReport,b:BenchmarkReport){
  const map=(cases:unknown[])=>new Map(cases.filter(item=>item&&typeof item==='object').map(item=>{const value=item as Record<string,unknown>;return[`${value.mode??'agent'}:${value.case_id}:${value.repeat??0}`,value]}))
  const left=map(a.cases),right=map(b.cases)
  return [...new Set([...left.keys(),...right.keys()])].sort().map(key=>{
    const baseline=left.get(key),candidate=right.get(key)
    const quality=valueDifferences(qualityValues(baseline),qualityValues(candidate),new Set(['invalid_calls','invalid_tool_call_rate']))
    const resources=valueDifferences(Object.fromEntries(resourceFields.map(key=>[key,baseline?.[key]])),Object.fromEntries(resourceFields.map(key=>[key,candidate?.[key]])),new Set(resourceFields))
    const evidence=(value:Record<string,unknown>|undefined)=>value?withoutIdentity(Object.fromEntries(Object.entries(value).filter(([key])=>!qualityFields.includes(key)&&!resourceFields.includes(key)&&key!=='checks'))):undefined
    const qualityChanged=quality.some(row=>row.baseline!==row.candidate),resourceChanged=resources.some(row=>row.baseline!==row.candidate)
    const evidenceChanged=canonical(evidence(baseline))!==canonical(evidence(candidate)),rawChanged=canonical(baseline)!==canonical(candidate)
    return{key,baseline,candidate,quality,resources,regressions:quality.filter(row=>row.regressed).map(row=>row.key),qualityChanged,resourceChanged,evidenceChanged,rawChanged,changed:!baseline||!candidate||qualityChanged||resourceChanged||evidenceChanged}
  })
}
