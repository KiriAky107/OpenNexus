<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import apiClient from '@/services/apiClient'
import { t } from '@/i18n'

interface LogEntry { id: number; timestamp: string; level: string; source: string; event: string; details: Record<string, unknown> }
interface LogPage { items: LogEntry[]; next_cursor: number | null; sources: string[]; pending: number; dropped: number; write_failures: number; retention: number }
const page = ref<LogPage>({ items: [], next_cursor: null, sources: [], pending: 0, dropped: 0, write_failures: 0, retention: 20000 })
const level = ref(''), source = ref(''), query = ref(''), error = ref('')
const loading = ref(false), live = ref(true), following = ref(true)
const scroller = ref<HTMLElement>()
const atLatest = ref(true)
const MAX_VISIBLE = 500
let applied = { level: '', source: '', q: '' }
let revision = 0
let timer: ReturnType<typeof setInterval> | undefined
let adjusting = false
async function load(reset = false, older = false) {
  if (older && (loading.value || !page.value.next_cursor)) return
  if (reset) applied = { level: level.value, source: source.value, q: query.value.trim() }
  const version = ++revision
  const viewport = scroller.value
  const oldHeight = viewport?.scrollHeight ?? 0
  const oldTop = viewport?.scrollTop ?? 0
  // 添加历史记录并修剪相对边缘时保留可见行。
  const anchor = older && viewport ? [...viewport.querySelectorAll<HTMLElement>('[data-log-id]')].find(row => row.getBoundingClientRect().bottom > viewport.getBoundingClientRect().top) : undefined
  const anchorTop = anchor?.getBoundingClientRect().top
  const anchorId = anchor?.dataset.logId
  loading.value = true
  try {
    const result = await apiClient.get<LogPage>('/api/logs', { params: { limit: 50, before: older ? page.value.next_cursor ?? undefined : undefined, ...applied } })
    if (version !== revision) return
    // 如果读者在刷新期间滚动离开，请保持他们的视图不变。
    if (!reset && !older && !following.value) return
    const previous = page.value.items
    const overlaps = result.items.some(item => previous.some(old => old.id === item.id))
    const combined = older || (!reset && overlaps) ? [...previous, ...result.items] : result.items
    const all = [...new Map(combined.map(item => [item.id, item])).values()].sort((a, b) => a.id - b.id)
    const trimmed = all.length > MAX_VISIBLE
    const items = older ? all.slice(0, MAX_VISIBLE) : all.slice(-MAX_VISIBLE)
    let cursor = older || reset || !overlaps ? result.next_cursor : page.value.next_cursor
    if (!older && trimmed) cursor = items[0]?.id ?? null
    if (older && trimmed) atLatest.value = false
    if (!older) atLatest.value = true
    adjusting = true
    page.value = { ...result, items, next_cursor: cursor }
    error.value = ''
    await nextTick()
    if (version !== revision) return
    if (viewport) {
      if (older) {
        const retained = anchorId ? viewport.querySelector<HTMLElement>(`[data-log-id="${anchorId}"]`) : undefined
        viewport.scrollTop = retained && anchorTop != null ? oldTop + retained.getBoundingClientRect().top - anchorTop : oldTop + viewport.scrollHeight - oldHeight
      } else if (reset || following.value) {
        viewport.scrollTop = viewport.scrollHeight
        following.value = true
      }
    }
  } catch (reason) {
    if (version === revision) error.value = reason instanceof Error ? reason.message : t('日志加载失败', 'Failed to load logs')
  } finally { if (version === revision) { loading.value = false; adjusting = false } }
}
function onScroll() {
  const viewport = scroller.value
  if (!viewport || adjusting) return
  following.value = atLatest.value && viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop < 24
  if (viewport.scrollTop < 80 && viewport.scrollHeight > viewport.clientHeight && !error.value) void load(false, true)
}
onMounted(() => {
  void load(true)
  timer = setInterval(() => { if (live.value && following.value && !loading.value && !document.hidden) void load() }, 5000)
})
onUnmounted(() => { revision++; clearInterval(timer) })
</script>

