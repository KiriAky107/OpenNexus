<script setup lang="ts">
import{computed,ref,watch,onBeforeUnmount}from'vue'
import{benchmarkService,type BenchmarkRun,type BenchmarkReport}from'@/services/benchmarkService'
import{useWorkspaceStore}from'@/stores/workspace'
import{comparisonReason,metricDifferences,metricCategory,configDifferences,caseDifferences}from'./comparison'
const props=defineProps<{runs:BenchmarkRun[]}>(),emit=defineEmits<{report:[run:BenchmarkRun]}>(),workspace=useWorkspaceStore()
const baselineId=ref(''),candidateId=ref(''),filter=ref(''),caseFilter=ref<'all'|'regressed'|'changed'>('all'),count=ref(30),error=ref(''),busy=ref(false),reports=ref<[BenchmarkReport,BenchmarkReport]>()
let generation=0
const options=computed(()=>props.runs.filter(run=>`${run.datasetId} ${run.id} ${run.kind} ${run.status}`.toLowerCase().includes(filter.value.trim().toLowerCase())))
const baseline=computed(()=>props.runs.find(run=>run.id===baselineId.value)),candidate=computed(()=>props.runs.find(run=>run.id===candidateId.value)),reason=computed(()=>comparisonReason(baseline.value,candidate.value))
const metrics=computed(()=>reports.value?metricDifferences(reports.value[0].metrics,reports.value[1].metrics):[]),configs=computed(()=>reports.value?configDifferences(reports.value[0].config_snapshot,reports.value[1].config_snapshot):[])
const allCases=computed(()=>reports.value?caseDifferences(...reports.value):[])
const cases=computed(()=>allCases.value.filter(item=>caseFilter.value==='all'||caseFilter.value==='regressed'&&item.regressions.length||caseFilter.value==='changed'&&item.changed))
const format=(value:number|null)=>value===null?'缺失':Number(value.toFixed(4)).toLocaleString()
watch(()=>[baselineId.value,candidateId.value,reason.value,workspace.vaultId],async()=>{
  const version=++generation;reports.value=undefined;error.value='';busy.value=false;count.value=30
  if(reason.value||!baseline.value||!candidate.value)return
  busy.value=true
  try{
    const loaded=await Promise.all([benchmarkService.report(baselineId.value),benchmarkService.report(candidateId.value)])
    if(version!==generation)return
    if(loaded.some((report,index)=>{const run=index===0?baseline.value!:candidate.value!;return report.run_id!==run.id||report.status!=='completed'||report.dataset_id!==run.datasetId||report.dataset_hash!==run.datasetHash||report.kind!==run.kind||report.config_snapshot.vault_scope!==run.configSnapshot?.vault_scope}))throw new Error('报告身份或状态与所选运行不一致，请刷新。')
    reports.value=loaded
  }catch(cause){if(version===generation)error.value=String(cause)}finally{if(version===generation)busy.value=false}
})
watch(()=>workspace.vaultId,()=>{generation++;baselineId.value='';candidateId.value='';reports.value=undefined;error.value='';filter.value='';busy.value=false},{flush:'sync'})
watch(caseFilter,()=>count.value=30)
onBeforeUnmount(()=>generation++)
</script>
<template>
  <section class="panel benchmark-comparison" aria-label="Benchmark 运行对比">
    <h2>运行对比</h2><p class="subtle">选择当前知识库内、数据集内容相同的两次完整运行。变化量为候选 − 基线。</p>
    <input v-model="filter" class="input" aria-label="筛选对比运行" placeholder="筛选数据集、类型、状态或运行 ID"/>
    <div class="comparison-selects"><label>基线运行<select v-model="baselineId" class="select" aria-label="基线运行"><option value="">请选择</option><option v-for="run in options" :key="run.id" :value="run.id">{{ run.kind }} · {{ run.datasetId }} · {{ run.status }} · {{ run.id }}</option></select></label><label>候选运行<select v-model="candidateId" class="select" aria-label="候选运行"><option value="">请选择</option><option v-for="run in options" :key="run.id" :value="run.id">{{ run.kind }} · {{ run.datasetId }} · {{ run.status }} · {{ run.id }}</option></select></label></div>
    <p v-if="reason" role="status">{{ reason }}</p><p v-if="busy" role="status">读取已保存报告…</p><p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <div class="inline-actions"><button v-if="baseline" class="button-secondary" @click="emit('report',baseline)">查看基线完整报告</button><button v-if="candidate" class="button-secondary" @click="emit('report',candidate)">查看候选完整报告</button></div>
    <template v-if="reports"><p>数据集哈希：{{ baseline?.datasetHash }}</p><div v-for="group in ['质量与样本','耗时与成本']" :key="group" class="comparison-table"><h3>{{ group }}</h3><table><thead><tr><th>指标</th><th>基线</th><th>候选</th><th>变化量</th></tr></thead><tbody><tr v-for="row in metrics.filter(row=>metricCategory(row.key)===group)" :key="row.key"><td>{{ row.key }}</td><td>{{ format(row.baseline) }}</td><td>{{ format(row.candidate) }}</td><td>{{ row.delta===null?'不可计算':`${row.delta>0?'+':''}${format(row.delta)}` }}</td></tr></tbody></table></div>
      <details><summary>配置差异（{{ configs.length }}）</summary><dl><div v-for="row in configs" :key="row.key"><dt>{{ row.key }}</dt><dd>{{ JSON.stringify(row.baseline)??'缺失' }} → {{ JSON.stringify(row.candidate)??'缺失' }}</dd></div></dl></details>
      <div class="inline-actions"><h3>逐用例变化</h3><select v-model="caseFilter" class="select" aria-label="筛选对比用例"><option value="all">全部用例</option><option value="regressed">退步用例</option><option value="changed">有变化的用例</option></select><span>{{ cases.length }} 项</span></div>
      <p class="subtle">退步筛选按质量指标判定；耗时与成本另列。运行身份不同不计入业务变化，完整原始证据仍可查看。</p>
      <details v-for="item in cases.slice(0,count)" :key="item.key" class="case-comparison"><summary>{{ item.key }} · {{ !item.baseline||!item.candidate?'单侧缺失':item.regressions.length?`退步：${item.regressions.join('、')}`:item.changed?'有变化':'相同' }}</summary>
        <div v-for="group in [{name:'质量',rows:item.quality},{name:'耗时与成本',rows:item.resources}]" :key="group.name" class="comparison-table"><h4>{{ group.name }}</h4><table><thead><tr><th>字段</th><th>基线</th><th>候选</th><th>方向</th></tr></thead><tbody><tr v-for="row in group.rows" :key="row.key"><td>{{ row.key }}</td><td>{{ format(row.baseline) }}</td><td>{{ format(row.candidate) }}</td><td>{{ row.direction==='lower'?'越低越好':'越高越好' }}{{ row.regressed?' · 退步':'' }}</td></tr></tbody></table></div>
        <p>证据内容：{{ item.evidenceChanged?'有差异':'相同' }}{{ item.rawChanged&&!item.changed?'；仅运行身份不同':'' }}</p>
        <details><summary>原始证据差异（保留运行身份）</summary><div class="case-pair"><div><strong>基线</strong><pre>{{ item.baseline?JSON.stringify(item.baseline,null,2):'缺失' }}</pre></div><div><strong>候选</strong><pre>{{ item.candidate?JSON.stringify(item.candidate,null,2):'缺失' }}</pre></div></div></details>
      </details><button v-if="count<cases.length" class="button-secondary" @click="count+=30">更多用例</button>
    </template>
  </section>
</template>
<style scoped>
.benchmark-comparison{display:grid;gap:var(--space-md);min-width:0;overflow-wrap:anywhere}.comparison-selects,.case-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-md)}label{display:grid;gap:var(--space-xs);min-width:0}.select{width:100%;min-width:0}.comparison-table{overflow:auto}table{width:100%;border-collapse:collapse}td,th{padding:var(--space-sm);text-align:left;border-bottom:1px solid var(--color-border-default)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--color-background-secondary);padding:var(--space-sm)}dd{margin-inline:0}.case-comparison{padding:var(--space-sm);border:1px solid var(--color-border-default);border-radius:var(--radius-md)}summary{cursor:pointer}@media(max-width:640px){.comparison-selects,.case-pair{grid-template-columns:1fr}td,th{font-size:var(--font-size-xs)}}
</style>
