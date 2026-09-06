<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { benchmarkService as service, type BenchmarkRun } from '@/services/benchmarkService'
import { listProviders } from '@/services/providerService'
const kind = ref<'rag' | 'agent'>('rag'), dataset = ref(''), error = ref(''), busy = ref(false)
const datasets = ref<Awaited<ReturnType<typeof service.datasets>>>([]), runs = ref<BenchmarkRun[]>([])
const providers = ref<Awaited<ReturnType<typeof listProviders>>>([]), provider = ref(''), model = ref('')
const report = ref<Awaited<ReturnType<typeof service.report>> | null>(null)
const topK = ref(5), rrfK = ref(60), rerank = ref(false)
const fusion = ref<'rrf' | 'weighted'>('rrf')
const metricLabels: Record<string, string> = {
  total_cases: '计划样本', evaluated_cases: '已评样本', successful_cases: '运行成功', failed_cases: '运行失败',
  task_success_rate: '任务成功率', tool_selection_accuracy: '工具选择准确率', tool_argument_accuracy: '参数准确率',
  invalid_tool_call_rate: '无效调用率', average_steps: '平均步骤', average_latency_ms: '平均耗时 (ms)',
  token_usage: 'Token 用量', tool_calls: '实际工具调用', expected_calls: '预期工具调用',
  hit_at_1: 'Hit@1', hit_at_5: 'Hit@5', recall_at_k: 'Recall@K', mrr: 'MRR', citation_hit_rate: '引用命中率',
  p50_latency_ms: 'P50 (ms)', p95_latency_ms: 'P95 (ms)', failure_rate: '运行失败率',
}
const metricGroups = computed(() => {
  const metrics = report.value?.metrics ?? {}
  const groups = 'task_success_rate' in metrics ? { Agent: metrics } : metrics
  return Object.entries(groups).filter(([, value]) => value && typeof value === 'object').map(([name, value]) => ({
    name, rows: Object.entries(value as Record<string, unknown>).map(([key, number]) => ({
      label: metricLabels[key] ?? key,
      value: number === null ? '不适用' : typeof number === 'number' ? Number(number.toFixed(4)).toLocaleString() : String(number),
    })),
  }))
})
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false
async function loadDatasets() { try { datasets.value = await service.datasets(kind.value); dataset.value = datasets.value[0]?.id ?? '' } catch(e) { error.value = String(e) } }
async function refresh() { try { runs.value = await service.list() } catch(e) { error.value = String(e) } if (!disposed) timer = setTimeout(refresh, 1500) }
watch(kind, loadDatasets)
watch(provider, id => { model.value = providers.value.find(p => p.provider_id === id)?.default_model ?? '' })
async function start() {
  error.value = ''; busy.value = true
  try { await service.start(kind.value, kind.value === 'agent' ? { dataset_id: dataset.value, provider_id: provider.value, model: model.value, max_steps: 6, timeout_seconds: 90, token_budget: 6000 } : { dataset_id: dataset.value, modes: ['fts','vector','hybrid'], retrieval: { top_k: topK.value, fusion: fusion.value, rrf_k: rrfK.value, rerank: rerank.value } }) }
  catch(e) { error.value = String(e) } finally { busy.value = false }
}
async function action(run: BenchmarkRun, cancel = false) { try { if (cancel) await service.cancel(run.id); else report.value = await service.report(run.id) } catch(e) { error.value = String(e) } }
function download() { const url = URL.createObjectURL(new Blob([JSON.stringify(report.value,null,2)], { type:'application/json' })); const a=document.createElement('a'); a.href=url; a.download='benchmark-report.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000) }
onMounted(async () => { void refresh(); void loadDatasets(); try { providers.value=(await listProviders()).filter(p=>p.enabled); provider.value=providers.value[0]?.provider_id ?? '' } catch(e) { error.value=String(e) } })
onBeforeUnmount(() => { disposed=true; clearTimeout(timer) })
</script>
<template>
  <main class="benchmark-page"><h1>Benchmark 评测</h1><p>标准数据集通过真实检索引擎或 Agent Runtime 执行。Agent 会使用所选提供商额度；需要权限时请打开 Trace 处理。</p>
    <div class="controls"><label>类型 <select v-model="kind"><option value="rag">RAG</option><option value="agent">Agent</option></select></label>
      <label>数据集 <select v-model="dataset"><option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.id }} · {{ d.cases }} 案例</option></select></label>
      <template v-if="kind === 'agent'"><label>提供商 <select v-model="provider"><option v-for="p in providers" :key="p.provider_id" :value="p.provider_id">{{ p.name }}</option></select></label><label>模型 <input v-model="model"></label></template>
      <template v-else><label>融合 <select v-model="fusion"><option value="rrf">RRF</option><option value="weighted">加权 50/50</option></select></label><label>Top K <input v-model.number="topK" type="number" min="1" max="100"></label><label>RRF K <input v-model.number="rrfK" type="number" min="1"></label><label><input v-model="rerank" type="checkbox">Lexical Reranker</label></template>
      <button :disabled="busy || !dataset || (kind === 'agent' && (!provider || !model))" @click="start">运行评测</button></div>
    <p v-if="error" role="alert">{{ error }}</p>
    <table><thead><tr><th>数据集</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="run in runs" :key="run.id"><td>{{ run.datasetId }}<small>{{ run.id }}</small></td><td>{{ run.status }} {{ run.progress === null ? '' : `${Math.round(run.progress*100)}%` }} {{ run.errorCode }}</td><td><button v-if="['queued','running'].includes(run.status)" @click="action(run,true)">取消</button><button v-else @click="action(run)">查看报告</button><RouterLink v-if="run.agentId" :to="`/agent/runs/${run.agentId}`">Agent Trace</RouterLink></td></tr></tbody></table>
    <section v-if="report"><h2>评测报告</h2><button class="button-secondary" @click="download">下载完整 JSON</button>
      <div v-for="group in metricGroups" :key="group.name"><h3>{{ group.name }}</h3><dl class="metric-grid"><div v-for="row in group.rows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl></div>
      <details><summary>冻结配置与逐例证据</summary><pre>{{ JSON.stringify(report,null,2) }}</pre></details></section>
  </main>
</template>
<style scoped>
.benchmark-page { padding:24px; overflow:auto; width:100%; } .controls { display:flex; gap:12px; flex-wrap:wrap; } label { display:flex; align-items:center; gap:6px; } input[type=number] { width:80px; } table { width:100%; margin-block:20px; border-collapse:collapse; } td,th { text-align:left; padding:12px; border-bottom:1px solid var(--color-border-default); } small { display:block; } pre { white-space:pre-wrap; overflow-wrap:anywhere; } [role=alert] { color:var(--color-error); }
.metric-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:16px 0; }
.metric-grid > div { padding:16px; border:1px solid var(--color-border-default); border-radius:8px; background:var(--color-surface-primary); }
dt { font-size:13px; color:var(--color-text-secondary); } dd { margin:8px 0 0; font-size:22px; font-weight:600; }
button { padding:6px 12px; border:1px solid var(--color-border-default); border-radius:6px; background:var(--color-surface-primary); cursor:pointer; }
button:disabled { opacity:.5; cursor:default; } td a { margin-left:12px; } input:not([type=checkbox]) { border:1px solid var(--color-border-default); border-radius:6px; padding:6px; }
</style>