<template>
  <section class="feature-page logs-page">
    <header class="feature-header"><div><h1>{{ t('运行日志', 'Operation logs') }}</h1><p>{{ t('集中查看向量模型、智能体、任务与后台操作。', 'Inspect models, agents, tasks and background operations.') }}</p></div><button class="button-secondary" :disabled="loading" @click="load(true)">{{ t('刷新', 'Refresh') }}</button></header>
    <form class="panel log-filters" @submit.prevent="load(true)">
      <label class="field">{{ t('级别', 'Level') }}<select v-model="level" class="select"><option value="">{{ t('全部', 'All') }}</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select></label>
      <label class="field">{{ t('模块', 'Module') }}<select v-model="source" class="select"><option value="">{{ t('全部', 'All') }}</option><option v-for="item in page.sources" :key="item">{{ item }}</option></select></label>
      <label class="field log-search">{{ t('事件、错误码或关联 ID', 'Event, error code or correlation ID') }}<input v-model="query" class="input" maxlength="200" /></label>
      <button class="button-primary" :disabled="loading">{{ t('筛选', 'Filter') }}</button>
      <label><input v-model="live" type="checkbox" /> {{ t('自动跟随新日志', 'Follow new logs') }}</label>
    </form>
    <p class="subtle">{{ t('本地保留最近', 'Locally retains the latest') }} {{ page.retention.toLocaleString() }} {{ t('条日志；不记录正文、提示词、工具参数及密钥。', 'events; excludes content, prompts, tool arguments and credentials.') }}</p>
    <p v-if="page.dropped || page.write_failures" class="error-banner" role="alert">{{ t('日志存储不完整：队列溢出', 'Incomplete logging: queue overflow') }} {{ page.dropped }} · {{ t('写入失败', 'Write failures') }} {{ page.write_failures }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <div class="inline-actions log-controls"><span class="subtle" role="status">{{ live && following ? t('正在跟随最新日志', 'Following latest logs') : t('已暂停跟随，可自由查看历史', 'Following paused; browse history freely') }}</span><button class="button-secondary" :disabled="loading" @click="live = true; load(true)">{{ t('回到最新', 'Back to latest') }}</button><span class="subtle">{{ t('待写入', 'Pending') }} {{ page.pending }}</span></div>
    <div ref="scroller" class="panel log-list" :aria-busy="loading" tabindex="0" :aria-label="t('日志列表，向上滚动加载历史', 'Log list; scroll up for history')" @scroll.passive="onScroll">
      <div class="history-status"><button v-if="page.next_cursor" class="button-secondary" :disabled="loading" @click="load(false, true)">{{ loading ? t('加载中…', 'Loading…') : t('向上滚动加载更早日志', 'Scroll up for older logs') }}</button><span v-else class="subtle">{{ t('已到保留日志的开头', 'Beginning of retained logs') }}</span></div>
      <p v-if="!page.items.length">{{ loading ? t('加载中…', 'Loading…') : t('暂无符合条件的日志', 'No matching logs') }}</p>
      <details v-for="entry in page.items" :key="entry.id" :data-log-id="entry.id" class="ui-disclosure log-entry">
        <summary><span class="badge" :class="{ error: entry.level === 'ERROR' || entry.level === 'CRITICAL', warning: entry.level === 'WARNING' }">{{ entry.level }}</span><time>{{ new Date(entry.timestamp).toLocaleString() }}</time><span>{{ entry.source }}</span><strong>{{ entry.event }}</strong></summary>
        <dl><template v-for="(value, key) in entry.details" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl>
      </details>
    </div>
  </section>
</template>

<style scoped>
.logs-page > * { width: 100%; max-width: 1180px; margin-inline: auto; box-sizing: border-box; }
.logs-page > .subtle { margin-block: var(--space-md); }
.log-controls { margin-block: var(--space-md); }
.log-list { height: min(65vh, 800px); min-height: 240px; overflow-y: auto; overscroll-behavior: contain; overflow-anchor: none; scroll-behavior: auto; }
.history-status { text-align: center; margin-bottom: var(--space-md); }
.log-filters { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-md); }
.log-filters .field { min-width: 140px; margin: 0; }
.log-search { flex: 1; }
.log-entry { border-bottom: 1px solid var(--color-border-subtle); padding: var(--space-sm); }
.log-entry + .log-entry { margin-top: var(--space-xs); }
.log-entry summary { display: flex; flex-wrap: wrap; gap: var(--space-sm); cursor: pointer; align-items: center; overflow-wrap: anywhere; }
.log-entry summary::after { margin-left: auto; }
.log-entry dl { display: grid; grid-template-columns: minmax(100px, 160px) 1fr; gap: var(--space-sm); font-size: var(--font-size-sm); }
.log-entry dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; min-width: 0; }
.log-entry dt, .log-entry time { color: var(--color-text-secondary); }
</style>
