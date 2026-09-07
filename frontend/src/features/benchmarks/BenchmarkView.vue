<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { benchmarkService as service, type BenchmarkRun } from '@/services/benchmarkService'
import { listProviders } from '@/services/providerService'
const kind = ref<'rag' | 'agent'>('rag'), dataset = ref(''), error = ref(''), busy = ref(false)
const datasets = ref<Awaited<ReturnType<typeof service.datasets>>>([]), runs = ref<BenchmarkRun[]>([])
const providers = ref<Awaited<ReturnType<typeof listProviders>>>([]), provider = ref(''), model = ref('')
const report = ref<Awaited<ReturnType<typeof service.report>> | null>(null)
const reportName = ref(''), loading = ref(true)
const statusLabels: Record<string,string> = { queued:'排队中', running:'运行中', completed:'已完成', failed:'失败', cancelled:'已取消' }
const activeCount = computed(() => runs.value.filter(run => ['queued','running'].includes(run.status)).length)
const ratioKeys = new Set(['task_success_rate','tool_selection_accuracy','tool_argument_accuracy','invalid_tool_call_rate','hit_at_1','hit_at_5','recall_at_k','citation_hit_rate','failure_rate'])
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
      value: number === null ? '不适用' : typeof number === 'number' ? ratioKeys.has(key) ? `${Number((number*100).toFixed(2))}%` : Number(number.toFixed(key.includes('latency') ? 2 : 4)).toLocaleString() : String(number),
    })),
  }))
})
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false
async function loadDatasets() { try { datasets.value = await service.datasets(kind.value); dataset.value = datasets.value[0]?.id ?? '' } catch(e) { error.value = String(e) } }
async function refresh() { try { runs.value = await service.list() } catch(e) { error.value = String(e) } finally { loading.value = false } if (!disposed) timer = setTimeout(refresh, 1500) }
watch(kind, loadDatasets)
watch(provider, id => { model.value = providers.value.find(p => p.provider_id === id)?.default_model ?? '' })
async function start() {
  error.value = ''; busy.value = true
  try { await service.start(kind.value, kind.value === 'agent' ? { dataset_id: dataset.value, provider_id: provider.value, model: model.value, max_steps: 6, timeout_seconds: 90, token_budget: 6000 } : { dataset_id: dataset.value, modes: ['fts','vector','hybrid'], retrieval: { top_k: topK.value, fusion: fusion.value, rrf_k: rrfK.value, rerank: rerank.value } }) }
  catch(e) { error.value = String(e) } finally { busy.value = false }
}
async function action(run: BenchmarkRun, cancel = false) { try { if (cancel) await service.cancel(run.id); else { report.value = await service.report(run.id); reportName.value = run.datasetId } } catch(e) { error.value = String(e) } }
function download() { const url = URL.createObjectURL(new Blob([JSON.stringify(report.value,null,2)], { type:'application/json' })); const a=document.createElement('a'); a.href=url; a.download='benchmark-report.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000) }
onMounted(async () => { void refresh(); void loadDatasets(); try { providers.value=(await listProviders()).filter(p=>p.enabled); provider.value=providers.value[0]?.provider_id ?? '' } catch(e) { error.value=String(e) } })
onBeforeUnmount(() => { disposed=true; clearTimeout(timer) })
</script>
<template>
  <main class="feature-page benchmark-page">
    <header class="feature-header">
      <div><h1>Benchmark 评测</h1><p>比较检索质量与智能体表现，查看每次评测的结果和执行轨迹。</p></div>
      <span class="badge" :class="{ info: activeCount > 0 }">{{ activeCount ? `${activeCount} 项正在运行` : 'RAG / Agent' }}</span>
    </header>
    <div class="benchmark-content">
      <form class="panel benchmark-config" @submit.prevent="start">
        <div class="section-heading"><div><h2>创建评测</h2><p class="subtle">选择数据集和运行配置，结果将保留在下方列表。</p></div></div>
        <div class="form-grid">
          <div class="field"><label for="benchmark-kind">类型</label><select id="benchmark-kind" v-model="kind" class="select"><option value="rag">RAG 检索</option><option value="agent">Agent 任务</option></select></div>
          <div class="field dataset-field"><label for="benchmark-dataset">数据集</label><select id="benchmark-dataset" v-model="dataset" class="select"><option v-if="!datasets.length" value="">暂无可用数据集</option><option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.id }} · {{ d.cases }} 案例</option></select></div>
          <template v-if="kind === 'agent'">
            <div class="field"><label for="benchmark-provider">提供商</label><select id="benchmark-provider" v-model="provider" class="select"><option v-if="!providers.length" value="">暂无可用提供商</option><option v-for="p in providers" :key="p.provider_id" :value="p.provider_id">{{ p.name }}</option></select></div>
            <div class="field"><label for="benchmark-model">模型</label><input id="benchmark-model" v-model="model" class="input" placeholder="模型 ID"></div>
          </template>
          <template v-else>
            <div class="field"><label for="benchmark-fusion">融合方式</label><select id="benchmark-fusion" v-model="fusion" class="select"><option value="rrf">RRF 排名融合</option><option value="weighted">加权 50/50</option></select></div>
            <div class="field"><label for="benchmark-topk">Top K</label><input id="benchmark-topk" v-model.number="topK" class="input" type="number" min="1" max="100"></div>
            <div class="field"><label for="benchmark-rrfk">RRF K</label><input id="benchmark-rrfk" v-model.number="rrfK" class="input" type="number" min="1" :disabled="fusion !== 'rrf'"></div>
          </template>
        </div>
        <div class="config-footer">
          <label v-if="kind === 'rag'" class="checkbox-label"><input v-model="rerank" type="checkbox">启用词面重排（Lexical Reranker）</label>
          <p v-else class="subtle">将使用所选提供商额度；需要工具权限时，请在执行轨迹中处理。</p>
          <button class="button-primary" :disabled="busy || !dataset || (kind === 'agent' && (!provider || !model))">{{ busy ? '正在创建…' : '运行评测' }}</button>
        </div>
      </form>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <section class="panel benchmark-history" aria-labelledby="benchmark-history-title" :aria-busy="loading">
        <div class="section-heading"><h2 id="benchmark-history-title">运行记录</h2><span class="badge">{{ runs.length }} 项</span></div>
        <div v-if="!runs.length" class="empty-state"><div><strong>{{ loading ? '正在加载记录…' : '还没有评测记录' }}</strong><p>选择上方的数据集并运行评测，完成后可查看指标、下载报告。</p></div></div>
        <div v-else class="table-scroll"><table><thead><tr><th>数据集</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="run in runs" :key="run.id">
          <td><strong>{{ run.datasetId }}</strong><small class="subtle run-id">{{ run.id }}</small></td>
          <td><span class="badge" :class="{ success:run.status === 'completed', error:run.status === 'failed', info:['queued','running'].includes(run.status), warning:run.status === 'cancelled' }">{{ statusLabels[run.status] ?? run.status }}</span><span v-if="run.progress !== null" class="progress-label subtle">{{ Math.round(run.progress*100) }}%</span><small v-if="run.errorCode" class="run-error">{{ run.errorCode }}</small></td>
          <td><div class="inline-actions"><button v-if="['queued','running'].includes(run.status)" class="button-secondary" @click="action(run,true)">取消</button><button v-else class="button-secondary" @click="action(run)">查看报告</button><RouterLink v-if="run.agentId" class="trace-link" :to="`/agent/runs/${run.agentId}`">执行轨迹</RouterLink></div></td>
        </tr></tbody></table></div>
      </section>
      <section v-if="report" class="panel benchmark-report" aria-labelledby="benchmark-report-title">
        <div class="section-heading"><div><h2 id="benchmark-report-title">评测报告</h2><p class="subtle">{{ reportName }}</p></div><button class="button-secondary" @click="download">下载完整 JSON</button></div>
        <div v-for="group in metricGroups" :key="group.name" class="metric-group"><h3>{{ group.name }}</h3><dl class="metric-grid"><div v-for="row in group.rows" :key="row.label" class="metric-card"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl></div>
        <details class="ui-disclosure"><summary>冻结配置与逐例证据</summary><pre>{{ JSON.stringify(report,null,2) }}</pre></details>
      </section>
    </div>
  </main>
