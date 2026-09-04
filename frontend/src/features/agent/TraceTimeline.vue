<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TraceNode, AgentEvent } from '@/contracts'
import { buildTraceNodes, getToolCallsFromEvents, getTotalDuration } from '@/services/traceService'
import { eventLabel } from './labels'

const props = defineProps<{
  events: AgentEvent[]
  runStatus?: string
}>()

const emit = defineEmits<{
  (e: 'open-citation', data: Record<string, unknown>): void
}>()

// 子树展开与「查看本节点数据」是两件事：
// 叶子节点没有子树，但依然需要能看自己的 data，
// 所以两个状态集合分开维护，不能共用一个 expanded。
const expandedNodes = ref<Set<string>>(new Set())
const detailNodes = ref<Set<string>>(new Set())
const viewMode = ref<'timeline' | 'tree'>('timeline')
const showDetails = ref(true)

const traceNodes = computed(() => buildTraceNodes(props.events))
const toolCalls = computed(() => getToolCallsFromEvents(props.events))
const totalDuration = computed(() => getTotalDuration(props.events))

const summaryStats = computed(() => {
  const events = props.events
  return {
    totalEvents: events.length,
    modelCalls: events.filter((e) => e.event === 'ModelCallStarted').length,
    toolCalls: events.filter((e) => e.event === 'ToolCall').length,
    citations: events.filter((e) => e.event === 'Citation').length,
    errors: events.filter((e) => e.event.endsWith('Failed') || e.event === 'RunFailed').length,
  }
})

function toggle(set: Set<string>, nodeId: string) {
  if (set.has(nodeId)) {
    set.delete(nodeId)
  } else {
    set.add(nodeId)
  }
}

/** 展开/收起子树，只对有 children 的节点有意义。 */
function toggleExpand(nodeId: string) {
  toggle(expandedNodes.value, nodeId)
}

function isExpanded(nodeId: string): boolean {
  return expandedNodes.value.has(nodeId)
}

/** 查看/隐藏本节点自身的数据，任何节点（含叶子）都可用。 */
function toggleDetail(nodeId: string) {
  toggle(detailNodes.value, nodeId)
}

