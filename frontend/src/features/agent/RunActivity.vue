<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ApiAgentRun, AgentEvent } from '@/contracts'
import api from '@/services/apiClient'
import { getAgentTrace, cancelAgentRun, respondToPermission } from '@/services/agentService'
import { useWorkspaceStore } from '@/stores/workspace'
import BudgetConfirmation from './BudgetConfirmation.vue'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import { runStatusLabel, toolLabel } from './labels'
import { t } from '@/i18n'
import { useCitationNavigation } from '@/composables/useCitationNavigation'
import { getNote } from '@/services/noteService'
import { getTask } from '@/services/taskService'
import AppDialog from '@/components/common/AppDialog.vue'
import type { TaskItem } from '@/contracts'
const props = defineProps<{ runId: string }>()
const workspace = useWorkspaceStore()
const run = ref<ApiAgentRun>()
const events = ref<AgentEvent[]>([])
const permissions = ref<AgentEvent[]>([])
const error = ref('')
const busy = ref(false)
const expanded = ref(false)
const selectedTask = ref<TaskItem>()
const { openCitation } = useCitationNavigation()
const artifacts = computed(() => events.value.filter(event => event.event === 'ToolResult' && event.data.success
  && ['notes.create', 'notes.update', 'notes.patch_markdown', 'tasks.create', 'tasks.update'].includes(String(event.data.name)))
  .map(event => ({ event, output: event.data.output as Record<string, unknown> | undefined }))
  .filter(item => item.output && (typeof item.output.note_id === 'string' || typeof item.output.task_id === 'string')))