</template>
<style scoped>
.benchmark-page { width:100%; min-width:0; color:var(--color-text-primary); }
.benchmark-content { max-width:1180px; margin:0 auto; display:grid; gap:var(--space-xl); }
.benchmark-content > .panel { width:100%; min-width:0; margin:0; padding:var(--space-xl); }
.section-heading { display:flex; align-items:center; justify-content:space-between; gap:var(--space-md); margin-bottom:var(--space-lg); }
h2 { margin:0; font-size:var(--font-size-lg); font-weight:650; } h3 { margin:0 0 var(--space-md); font-size:var(--font-size-md); }
.section-heading p { margin:var(--space-xs) 0 0; } .form-grid { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); }
.dataset-field { grid-column:span 2; } .field { min-width:0; }
.config-footer { display:flex; align-items:center; justify-content:space-between; gap:var(--space-lg); margin-top:var(--space-xl); padding-top:var(--space-lg); border-top:1px solid var(--color-border-default); }
.config-footer p { margin:0; } .config-footer .button-primary { margin-left:auto; flex-shrink:0; }
.checkbox-label { display:flex; align-items:center; gap:var(--space-sm); color:var(--color-text-secondary); font-size:var(--font-size-sm); }
.empty-state { min-height:170px; } .empty-state p { margin:0; line-height:1.7; }
.table-scroll { overflow-x:auto; } table { width:100%; min-width:580px; border-collapse:collapse; font-size:var(--font-size-sm); }
th { text-align:left; color:var(--color-text-secondary); background:var(--color-background-secondary); font-weight:600; }
td,th { padding:var(--space-md); border-bottom:1px solid var(--color-border-default); } tbody tr:last-child td { border-bottom:0; }
.run-id,.run-error { display:block; margin-top:var(--space-xs); overflow-wrap:anywhere; } .run-error { color:var(--color-error); }
.progress-label { margin-left:var(--space-sm); } .trace-link { color:var(--color-accent-primary); text-decoration:none; font-weight:600; } .trace-link:hover { text-decoration:underline; }
.metric-group + .metric-group { margin-top:var(--space-xl); }
.metric-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:var(--space-md); margin:0 0 var(--space-xl); }
.metric-card { padding:var(--space-lg); border:1px solid var(--color-border-default); border-radius:var(--radius-md); background:var(--color-background-secondary); }
dt { font-size:var(--font-size-sm); color:var(--color-text-secondary); } dd { margin:var(--space-sm) 0 0; font-size:var(--font-size-2xl); font-weight:650; font-variant-numeric:tabular-nums; }
pre { padding:var(--space-md); border-radius:var(--radius-md); background:var(--color-background-secondary); color:var(--color-text-primary); white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font-editor-mono); font-size:var(--font-size-sm); }
.error-banner { margin:0; }
@media(max-width:640px) {
  .benchmark-content > .panel { padding:var(--space-lg); }
  .form-grid { grid-template-columns:minmax(0,1fr); } .dataset-field { grid-column:auto; }
  .section-heading,.config-footer { align-items:flex-start; flex-wrap:wrap; } .config-footer .button-primary { width:100%; }
  .metric-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } dd { font-size:var(--font-size-xl); }
}
</style>
