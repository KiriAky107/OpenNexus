import type { AgentEvent, TraceNode, TraceNodeType } from '@/contracts'

export function buildTraceNodes(events: AgentEvent[]): TraceNode[] {
  const nodes: TraceNode[] = []
  let currentModelCallId: string | null = null

  for (const event of events) {
    const type = mapEventType(event.event)
    const id = `seq-${event.sequence}`
    const title = getNodeTitle(event)
    const subtitle = getNodeSubtitle(event)
    const status = getNodeStatus(event)

    const node: TraceNode = {
      id,
      sequence: event.sequence,
      type,
      title,
      subtitle,
      status,
      data: event.data,
      timestamp: event.timestamp,
      children: [],
    }

    if (event.event === 'ModelCallStarted') {
      currentModelCallId = id
      node.children = []
      nodes.push(node)
      continue
    }

    if (event.event === 'ModelCallCompleted' || event.event === 'ModelCallFailed') {
      if (currentModelCallId) {
        const modelCall = findNodeById(nodes, currentModelCallId)
        if (modelCall) {
          modelCall.status = event.event === 'ModelCallCompleted' ? 'completed' : 'error'
          if (event.data.duration_ms != null) {
            modelCall.duration_ms = event.data.duration_ms as number
          }
          if (event.data.finish_reason) {
            modelCall.subtitle = `${modelCall.subtitle ?? ''} · ${String(event.data.finish_reason)}`
          }
        }
        currentModelCallId = null
      }
      continue
    }

    if (currentModelCallId && type !== 'run' && type !== 'complete' && type !== 'error') {
      const parent = findNodeById(nodes, currentModelCallId)
      if (parent) {
        node.parent_id = currentModelCallId
        parent.children.push(node)
        continue
      }
    }

    nodes.push(node)
  }

  return nodes
}

function findNodeById(nodes: TraceNode[], id: string): TraceNode | null {
  for (const node of nodes) {
    if (node.id === id) return node
    const found = findNodeById(node.children, id)
    if (found) return found
  }
  return null
}

function mapEventType(eventType: AgentEvent['event']): TraceNodeType {
  switch (eventType) {
    case 'RunStarted': return 'run'
    case 'RunCompleted': return 'complete'
    case 'RunFailed': return 'error'
    case 'RunCancelled': return 'complete'
    case 'ModelCallStarted':
    case 'ModelCallCompleted':
    case 'ModelCallFailed':
      return 'model_call'
    case 'ToolCall': return 'tool_call'
    case 'ToolResult': return 'tool_result'
    case 'TextDelta': return 'text'
    case 'ThinkingDelta': return 'thinking'
    case 'Citation': return 'citation'
    case 'Usage': return 'usage'
    case 'PermissionRequired':
    case 'PermissionResolved':
      return 'permission'
    default: return 'text'
  }
}

function getNodeTitle(event: AgentEvent): string {
  switch (event.event) {
    case 'RunStarted': return '运行开始'
    case 'RunCompleted': return '运行完成'
    case 'RunFailed': return '运行失败'
    case 'RunCancelled': return '运行已取消'
    case 'ModelCallStarted': return '模型调用'
    case 'ModelCallCompleted': return '模型调用完成'
    case 'ModelCallFailed': return '模型调用失败'
    case 'ToolCall': return `工具调用：${event.data.name ?? '未知工具'}`
    case 'ToolResult': return `工具结果：${event.data.name ?? '未知工具'}`
    case 'TextDelta': return '回复文本'
    case 'ThinkingDelta': return '思考中'
    case 'Citation': return '引用来源'
    case 'Usage': return 'Token 用量'
    case 'PermissionRequired': return '需要权限确认'
    case 'PermissionResolved': return '权限已处理'
    default: return event.event
  }
}

