<script setup lang="ts">
import { computed, ref } from 'vue'
import { Cpu, Connection } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'
interface ModelUsage { key: string; model: string; provider_id: string; requests: number; totals: Record<string, number | null>; coverage: Record<string, number> }
export interface UsageBucket {
  date: string
  end_date: string
  local: { requests: number; totals: Record<string, number | null>; coverage: Record<string, number>; models?: ModelUsage[] }
  api: { requests: number; totals: Record<string, number | null>; coverage: Record<string, number>; models?: ModelUsage[] }
}
const props = defineProps<{ buckets: UsageBucket[] }>()
const metric = ref('input_tokens')
const hovered = ref<string | null>(null)
const activeBucket = computed(() => props.buckets.find(bucket => bucket.date === hovered.value))
const sums = computed(() => sources.map(source => ({ source, value: props.buckets.reduce((sum, bucket) => sum + (value(bucket, source) ?? 0), 0), coverage: props.buckets.reduce((sum, bucket) => sum + (metric.value === 'requests' ? bucket[source].requests : bucket[source].coverage[metric.value] ?? 0), 0), requests: props.buckets.reduce((sum, bucket) => sum + bucket[source].requests, 0) })))
const total = computed(() => sums.value.reduce((sum, item) => sum + item.value, 0))
const pie = computed(() => total.value ? `conic-gradient(var(--color-info) 0 ${sums.value[0]!.value / total.value * 100}%, var(--color-accent-primary) 0 100%)` : 'var(--color-border-subtle)')
const sources = ['local', 'api'] as const
const labels = computed(() => ({ local: t('本地模型', 'Local models'), api: t('模型提供商', 'Providers') }))
const metricLabel = computed(() => metric.value === 'requests' ? t('请求次数', 'Requests') : metric.value === 'input_tokens' ? t('输入 Token', 'Input tokens') : metric.value === 'output_tokens' ? t('输出 Token', 'Output tokens') : t('总 Token', 'Total tokens'))
function value(bucket: UsageBucket, source: 'local' | 'api') {
  const item = bucket[source]
  return metric.value === 'requests' ? item.requests : item.requests === 0 ? 0 : item.totals[metric.value] ?? null
}
const modelLegend = computed(() => sources.flatMap(source => {
  const entries = new Map<string, ModelUsage & { consumed: number }>()
  for (const bucket of props.buckets) for (const item of bucket[source].models ?? []) {
    const previous = entries.get(item.key)
    entries.set(item.key, {...item, consumed: (previous?.consumed ?? 0) + modelValue(item)})
  }
  const peak = Math.max(1, ...[...entries.values()].map(item => item.consumed))
  return [...entries.values()].sort((a, b) => b.consumed - a.consumed || a.key.localeCompare(b.key)).map(item => ({ ...item, source, shade: 40 + item.consumed / peak * 55 }))
}))
function modelColor(key: string, source: 'local' | 'api') {
  const shade = modelLegend.value.find(item => item.key === key && item.source === source)?.shade ?? 85
  return `color-mix(in srgb, var(${source === 'local' ? '--color-info' : '--color-accent-primary'}) ${shade}%, var(--color-surface-primary))`
}
function modelValue(item: ModelUsage) { return metric.value === 'requests' ? item.requests : item.totals[metric.value] ?? 0 }
function modelDescription(item: ModelUsage) { return `${item.model} · ${metricLabel.value}: ${metric.value !== 'requests' && item.totals[metric.value] == null ? t('未提供', 'Unavailable') : modelValue(item).toLocaleString()} · ${t('覆盖', 'Coverage')} ${metric.value === 'requests' ? item.requests : item.coverage[metric.value] ?? 0}/${item.requests}` }
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
    <div class="chart-layout"><div class="bar-pane">
    <p class="subtle chart-scale">{{ metricLabel }} · 0 — {{ maximum.toLocaleString() }}</p>
    <div class="chart-scroll" tabindex="0" :aria-label="t('按日期横向滚动查看柱状图', 'Scroll the chart by date')">
      <div class="chart-columns" :style="{ minWidth: `${buckets.length * 14}px` }">
        <div v-for="bucket in buckets" :key="bucket.date" class="chart-column" :class="{ highlighted: hovered === bucket.date }" tabindex="0" @mouseenter="hovered = bucket.date" @mouseleave="hovered = null" @focus="hovered = bucket.date" @blur="hovered = null">
          <div class="chart-bars">
            <div v-for="source in sources" :key="source" class="usage-bar" :class="[source, { missing: value(bucket, source) === null, stacked: bucket[source].models?.length }]"
              :style="{ height: value(bucket, source) === null ? '8px' : `${(value(bucket, source) ?? 0) / maximum * 160}px` }"
              role="img" :aria-label="description(bucket, source)" :title="description(bucket, source)">
              <span v-for="item in bucket[source].models ?? []" :key="item.key" class="model-segment" :style="{ height: `${value(bucket, source) ? modelValue(item) / value(bucket, source)! * 100 : 0}%`, backgroundColor: modelColor(item.key, source) }" :title="modelDescription(item)" />
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="chart-readout" aria-live="polite"><template v-if="activeBucket"><strong>{{ activeBucket.date }}</strong><span v-for="source in sources" :key="source" :class="source">{{ description(activeBucket, source) }}<small v-for="item in activeBucket[source].models ?? []" :key="item.key" class="model-readout">{{ modelDescription(item) }}</small></span></template><span v-else>{{ t('悬停或聚焦日期查看数据', 'Hover or focus a date for details') }}</span></div>
    </div><aside class="pie-pane"><h4>{{ t('来源占比', 'Usage by source') }}</h4>
      <div class="usage-pie" role="img" :aria-label="sums.map(item => `${labels[item.source]}: ${item.value}`).join(' · ')" :style="{ background: pie }"><div><strong>{{ total.toLocaleString() }}</strong><small>{{ metricLabel }}</small></div></div>
      <div v-for="item in sums" :key="item.source" class="pie-value" :class="item.source"><AppIcon :icon="item.source === 'local' ? Cpu : Connection" :size="16" /><span>{{ labels[item.source] }}</span><strong>{{ item.coverage ? item.value.toLocaleString() : item.requests ? t('未提供', 'Unavailable') : '0' }} · {{ total ? (item.value / total * 100).toFixed(1) + '%' : '—' }}</strong><small>{{ t('覆盖', 'Coverage') }} {{ item.coverage }}/{{ item.requests }}</small></div>
      <p class="subtle">{{ t('占比仅基于已报告值；缺失指标不计入分母。', 'Shares use reported values only; missing counters are excluded.') }}</p>
    </aside></div>
    <p v-if="modelLegend.length" class="subtle">{{ t('同一来源内，颜色越深表示所选时段该模型的累计消耗越多。', 'Within each source, darker shades indicate greater model usage over the selected period.') }}</p>
    <div class="model-legend"><span v-for="item in modelLegend" :key="`${item.source}:${item.key}`" :title="item.provider_id"><i :style="{ background: modelColor(item.key, item.source) }" />{{ item.model }}</span></div>
    <p class="subtle">{{ t('按本机时区分组；柱高仅汇总已报告值，悬停可查看覆盖请求数。虚线表示有请求但未提供该指标，不作为零消耗。', 'Grouped by your local UTC offset. Bars sum reported values; hover for coverage. Dashed markers mean requests with unavailable counters, not zero usage.') }}</p>
    <details class="ui-disclosure"><summary>{{ t('查看图表数据', 'View chart data') }}</summary><div class="chart-scroll"><table><thead><tr><th>{{ t('日期', 'Date') }}</th><th>{{ labels.local }}</th><th>{{ labels.api }}</th></tr></thead><tbody><tr v-for="bucket in buckets" :key="bucket.date"><th>{{ bucket.date }}<template v-if="bucket.date !== bucket.end_date"> – {{ bucket.end_date }}</template></th><td v-for="source in sources" :key="source">{{ description(bucket, source) }}</td></tr></tbody></table></div></details>
  </section>