function isDetailOpen(nodeId: string): boolean {
  return detailNodes.value.has(nodeId)
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

function getNodeIcon(type: TraceNode['type']): string {
  const icons: Record<TraceNode['type'], string> = {
    run: '▶',
    model_call: '🤖',
    tool_call: '🔧',
    tool_result: '✅',
    text: '💬',
    thinking: '🧠',
    citation: '📚',
    usage: '📊',
    permission: '🔒',
    error: '❌',
    complete: '🏁',
  }
  return icons[type] ?? '•'
}

function getNodeStatusClass(node: TraceNode): string {
  switch (node.status) {
    case 'running': return 'status-running'
    case 'completed': return 'status-completed'
    case 'error': return 'status-error'
    case 'pending': return 'status-pending'
    case 'cancelled': return 'status-cancelled'
    default: return 'status-completed'
  }
}

/** 引用节点带 file_path 才能定位到笔记块。 */
function isCitationNode(node: TraceNode): boolean {
  return node.type === 'citation' && typeof node.data.file_path === 'string'
}

function prettyData(data: Record<string, unknown>): string {
  const filtered = { ...data }
  if (typeof filtered.output === 'string' && filtered.output.length > 500) {
    filtered.output = filtered.output.slice(0, 500) + '...'
  }
  return JSON.stringify(filtered, null, 2)
}

function flatNodes(nodes: TraceNode[], depth = 0): Array<{ node: TraceNode; depth: number }> {
  const result: Array<{ node: TraceNode; depth: number }> = []
  for (const node of nodes) {
    result.push({ node, depth })
    if (node.children.length > 0 && isExpanded(node.id)) {
      result.push(...flatNodes(node.children, depth + 1))
    }
  }
  return result
}

const flatTrace = computed(() => flatNodes(traceNodes.value))
</script>

<template>
  <div class="trace-visualization">
    <div class="trace-header">
      <div class="trace-stats">
        <div class="stat-item">
          <span class="stat-value">{{ summaryStats.totalEvents }}</span>
          <span class="stat-label">事件</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ summaryStats.modelCalls }}</span>
          <span class="stat-label">模型调用</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ summaryStats.toolCalls }}</span>
          <span class="stat-label">工具调用</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ summaryStats.citations }}</span>
          <span class="stat-label">引用</span>
        </div>
        <div class="stat-item">
          <span class="stat-value duration">{{ totalDuration > 0 ? formatDuration(totalDuration) : '-' }}</span>
          <span class="stat-label">总耗时</span>
        </div>
      </div>
      <div class="trace-controls">
        <div class="view-toggle">
          <button :class="{ active: viewMode === 'timeline' }" @click="viewMode = 'timeline'">时间线</button>
          <button :class="{ active: viewMode === 'tree' }" @click="viewMode = 'tree'">树形</button>
        </div>
        <button class="detail-toggle" @click="showDetails = !showDetails">
          {{ showDetails ? '隐藏详情' : '显示详情' }}
        </button>
      </div>
    </div>

    <div v-if="viewMode === 'timeline'" class="timeline-view">
      <div class="timeline">
        <article
          v-for="event in events"
          :key="event.sequence"
          class="event-card"
          :class="{ expanded: isDetailOpen(`event-${event.sequence}`) }"
        >
          <div class="event-dot" :class="`dot-${event.event}`"></div>
          <div class="event-content" @click="toggleDetail(`event-${event.sequence}`)">
            <div class="event-header">
              <span class="event-badge" :class="{
                success: event.event === 'RunCompleted' || event.event === 'ModelCallCompleted',
                error: event.event.endsWith('Failed') || event.event === 'RunFailed',
                warning: event.event === 'PermissionRequired',
                info: event.event === 'ToolCall' || event.event === 'ModelCallStarted',
              }">{{ eventLabel(event.event as any) }}</span>
              <span class="event-time">{{ formatTime(event.timestamp) }}</span>
            </div>
            <div v-if="event.data.text || event.data.message" class="event-text">
              {{ (event.data.text || event.data.message) as string }}
            </div>
            <div v-else-if="event.data.name" class="event-name">
              <code>{{ event.data.name as string }}</code>
              <span v-if="event.data.duration_ms != null" class="event-duration">
                {{ formatDuration(event.data.duration_ms as number) }}
              </span>
            </div>
            <div v-if="event.event === 'Usage'" class="event-usage">
              <span class="total">累计: {{ event.data.token_usage ?? '-' }} tokens</span>
            </div>
            <div v-if="event.event === 'Citation'" class="event-citation" @click.stop="emit('open-citation', event.data)">
              <span class="cite-icon">📎</span>
              <span>{{ (event.data.heading_path || event.data.note_title || event.data.file_path) as string }}</span>
            </div>
            <div v-if="event.event === 'PermissionRequired'" class="event-permission">
              <span class="perm-label">权限:</span>
              <code>{{ event.data.permission as string }}</code>
            </div>
          </div>
          <div v-if="isDetailOpen(`event-${event.sequence}`) && showDetails" class="event-detail">
            <details open>
              <summary>完整数据</summary>
              <pre>{{ prettyData(event.data) }}</pre>
            </details>
          </div>
        </article>

        <div v-if="!events.length" class="empty-state">
          <div><strong>等待执行轨迹</strong><p>事件连接建立后将在这里实时显示。</p></div>
        </div>
      </div>
    </div>

    <div v-else class="tree-view">
      <div v-for="item in flatTrace" :key="item.node.id" class="tree-node" :style="{ paddingLeft: `${item.depth * 24 + 8}px` }">
        <div
          class="node-row"
          :class="[getNodeStatusClass(item.node), { 'detail-open': isDetailOpen(item.node.id) }]"
          role="button"
          tabindex="0"
          :aria-expanded="isDetailOpen(item.node.id)"
          @click="toggleDetail(item.node.id)"
          @keydown.enter.prevent="toggleDetail(item.node.id)"
          @keydown.space.prevent="toggleDetail(item.node.id)"
        >
          <button
            v-if="item.node.children.length"
            type="button"
            class="expand-icon"
            :aria-label="isExpanded(item.node.id) ? '收起子调用' : `展开 ${item.node.children.length} 个子调用`"
            @click.stop="toggleExpand(item.node.id)"
          >
            {{ isExpanded(item.node.id) ? '▼' : '▶' }}
          </button>
          <span v-else class="expand-icon placeholder"></span>
          <span class="node-icon">{{ getNodeIcon(item.node.type) }}</span>
          <span class="node-title">{{ item.node.title }}</span>
          <span v-if="item.node.subtitle" class="node-subtitle">{{ item.node.subtitle }}</span>
          <span v-if="item.node.duration_ms != null" class="node-duration">
            {{ formatDuration(item.node.duration_ms) }}
          </span>
          <button
            v-if="isCitationNode(item.node)"
            type="button"
            class="node-locate"
            @click.stop="emit('open-citation', item.node.data)"
          >
            定位
          </button>
        </div>
        <div v-if="isDetailOpen(item.node.id) && showDetails" class="node-detail">
          <pre>{{ prettyData(item.node.data) }}</pre>
        </div>
      </div>
      <div v-if="!traceNodes.length" class="empty-state">
        <div><strong>暂无树形数据</strong><p>运行开始后将展示调用树。</p></div>
      </div>
    </div>

    <div v-if="toolCalls.length > 0 && viewMode === 'timeline'" class="tool-calls-summary panel">
      <h3 class="panel-title">工具调用统计</h3>
      <div class="tool-call-list">
        <div v-for="call in toolCalls" :key="call.tool_call_id" class="tool-call-item" :class="call.status">
          <span class="tool-status-dot"></span>
          <code class="tool-name">{{ call.name }}</code>
          <span v-if="call.duration_ms != null" class="tool-duration">
            {{ formatDuration(call.duration_ms) }}
          </span>
          <span class="tool-status-badge" :class="call.status">
            {{ call.status === 'completed' ? '成功' : call.status === 'error' ? '失败' : call.status }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace-visualization {
  display: grid;
  gap: var(--space-lg);
}

.trace-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-md);
  flex-wrap: wrap;
}

