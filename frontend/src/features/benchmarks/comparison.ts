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
  const left=flatten(a),right=flatten(b)
  return [...new Set([...Object.keys(left),...Object.keys(right)])].sort().filter(key=>JSON.stringify(left[key])!==JSON.stringify(right[key])).map(key=>({key,baseline:left[key],candidate:right[key]}))
}
export function caseDifferences(a:BenchmarkReport,b:BenchmarkReport){
  const map=(cases:unknown[])=>new Map(cases.filter(item=>item&&typeof item==='object').map(item=>{const value=item as Record<string,unknown>;return[`${value.mode??'agent'}:${value.case_id}:${value.repeat??0}`,value]}))
  const left=map(a.cases),right=map(b.cases)
  return [...new Set([...left.keys(),...right.keys()])].sort().map(key=>{
    const baseline=left.get(key),candidate=right.get(key)
    const quality=['recall','reciprocal_rank','hit_at_1','hit_at_5','citation_hit','success']
    const regressions=quality.filter(field=>baseline&&candidate&&(typeof baseline[field]==='number'||typeof baseline[field]==='boolean')&&(typeof candidate[field]==='number'||typeof candidate[field]==='boolean')&&Number(candidate[field])<Number(baseline[field]))
    return{key,baseline,candidate,regressions,changed:JSON.stringify(baseline)!==JSON.stringify(candidate)}
  })
}
