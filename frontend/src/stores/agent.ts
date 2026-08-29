import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { AgentRun, AgentEvent, ToolDefinition, PermissionRequest, ToolCall } from '@/contracts'
import { mockAgentRuns, mockAgentEvents, mockTools, mockPermissionRequest } from '@/services/agentService'
import * as agentService from '@/services/agentService'
import type { SseClient } from '@/services/sseClient'

export const useAgentStore = defineStore('agent', () => {
  const runs = ref<AgentRun[]>(mockAgentRuns)
  const activeRunId = ref<string | null>('run-1')
  const events = ref<AgentEvent[]>(mockAgentEvents.filter((e) => e.run_id === 'run-1'))
  const tools = ref<ToolDefinition[]>(mockTools)
  const isCreating = ref(false)
  const isRunning = ref(false)
  const permissionRequest = ref<PermissionRequest | null>(null)
  const toolCalls = ref<ToolCall[]>([])
  const error = ref<string | null>(null)
  let eventStream: SseClient | null = null

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
    eventStream?.cancel()
    activeRunId.value = runId
    const run = await agentService.getAgentRun(runId)
    const existingIndex = runs.value.findIndex((item) => item.run_id === runId)
    if (existingIndex >= 0) runs.value[existingIndex] = run
    else runs.value.unshift(run)
    events.value = []
    toolCalls.value = []
    permissionRequest.value = null
    subscribe(runId)
  }

  function processEvent(event: AgentEvent) {
    if (events.value.some((item) => item.run_id === event.run_id && item.sequence === event.sequence)) return
    events.value.push(event)
    events.value.sort((a, b) => a.sequence - b.sequence)
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
        impact: '该工具需要获得权限后才能继续执行。',
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
    eventStream?.cancel()
    isRunning.value = true
    error.value = null
    eventStream = agentService.streamAgentEvents(runId, {
      onEvent: processEvent,
      onError(streamError) { error.value = streamError.message; isRunning.value = false },
      onDone() { isRunning.value = false; eventStream = null },
    })
  }

  async function createRun(request: agentService.CreateAgentRunRequest) {
    isCreating.value = true
    try {
      const run = await agentService.createAgentRun(request)
      runs.value.unshift(run)
      activeRunId.value = run.run_id
      events.value = []
      toolCalls.value = []
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
    isRunning.value = false
    eventStream?.cancel()
    eventStream = null
  }

  async function respondPermission(decision: 'allow' | 'deny', scope: 'once' | 'session' = 'once') {
    if (!activeRunId.value || !permissionRequest.value) return
    const apiDecision = decision === 'deny' ? 'deny' : scope === 'session' ? 'allow_session' : 'allow_once'
    await agentService.respondToPermission(activeRunId.value, permissionRequest.value.request_id, apiDecision)
    permissionRequest.value = null
  }

  function showPermissionDemo() {
    permissionRequest.value = mockPermissionRequest
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
    currentStep,
    loadTools,
    loadRuns,
    loadRun,
    createRun,
    cancelRun,
    respondPermission,
    showPermissionDemo,
  }
})