.trace-stats {
  display: flex;
  gap: var(--space-lg);
  flex-wrap: wrap;
}

.stat-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value {
  font-size: var(--font-size-xl);
  font-weight: 600;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.stat-value.duration {
  color: var(--color-accent-primary);
}

.stat-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}

.trace-controls {
  display: flex;
  gap: var(--space-sm);
  align-items: center;
}

.view-toggle {
  display: flex;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.view-toggle button {
  padding: 4px 12px;
  background: var(--color-surface-primary);
  border: none;
  border-right: 1px solid var(--color-border-default);
  color: var(--color-text-secondary);
  font-size: var(--font-size-sm);
  cursor: pointer;
  transition: all var(--motion-fast);
}

.view-toggle button:last-child { border-right: none; }
.view-toggle button.active {
  background: var(--color-accent-primary);
  color: var(--color-text-inverse);
}

.detail-toggle {
  padding: 4px 12px;
  background: var(--color-surface-primary);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: var(--font-size-sm);
  cursor: pointer;
}

.detail-toggle:hover { border-color: var(--color-accent-secondary); }

.timeline {
  position: relative;
  display: grid;
  gap: var(--space-sm);
  padding-left: var(--space-md);
}

.timeline::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 7px;
  width: 2px;
  border-radius: var(--radius-full);
  background: var(--color-border-default);
}

.event-card {
  position: relative;
  padding: var(--space-md);
  background: var(--color-surface-primary);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  transition: border-color var(--motion-fast), box-shadow var(--motion-fast);
}

.event-card:hover {
  border-color: var(--color-accent-secondary);
  box-shadow: var(--shadow-sm);
}

.event-dot {
  position: absolute;
  top: 18px;
  left: -22px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color-accent-primary);
  border: 2px solid var(--color-surface-primary);
  box-shadow: 0 0 0 1px var(--color-border-default);
}

.dot-RunStarted, .dot-ModelCallStarted { background: var(--color-accent-primary); }
.dot-RunCompleted, .dot-ModelCallCompleted, .dot-ToolResult { background: var(--color-success); }
.dot-RunFailed, .dot-ModelCallFailed { background: var(--color-error); }
.dot-ToolCall { background: var(--color-info); }
.dot-PermissionRequired { background: var(--color-warning); }
.dot-ThinkingDelta { background: var(--color-text-tertiary); }
.dot-TextDelta { background: var(--color-text-secondary); }
.dot-Citation { background: var(--color-accent-secondary); }
.dot-Usage { background: var(--color-text-tertiary); }

.event-content {
  cursor: pointer;
}

.event-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-xs);
}

.event-badge {
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  background: var(--color-background-tertiary);
  color: var(--color-text-secondary);
}

.event-badge.success {
  background: var(--color-success-soft);
  color: var(--color-success);
}
.event-badge.error {
  background: var(--color-error-soft);
  color: var(--color-error);
}
.event-badge.warning {
  background: var(--color-warning-soft);
  color: var(--color-warning);
}
.event-badge.info {
  background: var(--color-info-soft);
  color: var(--color-info);
}

.event-time {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  font-family: var(--font-ui-mono);
}

