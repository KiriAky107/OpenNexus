import { defineStore } from 'pinia'
import { ref, computed, onScopeDispose } from 'vue'
import type { AgentRun, AgentEvent, ToolDefinition, PermissionRequest, ToolCall } from '@/contracts'
import * as agentService from '@/services/agentService'
import type { SseClient } from '@/services/sseClient'
import { t } from '@/i18n'

export const useAgentStore = defineStore('agent', () => {
  const runs = ref<AgentRun[]>([])
  const activeRunId = ref<string | null>(null)
  const events = ref<AgentEvent[]>([])
  const tools = ref<ToolDefinition[]>([])
  const isCreating = ref(false)
  const isRunning = ref(false)
  const permissionRequest = ref<PermissionRequest | null>(null)
  const toolCalls = ref<ToolCall[]>([])
  const error = ref<string | null>(null)
  let eventStream: SseClient | null = null
  let selectionVersion = 0
  let streamVersion = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryCount = 0
  const seenSequences = new Set<number>()
  let lastSequence = -1
  const connectionState = ref<'idle' | 'connected' | 'reconnecting' | 'disconnected'>('idle')
  const terminal = (status?: string) => ['completed', 'failed', 'cancelled'].includes(status || '')

  function stopStream() {
    streamVersion++
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
    eventStream?.cancel()
    eventStream = null
  }
  function resetEvents() {
    events.value = []
    toolCalls.value = []
    seenSequences.clear()
    lastSequence = -1
    retryCount = 0
  }
  onScopeDispose(stopStream)

  const activeRun = computed(() =>
    runs.value.find((r) => r.run_id === activeRunId.value) || null
  )

  const sortedRuns = computed(() =>
    [...runs.value].sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''))
  )

  const currentStep = computed(() => {
    const tc = events.value.filter((e) => e.event === 'ToolCall').length
    return tc
  })

  async function loadTools() {
    tools.value = await agentService.listTools()
  }

  async function loadRuns() {
    const resp = await agentService.listAgentRuns()
    runs.value = resp.items
  }

  async function loadRun(runId: string) {
    const version = ++selectionVersion
    stopStream()
    activeRunId.value = runId
    resetEvents()
    permissionRequest.value = null
    isRunning.value = false
    const run = await agentService.getAgentRun(runId)
    if (version !== selectionVersion) return
    const existingIndex = runs.value.findIndex((item) => item.run_id === runId)
    if (existingIndex >= 0) runs.value[existingIndex] = run
    else runs.value.unshift(run)
    events.value = []
    toolCalls.value = []
    permissionRequest.value = null
    subscribe(runId)
  }

  function processEvent(event: AgentEvent) {
    // 服务端会先回放历史再发送实时事件，以 run_id + sequence 去重保证幂等。
    if (seenSequences.has(event.sequence)) return
    seenSequences.add(event.sequence)
    if (event.sequence > lastSequence) events.value.push(event)
    else {
      const index = events.value.findIndex(item => item.sequence > event.sequence)
      events.value.splice(index < 0 ? events.value.length : index, 0, event)
    }
    lastSequence = Math.max(lastSequence, event.sequence)
    const data = event.data
    const run = runs.value.find((item) => item.run_id === event.run_id)
    if (event.event === 'RunStarted' && run) run.status = 'running'
    if (event.event === 'ToolCall') {
      toolCalls.value.push({
        tool_call_id: String(data.tool_call_id ?? ''),
        name: String(data.name ?? 'unknown'),
        parameters: (data.arguments ?? {}) as Record<string, unknown>,
        status: 'running',
        started_at: event.timestamp,
      })
    } else if (event.event === 'ToolResult') {
      const toolCall = toolCalls.value.find((item) => item.tool_call_id === data.tool_call_id)
      if (toolCall) {
        toolCall.status = data.success ? 'completed' : 'error'
        toolCall.result = data.output == null ? undefined : JSON.stringify(data.output)
        toolCall.error_code = data.error_code == null ? undefined : String(data.error_code)
        toolCall.error_message = data.error_message == null ? undefined : String(data.error_message)
        toolCall.completed_at = event.timestamp
      }
      permissionRequest.value = null
      if (run?.status === 'waiting_permission') run.status = 'running'
    } else if (event.event === 'PermissionRequired') {
      const call = (data.tool_call ?? {}) as Record<string, unknown>
      permissionRequest.value = {
        request_id: String(data.request_id ?? ''),
        run_id: event.run_id,
        tool_name: String(call.name ?? 'unknown'),
        permission: String(data.permission ?? ''),
        parameters: (call.arguments ?? {}) as Record<string, unknown>,
        impact: t('该工具需要获得权限后才能继续执行。', 'This tool requires permission before it can continue.'),
      }
      if (run) run.status = 'waiting_permission'
    } else if (['RunCompleted', 'RunFailed', 'RunCancelled'].includes(event.event)) {
      isRunning.value = false
      permissionRequest.value = null
      if (run) {
        run.status = event.event === 'RunCompleted' ? 'completed' : event.event === 'RunFailed' ? 'failed' : 'cancelled'
        run.completed_at = event.timestamp
      }
    }
  }

  function subscribe(runId: string) {
    stopStream()
    const version = streamVersion
    const current = () => activeRunId.value === runId && version === streamVersion
    isRunning.value = !terminal(activeRun.value?.status)
    const interrupted = (cause?: Error) => {
      if (!current() || retryTimer) return
      eventStream?.cancel()
      eventStream = null
      if (terminal(activeRun.value?.status)) {
        isRunning.value = false
        connectionState.value = 'idle'
        return
      }
      error.value = cause?.message || t('事件连接中断', 'Event connection interrupted')
      connectionState.value = retryCount >= 5 ? 'disconnected' : 'reconnecting'
      if (retryCount >= 5) return
      const delay = Math.min(1000 * 2 ** retryCount++, 16000)
      retryTimer = setTimeout(async () => {
        retryTimer = null
        try {
          const run = await agentService.getAgentRun(runId)
          if (!current()) return
          const index = runs.value.findIndex(item => item.run_id === runId)
          if (index >= 0) runs.value[index] = run
          // 即使已结束仍续读一次缺失的尾部事件，保留完整 Trace。
          subscribe(runId)
        } catch (cause) {
          if (current()) interrupted(cause instanceof Error ? cause : new Error(String(cause)))
        }
      }, delay)
    }
    eventStream = agentService.streamAgentEvents(runId, {
      onOpen() { if (current()) { connectionState.value = 'connected'; error.value = null } },
      onEvent(event) { if (current()) processEvent(event) },
      onError: interrupted,
      onDone() { interrupted() },
    }, lastSequence)
  }

  function reconnect() {
    if (!activeRunId.value) return
    retryCount = 0
    subscribe(activeRunId.value)
  }

  async function createRun(request: agentService.CreateAgentRunRequest) {
    isCreating.value = true
    try {
      const run = await agentService.createAgentRun(request)
      selectionVersion++
      runs.value.unshift(run)
      activeRunId.value = run.run_id
      resetEvents()
      subscribe(run.run_id)
      return run
    } finally {
      isCreating.value = false
    }
  }

  async function cancelRun(runId: string) {
    await agentService.cancelAgentRun(runId)
    const run = runs.value.find((r) => r.run_id === runId)
    if (run) run.status = 'cancelled'
    if (activeRunId.value === runId) {
      isRunning.value = false
      permissionRequest.value = null
      stopStream()
      connectionState.value = 'idle'
    }
  }

  async function respondPermission(decision: 'allow' | 'deny', scope: 'once' | 'session' = 'once') {
    if (!activeRunId.value || !permissionRequest.value) return
    const apiDecision = decision === 'deny' ? 'deny' : scope === 'session' ? 'allow_session' : 'allow_once'
    await agentService.respondToPermission(activeRunId.value, permissionRequest.value.request_id, apiDecision)
    permissionRequest.value = null
  }

  return {
    runs,
    activeRunId,
    activeRun,
    sortedRuns,
    events,
    tools,
    isCreating,
    isRunning,
    permissionRequest,
    toolCalls,
    error,
    connectionState,
    reconnect,
    currentStep,
    loadTools,
    loadRuns,
    loadRun,
    createRun,
    cancelRun,
    respondPermission,
  }
})