function getNodeSubtitle(event: AgentEvent): string | undefined {
  const data = event.data
  switch (event.event) {
    case 'ModelCallStarted':
      return [data.provider_id, data.model].filter(Boolean).join(' / ') || undefined
    case 'ModelCallCompleted':
      if (data.duration_ms != null) return `耗时 ${formatDuration(data.duration_ms as number)}`
      return undefined
    case 'ToolCall':
      return `调用 ${data.name ?? 'unknown'}`
    case 'ToolResult':
      if (data.duration_ms != null) return `耗时 ${formatDuration(data.duration_ms as number)}`
      if (data.success) return '成功'
      return data.error_code ? `错误：${data.error_code}` : undefined
    case 'Citation':
      return data.heading_path ? String(data.heading_path) : undefined
    case 'Usage': {
      // total_tokens 优先；缺失时回退到 input+output 之和。
      const total = asNumber(data.total_tokens)
      if (total != null) return `${total} tokens`
      const input = asNumber(data.input_tokens)
      const output = asNumber(data.output_tokens)
      if (input == null && output == null) return '- tokens'
      return `${(input ?? 0) + (output ?? 0)} tokens`
    }
    case 'PermissionRequired':
      return String(data.permission ?? '')
    case 'PermissionResolved':
      return String(data.decision ?? '')
    default:
      return undefined
  }
}

function getNodeStatus(event: AgentEvent): TraceNode['status'] {
  switch (event.event) {
    case 'RunFailed':
    case 'ModelCallFailed':
      return 'error'
    case 'RunCompleted':
    case 'RunCancelled':
    case 'ModelCallCompleted':
    case 'ToolResult':
    case 'Usage':
    case 'PermissionResolved':
      return 'completed'
    case 'ToolCall':
      if (event.data.status === 'completed') return 'completed'
      if (event.data.status === 'error') return 'error'
      return 'running'
    case 'PermissionRequired':
      return 'pending'
    case 'ModelCallStarted':
    case 'RunStarted':
    case 'ThinkingDelta':
      return 'running'
    default:
      return 'completed'
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

/** 事件 data 是 Record<string, unknown>，取数值字段前先收窄类型。 */
function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

export function calculateDuration(event1: AgentEvent, event2: AgentEvent): number {
  const t1 = new Date(event1.timestamp).getTime()
  const t2 = new Date(event2.timestamp).getTime()
  return Math.max(0, t2 - t1)
}

export function getTotalDuration(events: AgentEvent[]): number {
  if (events.length < 2) return 0
  const first = events[0]
  const last = events[events.length - 1]
  return calculateDuration(first, last)
}

export function getToolCallsFromEvents(events: AgentEvent[]): Array<{
  tool_call_id: string
  name: string
  status: 'pending' | 'running' | 'completed' | 'error'
  arguments?: Record<string, unknown>
  result?: string
  duration_ms?: number
  started_at?: string
  completed_at?: string
}> {
  const calls = new Map<string, {
    tool_call_id: string
    name: string
    status: 'pending' | 'running' | 'completed' | 'error'
    arguments?: Record<string, unknown>
    result?: string
    duration_ms?: number
    started_at?: string
    completed_at?: string
  }>()

  for (const event of events) {
    if (event.event === 'ToolCall') {
      const id = String(event.data.tool_call_id ?? '')
      calls.set(id, {
        tool_call_id: id,
        name: String(event.data.name ?? 'unknown'),
        status: 'running',
        arguments: (event.data.arguments ?? event.data.parameters) as Record<string, unknown> | undefined,
        started_at: event.timestamp,
      })
    } else if (event.event === 'ToolResult') {
      const id = String(event.data.tool_call_id ?? '')
      const existing = calls.get(id)
      if (existing) {
        existing.status = event.data.success === false ? 'error' : 'completed'
        existing.result = event.data.output != null ? JSON.stringify(event.data.output) : event.data.result as string | undefined
        existing.duration_ms = event.data.duration_ms as number | undefined
        existing.completed_at = event.timestamp
      }
    }
  }

  return [...calls.values()]
}
