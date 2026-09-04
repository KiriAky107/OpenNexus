<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiClient } from '@/services/apiClient'
interface Usage {totals: Record<string,number|null>;coverage:Record<string,number>;request_count:number;complete_requests:number;cache_hit_rate:number|null;cache_covered_requests:number;options:{provider_id:string;model:string;source:string}[]}
const data = ref<Usage | null>(null)
const period = ref('7')
const provider = ref('')
const model = ref('')
const source = ref('')
const start = ref('')
const end = ref('')
const busy = ref(false)
const error = ref('')
const metrics: Record<string,string> = {input_tokens:'输入 Token',output_tokens:'输出 Token',total_tokens:'总 Token',cache_hit_tokens:'缓存命中',cache_miss_tokens:'缓存未命中',cache_write_tokens:'缓存写入',reasoning_tokens:'推理 Token'}
async function load() {
  busy.value = true; error.value = ''
  try {
    const until = period.value === 'custom' ? new Date(end.value) : new Date()
    const from = period.value === 'custom' ? new Date(start.value) : new Date(until)
    if (period.value === 'today') from.setHours(0,0,0,0)
    else if (period.value !== 'custom') from.setDate(from.getDate() - Number(period.value))
    if (!Number.isFinite(from.getTime()) || !Number.isFinite(until.getTime()) || until <= from) throw new Error('请选择有效的开始与结束时间。')
    data.value = await apiClient.get<Usage>('/api/usage', {params: {start:from.toISOString(),end:until.toISOString(),provider_id:provider.value || undefined,model:model.value || undefined,source:source.value || undefined}})
  } catch(e) { error.value = (e as Error).message } finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <section class="panel usage-card"><header><h3>Token 消耗情况</h3><button class="button-secondary" :disabled="busy" @click="load">{{ busy ? '加载中…' : '刷新统计' }}</button></header>
    <div class="filters"><label>时间<select v-model="period" class="select" @change="period !== 'custom' && load()"><option value="today">今日</option><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="custom">自定义</option></select></label>
      <label>提供商<select v-model="provider" class="select" @change="model = ''; load()"><option value="">全部</option><option v-for="id in [...new Set(data?.options.map(o => o.provider_id) || [])]" :key="id">{{ id }}</option></select></label>
      <label>模型<select v-model="model" class="select" @change="load"><option value="">全部</option><option v-for="id in [...new Set(data?.options.filter(o => !provider || o.provider_id === provider).map(o => o.model) || [])]" :key="id">{{ id }}</option></select></label>
      <label>来源<select v-model="source" class="select" @change="load"><option value="">全部</option><option value="api">远程 API</option><option value="local">本地服务</option></select></label>
    </div>
    <div v-if="period === 'custom'" class="filters"><label>开始<input v-model="start" class="input" type="datetime-local" /></label><label>结束<input v-model="end" class="input" type="datetime-local" /></label><button class="button-secondary" @click="load">应用时间段</button></div>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <template v-if="data"><p v-if="!data.request_count" class="subtle">该时间段没有已记录的模型请求。</p>
      <div class="usage-grid"><div v-for="(label,key) in metrics" :key="key"><small>{{ label }}</small><strong>{{ data.totals[key] === null ? '未提供' : data.totals[key]?.toLocaleString() }}</strong><small>覆盖 {{ data.coverage[key] }} / {{ data.request_count }} 次</small></div>
      <div><small>缓存命中率</small><strong>{{ data.cache_hit_rate === null ? '未提供' : `${(data.cache_hit_rate * 100).toFixed(1)}%` }}</strong><small>覆盖 {{ data.cache_covered_requests }} 次</small></div></div>
      <p class="subtle">请求 {{ data.request_count }} 次，其中完整结束 {{ data.complete_requests }} 次。输入总量包含厂商已报告的缓存，推理 Token 不重复加入输出。</p>
    </template><p class="subtle">统计为本应用观测值，不是厂商账户账单。缺失指标显示“未提供”，历史未记录的数据不补估。</p>
  </section>
</template>
<style scoped>.usage-card{display:grid;gap:16px;padding:20px}.usage-card header,.filters{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.usage-card header{justify-content:space-between}.filters label{display:grid;gap:5px}.usage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:16px}.usage-grid>div{display:grid;gap:8px}.usage-grid strong{font-size:22px}</style>