</template>

<style scoped>
.usage-bar.stacked { display: flex; flex-direction: column-reverse; background: transparent; overflow: hidden; }
.model-segment { width: 100%; flex-shrink: 0; border-top: 1px solid var(--color-surface-primary); box-sizing: border-box; }
.api .model-segment { background-image: repeating-linear-gradient(45deg, transparent 0 4px, #ffffff30 4px 7px); }
.model-legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px; margin-top: 14px; }.model-legend span { display: inline-flex; align-items: center; gap: 5px; overflow-wrap: anywhere; }.model-legend i { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }.model-readout { display: block; }

.chart-layout { display: grid; grid-template-columns: minmax(0, 2fr) minmax(220px, 1fr); gap: 24px; margin-top: 16px; }
.bar-pane { min-width: 0; }.pie-pane { border-left: 1px dashed var(--color-border-default); padding-left: 24px; min-width: 0; }
.usage-pie { width: 170px; aspect-ratio: 1; margin: 20px auto; border-radius: 50%; display: grid; place-items: center; }
.usage-pie > div { width: 112px; aspect-ratio: 1; border-radius: 50%; background: var(--color-surface-primary); display: flex; flex-direction: column; align-items: center; justify-content: center; }.usage-pie strong { font-size: 20px; }.usage-pie small { font-size: 11px; }
.pie-value { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12px; margin: 10px 0; }.pie-value small { width: 100%; color: var(--color-text-secondary); }.pie-pane > p { font-size: 12px; }
.chart-column { transition: background-color 150ms; border-radius: 4px; }.chart-column.highlighted { background: var(--color-accent-soft); outline: 1px solid var(--color-border-focus); outline-offset: -1px; }.highlighted .usage-bar { filter: brightness(1.15); }
.chart-readout { min-height: 76px; font-size: 12px; display: flex; flex-direction: column; gap: 4px; margin-top: 12px; padding: 10px; border: 1px dashed var(--color-border-subtle); border-radius: var(--radius-sm); background: var(--color-surface-primary); overflow-wrap: anywhere; }
@media (max-width: 760px) { .chart-layout { grid-template-columns: 1fr; }.pie-pane { border-left: 0; border-top: 1px dashed var(--color-border-default); padding: 20px 0 0; } }

.usage-chart { min-width: 0; padding: 16px; border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); background: var(--color-background-primary); }
.chart-toolbar, .chart-legend, .chart-legend span { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.chart-toolbar { justify-content: space-between; }.chart-toolbar .select { width: auto; }.chart-legend { font-size: 12px; }
.local { color: var(--color-info); }.api { color: var(--color-accent-primary); }
.chart-scale { margin: 12px 0 0; font-size: 12px; }.chart-scroll { overflow-x: auto; padding-bottom: 2px; }.chart-columns { display: flex; gap: 2px; }
.chart-column { flex: 1; min-width: 12px; text-align: center; }
.chart-bars { height: 176px; display: flex; align-items: flex-end; justify-content: center; gap: 2px; border-bottom: 1px solid var(--color-border-default); background: repeating-linear-gradient(to top, transparent 0 39px, var(--color-border-subtle) 39px 40px); }
.usage-bar { width: 35%; max-width: 28px; background: currentColor; border-radius: 3px 3px 0 0; }.usage-bar.api { background: repeating-linear-gradient(45deg, currentColor 0 4px, color-mix(in srgb, currentColor 45%, var(--color-surface-primary)) 4px 7px); }
.usage-bar.missing { background: transparent; border: 1px dashed currentColor; box-sizing: border-box; }
.usage-chart > p:last-of-type { font-size: 12px; margin: 12px 0; }.usage-chart summary { cursor: pointer; color: var(--color-text-link); font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }th, td { padding: 8px; text-align: left; border-bottom: 1px solid var(--color-border-subtle); }
</style>
