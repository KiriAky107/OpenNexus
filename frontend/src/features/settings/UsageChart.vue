<script setup lang="ts">
import { computed, ref } from 'vue'
import { Cpu, Connection } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'
export interface UsageBucket {
  date: string
  end_date: string
  local: { requests: number; totals: Record<string, number | null>; coverage: Record<string, number> }
  api: { requests: number; totals: Record<string, number | null>; coverage: Record<string, number> }
}
const props = defineProps<{ buckets: UsageBucket[] }>()
const metric = ref('input_tokens')
const sources = ['local', 'api'] as const
const labels = computed(() => ({ local: t('本地模型', 'Local models'), api: t('模型提供商', 'Providers') }))
const metricLabel = computed(() => metric.value === 'requests' ? t('请求次数', 'Requests') : metric.value === 'input_tokens' ? t('输入 Token', 'Input tokens') : metric.value === 'output_tokens' ? t('输出 Token', 'Output tokens') : t('总 Token', 'Total tokens'))
function value(bucket: UsageBucket, source: 'local' | 'api') {
  const item = bucket[source]
  return metric.value === 'requests' ? item.requests : item.requests === 0 ? 0 : item.totals[metric.value] ?? null
}
function description(bucket: UsageBucket, source: 'local' | 'api') {
  const count = value(bucket, source)
  const coverage = metric.value === 'requests' ? '' : ` · ${t('覆盖', 'Coverage')} ${bucket[source].coverage[metric.value] ?? 0}/${bucket[source].requests}`
  return `${bucket.date}${bucket.end_date !== bucket.date ? ' – ' + bucket.end_date : ''} · ${labels.value[source]} · ${metricLabel.value}: ${count === null ? t('未提供', 'Unavailable') : count.toLocaleString()}${coverage}`
}
const maximum = computed(() => Math.max(1, ...props.buckets.flatMap(bucket => sources.map(source => value(bucket, source) ?? 0))))
</script>

<template>
  <section class="usage-chart" :aria-label="t('使用趋势', 'Usage trend')">
    <div class="chart-toolbar">
      <h4>{{ t('使用趋势', 'Usage trend') }}</h4>
      <select v-model="metric" class="select" :aria-label="t('图表统计指标', 'Chart metric')">
        <option value="input_tokens">{{ t('输入 Token', 'Input tokens') }}</option>
        <option value="output_tokens">{{ t('输出 Token', 'Output tokens') }}</option>
        <option value="total_tokens">{{ t('总 Token', 'Total tokens') }}</option>
        <option value="requests">{{ t('请求次数', 'Requests') }}</option>
      </select>
      <div class="chart-legend"><span class="local"><AppIcon :icon="Cpu" :size="16" />{{ labels.local }} · {{ t('实色', 'Solid') }}</span><span class="api"><AppIcon :icon="Connection" :size="16" />{{ labels.api }} · {{ t('斜纹', 'Striped') }}</span></div>
    </div>
    <p class="subtle chart-scale">{{ metricLabel }} · 0 — {{ maximum.toLocaleString() }}</p>
    <div class="chart-scroll" tabindex="0" :aria-label="t('按日期横向滚动查看柱状图', 'Scroll the chart by date')">
      <div class="chart-columns" :style="{ minWidth: `${buckets.length * 48}px` }">
        <div v-for="bucket in buckets" :key="bucket.date" class="chart-column">
          <div class="chart-bars">
            <div v-for="source in sources" :key="source" class="usage-bar" :class="[source, { missing: value(bucket, source) === null }]"
              :style="{ height: value(bucket, source) === null ? '8px' : `${(value(bucket, source) ?? 0) / maximum * 160}px` }"
              role="img" :aria-label="description(bucket, source)" :title="description(bucket, source)" />
          </div>
          <small :title="`${bucket.date} – ${bucket.end_date}`">{{ bucket.date.slice(5) }}</small>
        </div>
      </div>
    </div>
    <p class="subtle">{{ t('按本机时区分组；柱高仅汇总已报告值，悬停可查看覆盖请求数。虚线表示有请求但未提供该指标，不作为零消耗。', 'Grouped by your local UTC offset. Bars sum reported values; hover for coverage. Dashed markers mean requests with unavailable counters, not zero usage.') }}</p>
    <details><summary>{{ t('查看图表数据', 'View chart data') }}</summary><div class="chart-scroll"><table><thead><tr><th>{{ t('日期', 'Date') }}</th><th>{{ labels.local }}</th><th>{{ labels.api }}</th></tr></thead><tbody><tr v-for="bucket in buckets" :key="bucket.date"><th>{{ bucket.date }}<template v-if="bucket.date !== bucket.end_date"> – {{ bucket.end_date }}</template></th><td v-for="source in sources" :key="source">{{ description(bucket, source) }}</td></tr></tbody></table></div></details>
  </section>
</template>

<style scoped>
.usage-chart { min-width: 0; padding: 16px; border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); background: var(--color-background-primary); }
.chart-toolbar, .chart-legend, .chart-legend span { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.chart-toolbar { justify-content: space-between; }.chart-toolbar .select { width: auto; }.chart-legend { font-size: 12px; }
.local { color: var(--color-info); }.api { color: var(--color-accent-primary); }
.chart-scale { margin: 12px 0 0; font-size: 12px; }.chart-scroll { overflow-x: auto; padding-bottom: 8px; }.chart-columns { display: flex; gap: 8px; }
.chart-column { flex: 1; min-width: 40px; text-align: center; }.chart-column small { font-size: 11px; color: var(--color-text-secondary); white-space: nowrap; }
.chart-bars { height: 176px; display: flex; align-items: flex-end; justify-content: center; gap: 4px; border-bottom: 1px solid var(--color-border-default); background: repeating-linear-gradient(to top, transparent 0 39px, var(--color-border-subtle) 39px 40px); }
.usage-bar { width: 14px; max-width: 35%; background: currentColor; border-radius: 3px 3px 0 0; }.usage-bar.api { background: repeating-linear-gradient(45deg, currentColor 0 4px, color-mix(in srgb, currentColor 45%, var(--color-surface-primary)) 4px 7px); }
.usage-bar.missing { background: transparent; border: 1px dashed currentColor; box-sizing: border-box; }
.usage-chart > p:last-of-type { font-size: 12px; margin: 12px 0; }.usage-chart summary { cursor: pointer; color: var(--color-text-link); font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }th, td { padding: 8px; text-align: left; border-bottom: 1px solid var(--color-border-subtle); }
</style>
