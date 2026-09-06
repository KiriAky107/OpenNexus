<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue'
import UsageChart from './UsageChart.vue'
import type { UsageBucket } from './UsageChart.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { DataAnalysis, Download, Upload, Coin, CircleCheck, CircleClose, FolderAdd, Cpu, PieChart, Microphone, Refresh } from '@element-plus/icons-vue'
import { apiClient } from '@/services/apiClient'
import { t } from '@/i18n'
interface Usage {series?:UsageBucket[];audio_request_count:number;audio_seconds:number|null;audio_covered_requests:number;totals: Record<string,number|null>;coverage:Record<string,number>;request_count:number;complete_requests:number;cache_hit_rate:number|null;cache_covered_requests:number;options:{provider_id:string;model:string;source:string}[]}
const data = ref<Usage | null>(null)
const period = ref('7')
const provider = ref('')
const model = ref('')
const source = ref('')
const start = ref('')
const end = ref('')
const busy = ref(false)
const error = ref('')
let generation = 0
const metrics = computed<Record<string,string>>(() => ({input_tokens:t('输入 Token','Input tokens'),output_tokens:t('输出 Token','Output tokens'),total_tokens:t('总 Token','Total tokens'),cache_hit_tokens:t('缓存命中','Cache hits'),cache_miss_tokens:t('缓存未命中','Cache misses'),cache_write_tokens:t('缓存写入','Cache writes'),reasoning_tokens:t('推理 Token','Reasoning tokens')}))
const metricIcons: Record<string, Component> = {
  input_tokens: Download, output_tokens: Upload, total_tokens: Coin,
  cache_hit_tokens: CircleCheck, cache_miss_tokens: CircleClose,
  cache_write_tokens: FolderAdd, reasoning_tokens: Cpu,
}
async function load() {
  const current = ++generation
  busy.value = true; error.value = ''
  try {
    const until = period.value === 'custom' ? new Date(end.value) : new Date()
    const from = period.value === 'custom' ? new Date(start.value) : new Date(until)
    if (period.value === 'today') from.setHours(0,0,0,0)
    else if (period.value !== 'custom') { from.setDate(from.getDate() - Number(period.value) + 1); from.setHours(0,0,0,0) }
    if (!Number.isFinite(from.getTime()) || !Number.isFinite(until.getTime()) || until <= from) throw new Error(t('请选择有效的开始与结束时间。', 'Choose a valid start and end time.'))
    const result = await apiClient.get<Usage>('/api/usage', {params: {timezone_offset: -new Date().getTimezoneOffset(), start:from.toISOString(),end:until.toISOString(),provider_id:provider.value || undefined,model:model.value || undefined,source:source.value || undefined}})
    if (current === generation) data.value = result
  } catch(e) { if (current === generation) { error.value = (e as Error).message; data.value = null } } finally { if (current === generation) busy.value = false }
}
onMounted(load)
</script>
<template>
  <section class="panel usage-card"><header><h3><AppIcon :icon="DataAnalysis" :size="20" />{{ t('Token 消耗情况', 'Token Usage') }}</h3><button class="button-secondary" :disabled="busy" @click="load"><AppIcon :icon="Refresh" :size="16" />{{ busy ? t('加载中…', 'Loading…') : t('刷新统计', 'Refresh') }}</button></header>
    <div class="filters"><label>{{ t('时间', 'Period') }}<select v-model="period" class="select" @change="period !== 'custom' && load()"><option value="today">{{ t('今日', 'Today') }}</option><option value="7">{{ t('近 7 天', 'Last 7 days') }}</option><option value="30">{{ t('近 30 天', 'Last 30 days') }}</option><option value="90">{{ t('近三个月（90 天）', 'Last 3 months (90 days)') }}</option><option value="custom">{{ t('自定义', 'Custom') }}</option></select></label>
      <label>{{ t('提供商', 'Provider') }}<select v-model="provider" class="select" @change="model = ''; load()"><option value="">{{ t('全部', 'All') }}</option><option v-for="id in [...new Set(data?.options.map(o => o.provider_id) || [])]" :key="id">{{ id }}</option></select></label>
      <label>{{ t('模型', 'Model') }}<select v-model="model" class="select" @change="load"><option value="">{{ t('全部', 'All') }}</option><option v-for="id in [...new Set(data?.options.filter(o => !provider || o.provider_id === provider).map(o => o.model) || [])]" :key="id">{{ id }}</option></select></label>
      <label>{{ t('来源', 'Source') }}<select v-model="source" class="select" @change="load"><option value="">{{ t('全部', 'All') }}</option><option value="api">{{ t('远程 API', 'Remote API') }}</option><option value="local">{{ t('本地服务', 'Local service') }}</option></select></label>
    </div>
    <div v-if="period === 'custom'" class="filters"><label>{{ t('开始', 'Start') }}<input v-model="start" class="input" type="datetime-local" /></label><label>{{ t('结束', 'End') }}<input v-model="end" class="input" type="datetime-local" /></label><button class="button-secondary" @click="load">{{ t('应用时间段', 'Apply period') }}</button></div>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <template v-if="data"><p v-if="!data.request_count" class="subtle">{{ t('该时间段没有已记录的模型请求。', 'No model requests were recorded during this period.') }}</p>
      <UsageChart v-if="data.series" :buckets="data.series" />
      <div class="usage-grid"><div v-for="(label,key) in metrics" :key="key"><small class="metric-label"><span class="metric-icon"><AppIcon :icon="metricIcons[key]!" :size="18" /></span>{{ label }}</small><strong>{{ data.totals[key] === null ? t('未提供', 'Unavailable') : data.totals[key]?.toLocaleString() }}</strong><small>{{ t('覆盖', 'Coverage') }} {{ data.coverage[key] }} / {{ data.request_count }} {{ t('次', 'requests') }}</small></div>
      <div><small class="metric-label"><span class="metric-icon"><AppIcon :icon="PieChart" :size="18" /></span>{{ t('缓存命中率', 'Cache hit rate') }}</small><strong>{{ data.cache_hit_rate === null ? t('未提供', 'Unavailable') : `${(data.cache_hit_rate * 100).toFixed(1)}%` }}</strong><small>{{ t('覆盖', 'Coverage') }} {{ data.cache_covered_requests }}</small></div></div>
      <p class="subtle audio-usage"><AppIcon :icon="Microphone" :size="16" />{{ t('音频调用', 'Audio calls') }} {{ data.audio_request_count ?? 0 }} · {{ t('时长', 'Duration') }} {{ data.audio_seconds == null ? t('未提供', 'Unavailable') : `${data.audio_seconds.toFixed(2)} ${t('秒', 'sec')}` }} ({{ t('覆盖', 'coverage') }} {{ data.audio_covered_requests ?? 0 }}; {{ t('重试分别计数', 'retries counted separately') }})</p>
      <p class="subtle">{{ t('请求', 'Requests') }} {{ data.request_count }}, {{ t('其中完整结束', 'completed') }} {{ data.complete_requests }}. {{ t('输入总量包含厂商已报告的缓存，推理 Token 不重复加入输出。', 'Input totals include provider-reported cache tokens; reasoning tokens are not added to output twice.') }}</p>
      <details class="cache-explanation ui-disclosure"><summary>{{ t('缓存统计如何计算？', 'How are cache statistics calculated?') }}</summary><p>{{ t('命中、写入优先使用厂商报告字段；有输入总量和命中数时，未命中可由输入减命中得到。命中率为同时提供命中与未命中的请求中，命中 Token 合计 ÷ 输入 Token 合计；缺少输入总量时使用命中加未命中作为分母。Anthropic 输入总量包含读取及写入缓存。', 'Cache hits and writes use reported counters. Misses can be input minus hits. The hit rate divides hits by input tokens for requests reporting both hits and misses, using hits plus misses when input is absent. Anthropic input includes cache reads and writes.') }}</p><p>{{ t('0 表示已报告零值；未提供表示字段缺失。聊天次数不会自动折算为缓存，是否命中由厂商决定。本地 Embedding 通常不报告缓存字段。', 'Zero means a reported zero; unavailable means missing. Conversation counts do not imply cache hits. Local embedding typically does not report cache counters.') }}</p></details>
    </template><p class="subtle">{{ t('统计为本应用观测值，不是厂商账户账单。缺失指标显示“未提供”，历史未记录的数据不补估。', 'Statistics are application observations, not provider billing. Missing metrics stay unavailable and historical gaps are not estimated.') }}</p>
  </section>
</template>
<style scoped>
.usage-card h3, .usage-card header button, .metric-label, .audio-usage { display: flex; align-items: center; gap: 8px; }
.usage-card h3 > .app-icon, .audio-usage > .app-icon { color: var(--color-accent-primary); }
.metric-icon { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: var(--radius-sm); background: var(--color-background-secondary); color: var(--color-accent-primary); flex-shrink: 0; }
.usage-card{display:grid;gap:16px;padding:20px}.usage-card header,.filters{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.usage-card header{justify-content:space-between}.filters label{display:grid;gap:5px}.usage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:16px}.usage-grid>div{display:grid;gap:8px}.usage-grid strong{font-size:22px}</style>
