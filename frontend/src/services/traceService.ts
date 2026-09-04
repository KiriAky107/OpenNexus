import type { AgentEvent, TraceNode, TraceNodeType } from '@/contracts'

/**
 * 把扁平事件流折叠成调用树。
 *
 * 归属关系一律走 id，不依赖事件相邻顺序 —— 后端的真实顺序是
 * ModelCallStarted → ModelCallCompleted → Usage → ToolCall/ToolResult，
 * 工具在模型调用「完成」之后才执行，并且多个工具是并发跑的
 * （runtime.py 里 asyncio.gather + Semaphore），事件会交错到达。
 * 因此工具事件用 data.parent_model_call_id 找父节点，
 * ToolResult 用 data.tool_call_id 回填对应 ToolCall 的状态。
 */
export function buildTraceNodes(events: AgentEvent[]): TraceNode[] {
  const roots: TraceNode[] = []
  /** model_call_id -> 模型调用节点 */
  const modelCalls = new Map<string, TraceNode>()
  /** tool_call_id -> 工具调用节点，供 ToolResult 回填状态 */
  const toolCalls = new Map<string, TraceNode>()

  for (const event of events) {
    const node: TraceNode = {
      id: `seq-${event.sequence}`,
      sequence: event.sequence,
      type: mapEventType(event.event),
      title: getNodeTitle(event),
      subtitle: getNodeSubtitle(event),
      status: getNodeStatus(event),
      data: event.data,
      timestamp: event.timestamp,
      children: [],
    }
    const modelCallId = asId(event.data.model_call_id)
    const parentModelCallId = asId(event.data.parent_model_call_id)
    const toolCallId = asId(event.data.tool_call_id)

    switch (event.event) {
      case 'ModelCallStarted': {
        if (modelCallId) modelCalls.set(modelCallId, node)
        roots.push(node)
        continue
      }

      // 完成/失败事件不单独成节点，只更新对应模型调用的状态。
      case 'ModelCallCompleted':
      case 'ModelCallFailed': {
        const target = modelCallId ? modelCalls.get(modelCallId) : undefined
        if (!target) {
          // 找不到配对的 Started（例如 SSE 断点恢复后只拿到后半段），保留为顶层节点。
          roots.push(node)
          continue
        }
        target.status = event.event === 'ModelCallCompleted' ? 'completed' : 'error'
        const duration = asNumber(event.data.duration_ms)
        if (duration != null) target.duration_ms = duration
        const extra = event.event === 'ModelCallCompleted'
          ? asText(event.data.finish_reason)
          : asText(event.data.error_code)
        if (extra) target.subtitle = target.subtitle ? `${target.subtitle} · ${extra}` : extra
        continue
      }

      // ToolResult 只回填对应 ToolCall，避免工具结束后仍显示 running。
      case 'ToolResult': {
        const target = toolCallId ? toolCalls.get(toolCallId) : undefined
        if (!target) {
          attach(node, parentModelCallId, modelCalls, roots)
          continue
        }
        target.status = event.data.success === false ? 'error' : 'completed'
        const duration = asNumber(event.data.duration_ms)
        if (duration != null) target.duration_ms = duration
        const detail = event.data.success === false
          ? asText(event.data.error_code) ?? '失败'
          : undefined
        if (detail) target.subtitle = target.subtitle ? `${target.subtitle} · ${detail}` : detail
        // 结果数据合并到调用节点，展开详情时才能看到 output。
        target.data = { ...target.data, result: event.data }
        continue
      }

      case 'ToolCall': {
        if (toolCallId) toolCalls.set(toolCallId, node)
        attach(node, parentModelCallId, modelCalls, roots)
        continue
      }

      default: {
        attach(node, parentModelCallId, modelCalls, roots)
        continue
      }
    }
  }

  return roots
}

/** 有已知父模型调用就挂进去，否则留在顶层。 */
function attach(
  node: TraceNode,
  parentModelCallId: string | null,
  modelCalls: Map<string, TraceNode>,
  roots: TraceNode[],
) {
  const parent = parentModelCallId ? modelCalls.get(parentModelCallId) : undefined
  if (parent) {
    node.parent_id = parent.id
    parent.children.push(node)
    return
  }
  roots.push(node)
}

function asId(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

function asText(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined
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
      // 后端发的是累计 token_usage（runtime.py），其余字段仅作兼容回退。
      const usage = asNumber(data.token_usage) ?? asNumber(data.total_tokens)
      if (usage != null) return `${usage} tokens`
      const input = asNumber(data.input_tokens)
      const output = asNumber(data.output_tokens)
      if (input == null && output == null) return undefined
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
    case 'ToolResult':
      // 只在 ToolResult 没配上 ToolCall 时（SSE 断点恢复）才成为独立节点，
      // 那时也要按 success 显示，不能一律算成功。
      return event.data.success === false ? 'error' : 'completed'
    case 'RunCompleted':
    case 'RunCancelled':
    case 'ModelCallCompleted':
    case 'Usage':
    case 'PermissionResolved':
      return 'completed'
    case 'ToolCall':
      // 后端的 ToolCall 事件不带 status，起始一律 running，
      // 由后到的 ToolResult 回填最终状态。
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