.event-text {
  white-space: pre-wrap;
  line-height: var(--line-height-relaxed);
  color: var(--color-text-primary);
  max-height: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.event-name {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.event-name code {
  padding: 2px 6px;
  background: var(--color-background-secondary);
  border-radius: var(--radius-sm);
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-sm);
}

.event-duration {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  font-family: var(--font-ui-mono);
}

.event-usage {
  display: flex;
  gap: var(--space-md);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  font-family: var(--font-ui-mono);
}

.event-usage .total {
  color: var(--color-accent-primary);
  font-weight: 500;
}

.event-citation {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  padding: var(--space-xs) var(--space-sm);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  color: var(--color-accent-primary);
  cursor: pointer;
}

.event-citation:hover { text-decoration: underline; }

.event-permission {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: var(--font-size-sm);
}

.event-permission code {
  padding: 2px 6px;
  background: var(--color-warning-soft);
  color: var(--color-warning);
  border-radius: var(--radius-sm);
  font-family: var(--font-ui-mono);
}

.event-detail {
  margin-top: var(--space-sm);
  padding-top: var(--space-sm);
  border-top: 1px solid var(--color-border-subtle);
}

.event-detail details summary {
  cursor: pointer;
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.event-detail pre {
  margin-top: var(--space-sm);
  max-height: 300px;
  overflow: auto;
  padding: var(--space-sm);
  border-radius: var(--radius-sm);
  background: var(--color-background-secondary);
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-xs);
  white-space: pre-wrap;
  word-break: break-all;
}

.tree-view {
  padding: var(--space-sm) 0;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-surface-primary);
}

.tree-node {
  border-bottom: 1px solid var(--color-border-subtle);
}
.tree-node:last-child { border-bottom: none; }

.node-row {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  padding: 8px 12px;
  cursor: pointer;
  font-size: var(--font-size-sm);
  transition: background-color var(--motion-fast);
}

.node-row:hover { background: var(--color-background-hover); }
.node-row:focus-visible {
  outline: 2px solid var(--color-accent-primary);
  outline-offset: -2px;
}

.node-row.detail-open { background: var(--color-background-secondary); }

.node-row.status-running {
  background: var(--color-info-soft);
}

.node-row.status-error {
  background: var(--color-error-soft);
}

.expand-icon {
  width: 16px;
  padding: 0;
  background: none;
  border: none;
  font-size: 10px;
  color: var(--color-text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
}

.expand-icon.placeholder { visibility: hidden; }

.node-locate {
  padding: 1px 8px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-full);
  background: var(--color-surface-primary);
  color: var(--color-accent-primary);
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
}

.node-locate:hover { border-color: var(--color-accent-primary); }

.node-icon {
  font-size: 14px;
  width: 20px;
  text-align: center;
  flex-shrink: 0;
}

.node-title {
  flex: 1;
  color: var(--color-text-primary);
  font-weight: 500;
}

.node-subtitle {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-xs);
}

.node-duration {
  color: var(--color-text-tertiary);
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-xs);
}

.node-detail {
  padding: 8px 12px 12px 36px;
}

.node-detail pre {
  margin: 0;
  max-height: 200px;
  overflow: auto;
  padding: var(--space-sm);
  border-radius: var(--radius-sm);
  background: var(--color-background-secondary);
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-xs);
  white-space: pre-wrap;
}

.tool-calls-summary { margin-top: var(--space-md); }
.tool-call-list {
  display: grid;
  gap: var(--space-xs);
}

.tool-call-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--color-background-secondary);
  font-size: var(--font-size-sm);
}

.tool-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
}

.tool-call-item.completed .tool-status-dot { background: var(--color-success); }
.tool-call-item.error .tool-status-dot { background: var(--color-error); }
.tool-call-item.running .tool-status-dot { background: var(--color-info); }

.tool-name {
  flex: 1;
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-xs);
}

.tool-duration {
  color: var(--color-text-tertiary);
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-xs);
}

.tool-status-badge {
  padding: 1px 6px;
  border-radius: var(--radius-full);
  font-size: 11px;
}

.tool-status-badge.completed { background: var(--color-success-soft); color: var(--color-success); }
.tool-status-badge.error { background: var(--color-error-soft); color: var(--color-error); }
.tool-status-badge.running { background: var(--color-info-soft); color: var(--color-info); }

.empty-state {
  padding: var(--space-3xl);
  text-align: center;
  color: var(--color-text-tertiary);
}

.empty-state strong {
  display: block;
  color: var(--color-text-secondary);
  margin-bottom: var(--space-xs);
}
</style>
