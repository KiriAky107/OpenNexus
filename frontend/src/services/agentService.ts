import apiClient from './apiClient'
import { SseClient } from './sseClient'
import type { AgentRun, AgentEvent, ApiAgentRun, OperationResponse, PageMeta, ToolDefinition, PermissionRequest } from '@/contracts'

function toAgentRun(run: ApiAgentRun): AgentRun {
  // API 的 token_usage 是累计值，UI 模型预留了输入/输出拆分字段。
  return {
    run_id: run.run_id,
    status: run.status,
    current_step: run.current_step,
    max_steps: run.max_steps,
    token_usage: {
      input_tokens: 0,
      output_tokens: 0,
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
  token_budget?: number
  allow_network?: boolean
  max_concurrent_tools?: number
}

export async function createAgentRun(request: CreateAgentRunRequest): Promise<AgentRun> {
  return toAgentRun(await apiClient.post<ApiAgentRun>('/api/agent/runs', request))
}

export async function cancelAgentRun(runId: string): Promise<OperationResponse> {
  return apiClient.post(`/api/agent/runs/${runId}/cancel`)
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
  }
): SseClient {
  // 将通用 SSE 包装成领域事件，Store 无需了解传输层 envelope。
  const client = new SseClient({
    url: `/api/agent/runs/${runId}/events`,
    method: 'GET',
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

export const mockTools: ToolDefinition[] = [
  {
    name: 'notes.search',
    description: '搜索笔记，支持关键词和语义检索',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '搜索关键词' },
        limit: { type: 'number', description: '返回结果数量' },
      },
      required: ['query'],
    },
    source: 'builtin',
  },
  {
    name: 'notes.read',
    description: '读取指定笔记的完整内容',
    parameters: {
      type: 'object',
      properties: {
        note_id: { type: 'string' },
      },
      required: ['note_id'],
    },
    source: 'builtin',
  },
  {
    name: 'notes.create',
    description: '创建新笔记',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        content: { type: 'string' },
        folder_path: { type: 'string' },
      },
      required: ['title', 'content'],
    },
    source: 'builtin',
  },
  {
    name: 'rag.search',
    description: '基于 RAG 的语义检索，返回相关知识片段',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string' },
        top_k: { type: 'number' },
      },
      required: ['query'],
    },
    source: 'builtin',
  },
  {
    name: 'tasks.create',
    description: '创建任务',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        description: { type: 'string' },
        priority: { type: 'string', enum: ['low', 'medium', 'high'] },
      },
      required: ['title'],
    },
    source: 'builtin',
  },
  {
    name: 'system.echo',
    description: '回显输入内容（测试用）',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string' },
      },
      required: ['text'],
    },
    source: 'builtin',
  },
  {
    name: 'math.add',
    description: '两数相加（测试用）',
    parameters: {
      type: 'object',
      properties: {
        a: { type: 'number' },
        b: { type: 'number' },
      },
      required: ['a', 'b'],
    },
    source: 'builtin',
  },
]

export const mockAgentRuns: AgentRun[] = [
  {
    run_id: 'run-1',
    status: 'completed',
    current_step: 3,
    max_steps: 10,
    token_usage: { input_tokens: 2340, output_tokens: 890, total_tokens: 3230 },
    started_at: '2026-08-25T11:00:00Z',
    completed_at: '2026-08-25T11:02:30Z',
  },
  {
    run_id: 'run-2',
    status: 'running',
    current_step: 2,
    max_steps: 10,
    token_usage: { input_tokens: 1500, output_tokens: 420, total_tokens: 1920 },
    started_at: '2026-08-26T09:30:00Z',
  },
]

export const mockAgentEvents: AgentEvent[] = [
  {
    event: 'RunStarted',
    sequence: 1,
    run_id: 'run-1',
    data: { task: '帮我整理红黑树的核心知识点' },
    timestamp: '2026-08-25T11:00:00Z',
  },
  {
    event: 'ThinkingDelta',
    sequence: 2,
    run_id: 'run-1',
    data: { text: '我需要先搜索笔记中关于红黑树的内容...' },
    timestamp: '2026-08-25T11:00:01Z',
  },
  {
    event: 'ToolCall',
    sequence: 3,
    run_id: 'run-1',
    data: {
      tool_call_id: 'tc-1',
      name: 'notes.search',
      parameters: { query: '红黑树 插入 删除', limit: 5 },
      status: 'running',
    },
    timestamp: '2026-08-25T11:00:02Z',
  },
  {
    event: 'ToolResult',
    sequence: 4,
    run_id: 'run-1',
    data: {
      tool_call_id: 'tc-1',
      name: 'notes.search',
      status: 'completed',
      result: '找到 5 条相关结果，包括红黑树性质、插入操作、删除操作等...',
      duration_ms: 320,
    },
    timestamp: '2026-08-25T11:00:02Z',
  },
  {
    event: 'Citation',
    sequence: 5,
    run_id: 'run-1',
    data: {
      note_id: 'n-rbt',
      block_id: 'b1',
      heading_path: '数据结构 / 红黑树 / 性质',
    },
    timestamp: '2026-08-25T11:00:03Z',
  },
  {
    event: 'ThinkingDelta',
    sequence: 6,
    run_id: 'run-1',
    data: { text: '搜索结果很全面，让我整理一下结构...' },
    timestamp: '2026-08-25T11:00:03Z',
  },
  {
    event: 'TextDelta',
    sequence: 7,
    run_id: 'run-1',
    data: { text: '## 红黑树核心知识点整理\n\n### 1. 基本性质\n红黑树是一种自平衡二叉搜索树，每个节点带有颜色属性...' },
    timestamp: '2026-08-25T11:00:04Z',
  },
  {
    event: 'Usage',
    sequence: 8,
    run_id: 'run-1',
    data: { input_tokens: 2340, output_tokens: 890, total_tokens: 3230 },
    timestamp: '2026-08-25T11:02:30Z',
  },
  {
    event: 'RunCompleted',
    sequence: 9,
    run_id: 'run-1',
    data: { message: 'Task completed successfully' },
    timestamp: '2026-08-25T11:02:30Z',
  },
]

export const mockPermissionRequest: PermissionRequest = {
  request_id: 'perm-1',
  run_id: 'run-2',
  tool_name: 'notes.create',
  permission: 'notes.write',
  parameters: { title: '红黑树知识点总结', folder_path: '/数据结构' },
  impact: '将在你的知识库中创建一篇新笔记',
}
