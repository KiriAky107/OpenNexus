import apiClient from './apiClient'
import { SseClient } from './sseClient'
import type { AgentRun, AgentEvent, AgentTraceResponse, ApiAgentRun, OperationResponse, PageMeta, ToolDefinition } from '@/contracts'

function toAgentRun(run: ApiAgentRun): AgentRun {
  // API 的 token_usage 是累计值，UI 模型预留了输入/输出拆分字段。
  return {
    run_id: run.run_id,
    status: run.status,
    current_step: run.current_step,
    max_steps: run.max_steps,
    token_usage: {
      total_tokens: run.token_usage,
    },
    started_at: run.created_at,
    completed_at: ['completed', 'failed', 'cancelled'].includes(run.status) ? run.updated_at : undefined,
    error: run.error_message ?? undefined,
  }
}

export async function listAgentRuns(params?: {
  limit?: number
  offset?: number
}): Promise<{ items: AgentRun[]; total: number }> {
  const response = await apiClient.get<{ items: ApiAgentRun[]; page: PageMeta }>('/api/agent/runs', { params })
  return { items: response.items.map(toAgentRun), total: response.page.total }
}

export async function getAgentRun(runId: string): Promise<AgentRun> {
  return toAgentRun(await apiClient.get<ApiAgentRun>(`/api/agent/runs/${runId}`))
}

export interface CreateAgentRunRequest {
  input: string
  provider_id: string
  model: string
  skill_id?: string
  allowed_tools?: string[]
  max_steps?: number
  tool_timeout_seconds?: number
  run_timeout_seconds?: number
  token_budget?: number | null
  allow_network?: boolean
  max_concurrent_tools?: number
}

export async function createAgentRun(request: CreateAgentRunRequest): Promise<AgentRun> {
  return toAgentRun(await apiClient.post<ApiAgentRun>('/api/agent/runs', request))
}

export async function cancelAgentRun(runId: string): Promise<OperationResponse> {
  return apiClient.post(`/api/agent/runs/${runId}/cancel`)
}

export async function getAgentTrace(
  runId: string,
  params?: { after_sequence?: number; limit?: number },
): Promise<AgentTraceResponse> {
  return apiClient.get(`/api/agent/runs/${runId}/trace`, { params })
}

export async function listTools(): Promise<ToolDefinition[]> {
  const response = await apiClient.get<{ items: ToolDefinition[] }>('/api/tools')
  return response.items
}

export function streamAgentEvents(
  runId: string,
  handlers: {
    onEvent?: (event: AgentEvent) => void
    onError?: (error: Error) => void
    onDone?: () => void
    onOpen?: () => void
  },
  afterSequence = -1,
): SseClient {
  // 将通用 SSE 包装成领域事件，Store 无需了解传输层 envelope。
  const client = new SseClient({
    url: `/api/agent/runs/${runId}/events?after_sequence=${afterSequence}`,
    method: 'GET',
    lastEventId: afterSequence >= 0 ? String(afterSequence) : undefined,
    onEvent: (eventName, data) => {
      handlers.onEvent?.({
        event: eventName as AgentEvent['event'],
        sequence: (data.sequence as number) || 0,
        run_id: (data.run_id as string) || runId,
        data: (data.data || data) as Record<string, unknown>,
        timestamp: (data.timestamp as string) || new Date().toISOString(),
      })
    },
    onError: handlers.onError,
    onDone: handlers.onDone,
    onOpen: handlers.onOpen,
  })
  client.connect().catch(() => {})
  return client
}

export async function respondToPermission(
  runId: string,
  requestId: string,
  decision: 'allow_once' | 'allow_session' | 'deny'
): Promise<OperationResponse> {
  return apiClient.post(`/api/agent/runs/${runId}/permissions/${requestId}`, {
    decision,
  })
}