async function openArtifact(output: Record<string, unknown>) {
  const version = generation
  try {
    if (typeof output.task_id === 'string') { const task = await getTask(output.task_id); if (version === generation) selectedTask.value = task }
    else if (typeof output.note_id === 'string') { const note = await getNote(output.note_id); if (version === generation) await openCitation(note) }
  } catch (cause) { if (version === generation) error.value = String(cause) }
}
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let refreshId = 0
let cursor = -1
const terminal = () => ['completed', 'failed', 'cancelled'].includes(run.value?.status || '')
async function refresh(version = generation) {
  const request = ++refreshId
  clearTimeout(timer)
  try {
    const current = await api.get<ApiAgentRun>(`/api/agent/runs/${props.runId}`)
    if (version !== generation || request !== refreshId) return
    run.value = current
    let more = true
    while (more) {
      const trace = await getAgentTrace(props.runId, { after_sequence: cursor, limit: 200 })
      if (version !== generation || request !== refreshId) return
      for (const event of trace.items) {
        if (event.sequence <= cursor) continue
        events.value.push(event)
        cursor = event.sequence
        if (event.event === 'PermissionRequired') permissions.value.push(event)
        if (event.event === 'PermissionResolved') permissions.value = permissions.value.filter(item => item.data.request_id !== event.data.request_id)
        if (event.event === 'ToolResult') permissions.value = permissions.value.filter(item => (item.data.tool_call as Record<string, unknown>)?.tool_call_id !== event.data.tool_call_id)
      }
      more = trace.has_more
      if (events.value.length > 1000) events.value = events.value.slice(-1000)
    }
    if (terminal()) permissions.value = []
    error.value = ''
    if (!terminal()) timer = setTimeout(() => void refresh(version), 2000)
  } catch (cause) { if (version === generation && request === refreshId) error.value = cause instanceof Error ? cause.message : String(cause) }
}
watch(() => [props.runId, workspace.vaultId], () => {
  generation++
  clearTimeout(timer)
  cursor = -1; run.value = undefined; events.value = []; permissions.value = []; selectedTask.value = undefined; error.value = ''; busy.value = false
  void refresh()
}, { immediate: true })
onBeforeUnmount(() => { generation++; clearTimeout(timer) })
async function act(operation: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  const version = generation
  try { await operation(); if (version === generation) await refresh(version) }
  catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
</script>

<template>
  <section class="run-activity panel" :aria-label="t('智能体执行', 'Agent execution')">
    <div class="inline-actions"><strong>{{ t('任务执行', 'Task execution') }}</strong><span class="badge">{{ runStatusLabel(run?.status) }}</span>
      <button class="button-secondary" :disabled="busy" @click="refresh()">{{ t('刷新', 'Refresh') }}</button>
      <button v-if="run && !terminal()" class="button-danger" :disabled="busy" @click="act(() => cancelAgentRun(runId))">{{ t('停止', 'Stop') }}</button>
    </div>
    <p v-if="error || run?.error_message" class="error-banner" role="alert">{{ error || run?.error_message }}</p>
    <div v-if="artifacts.length" class="inline-actions"><button v-for="item in artifacts" :key="item.event.sequence" class="button-secondary" @click="openArtifact(item.output!)">{{ item.output!.task_id ? t('查看任务', 'View task') : t('打开笔记', 'Open note') }}: {{ item.output!.title || item.output!.file_path || t('执行产出', 'Created result') }}</button></div>
    <BudgetConfirmation :run-id="runId" :status="run?.status" :events="events" @resolved="refresh()" />
    <section v-for="permission in permissions" :key="String(permission.data.request_id)" class="permission-card">
      <strong>{{ t('等待操作授权', 'Permission required') }} · {{ toolLabel(String((permission.data.tool_call as Record<string, unknown>)?.name)) }}</strong>
      <details><summary>{{ t('查看操作内容', 'Review operation') }}</summary><pre>{{ JSON.stringify(permission.data.tool_call, null, 2) }}</pre></details>
      <div class="inline-actions">
        <button class="button-primary" :disabled="busy" @click="act(() => respondToPermission(runId, String(permission.data.request_id), 'allow_once'))">{{ t('允许本次', 'Allow once') }}</button>
        <button class="button-danger" :disabled="busy" @click="act(() => respondToPermission(runId, String(permission.data.request_id), 'deny'))">{{ t('拒绝', 'Deny') }}</button>
      </div>
    </section>
    <details :open="expanded" @toggle="expanded = ($event.target as HTMLDetailsElement).open"><summary>{{ t('执行详情与结果', 'Execution details and result') }} · {{ run?.current_step || 0 }} {{ t('步', 'steps') }}</summary>
      <template v-if="expanded">
        <MarkdownContent v-if="run?.output" :source="run.output" />
        <div v-for="event in events.filter(item => item.event === 'ToolResult')" :key="event.sequence" class="operation-line">
          {{ event.data.success ? t('已执行', 'Executed') : t('失败', 'Failed') }} · {{ toolLabel(String(event.data.name)) }}
          <details><summary>{{ t('结果', 'Result') }}</summary><pre>{{ JSON.stringify(event.data.output ?? event.data.error_message, null, 2) }}</pre></details>
        </div>
      </template>
    </details>
    <a :href="`#/agent/runs/${runId}`">{{ t('打开完整运行记录', 'Open full run record') }}</a>
    <AppDialog v-if="selectedTask" :label="selectedTask.title" @close="selectedTask = undefined"><div class="modal"><h2>{{ selectedTask.title }}</h2><MarkdownContent :source="selectedTask.description || ''" /><button class="button-secondary" @click="selectedTask = undefined">{{ t('关闭', 'Close') }}</button></div></AppDialog>
  </section>
</template>

<style scoped>
.run-activity { display: grid; gap: var(--space-sm); min-width: 0; margin-block: var(--space-sm); }
pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--font-size-xs); }
.permission-card { border: 1px solid var(--color-border-default); border-radius: var(--radius-md); padding: var(--space-md); background: var(--color-surface-secondary); }
.operation-line { margin-block: var(--space-sm); }
summary { cursor: pointer; }
</style>
