import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { AgentRun, AgentEvent, ToolDefinition, PermissionRequest, ToolCall } from '@/contracts'
import { mockAgentRuns, mockAgentEvents, mockTools, mockPermissionRequest } from '@/services/agentService'
import * as agentService from '@/services/agentService'

export const useAgentStore = defineStore('agent', () => {
  const runs = ref<AgentRun[]>(mockAgentRuns)
  const activeRunId = ref<string | null>('run-1')
  const events = ref<AgentEvent[]>(mockAgentEvents.filter((e) => e.run_id === 'run-1'))
  const tools = ref<ToolDefinition[]>(mockTools)
  const isCreating = ref(false)
  const isRunning = ref(false)
  const permissionRequest = ref<PermissionRequest | null>(null)
  const toolCalls = ref<ToolCall[]>([])

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
    activeRunId.value = runId
    events.value = mockAgentEvents.filter((e) => e.run_id === runId)
    toolCalls.value = []
    for (const evt of events.value) {
      if (evt.event === 'ToolCall') {
        const data = evt.data as any
        toolCalls.value.push({
          tool_call_id: data.tool_call_id,
          name: data.name,
          parameters: data.parameters,
          status: data.status || 'completed',
          started_at: evt.timestamp,
        })
      } else if (evt.event === 'ToolResult') {
        const data = evt.data as any
        const tc = toolCalls.value.find((t) => t.tool_call_id === data.tool_call_id)
        if (tc) {
          tc.status = data.status
          tc.result = data.result
          tc.completed_at = evt.timestamp
        }
      }
    }
  }

  async function createRun(request: agentService.CreateAgentRunRequest) {
    isCreating.value = true
    try {
      const run = await agentService.createAgentRun(request)
      runs.value.unshift(run)
      activeRunId.value = run.run_id
      events.value = [{
        event: 'RunStarted',
        sequence: 1,
        run_id: run.run_id,
        data: { task: request.task },
        timestamp: new Date().toISOString(),
      }]
      isRunning.value = true
      // Mock events streaming
      simulateRun(run.run_id)
      return run
    } finally {
      isCreating.value = false
    }
  }

  function simulateRun(runId: string) {
    const runEvents: AgentEvent[] = [
      { event: 'ThinkingDelta', sequence: 2, run_id: runId, data: { text: '我需要先搜索相关笔记...' }, timestamp: new Date().toISOString() },
      { event: 'ToolCall', sequence: 3, run_id: runId, data: { tool_call_id: 'tc-mock-1', name: 'notes.search', parameters: { query: '红黑树', limit: 5 }, status: 'running' }, timestamp: new Date().toISOString() },
      { event: 'ToolResult', sequence: 4, run_id: runId, data: { tool_call_id: 'tc-mock-1', name: 'notes.search', status: 'completed', result: '找到 5 条相关结果' }, timestamp: new Date().toISOString() },
      { event: 'TextDelta', sequence: 5, run_id: runId, data: { text: '根据你的笔记，以下是...' }, timestamp: new Date().toISOString() },
      { event: 'RunCompleted', sequence: 6, run_id: runId, data: { message: 'Task completed successfully' }, timestamp: new Date().toISOString() },
    ]
    let idx = 0
    const push = () => {
      if (idx >= runEvents.length) {
        isRunning.value = false
        return
      }
      events.value.push(runEvents[idx])
      idx++
      setTimeout(push, 800)
    }
    setTimeout(push, 500)
  }

  async function cancelRun(runId: string) {
    await agentService.cancelAgentRun(runId)
    const run = runs.value.find((r) => r.run_id === runId)
    if (run) run.status = 'cancelled'
    isRunning.value = false
  }

  async function respondPermission(decision: 'allow' | 'deny', scope: 'once' | 'session' | 'always' = 'once') {
    if (!activeRunId.value || !permissionRequest.value) return
    await agentService.respondToPermission(activeRunId.value, permissionRequest.value.request_id, decision, scope)
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
