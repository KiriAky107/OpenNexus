import { describe, expect, it } from 'vitest'
import { buildTraceNodes, getToolCallsFromEvents, getTotalDuration } from './traceService'
import type { AgentEvent, AgentEventType } from '@/contracts'

let sequence = 0

function event(
  type: AgentEventType,
  data: Record<string, unknown> = {},
  timestamp = '2026-01-01T00:00:00.000Z',
): AgentEvent {
  return { event: type, sequence: ++sequence, run_id: 'run-1', data, timestamp }
}

/**
 * 后端真实的事件顺序（backend/app/agent/runtime.py）：
 *   ModelCallStarted → ModelCallCompleted → Usage → ToolCall → ToolResult
 * 工具在模型调用「完成之后」才执行，而且多个工具并发跑（asyncio.gather +
 * Semaphore），事件会交错到达。所以建树只能靠 id 关联，不能靠相邻顺序。
 */
describe('buildTraceNodes', () => {
  it('工具事件按 parent_model_call_id 归属，即使出现在 ModelCallCompleted 之后', () => {
    const nodes = buildTraceNodes([
      event('RunStarted'),
      event('ModelCallStarted', { model_call_id: 'mc-1', model: 'mock-1', provider_id: 'mock' }),
      event('ModelCallCompleted', { model_call_id: 'mc-1', duration_ms: 1200, finish_reason: 'tool_calls' }),
      event('Usage', { token_usage: 320 }),
      event('ToolCall', { tool_call_id: 'tc-1', name: 'read_note', parent_model_call_id: 'mc-1' }),
      event('ToolResult', { tool_call_id: 'tc-1', name: 'read_note', success: true, duration_ms: 40, parent_model_call_id: 'mc-1' }),
      event('RunCompleted'),
    ])

    // 顶层：运行开始、模型调用、Usage、运行完成。工具挂在模型调用下面。
    expect(nodes.map((n) => n.type)).toEqual(['run', 'model_call', 'usage', 'complete'])

    const modelCall = nodes[1]
    expect(modelCall.status).toBe('completed')
    expect(modelCall.duration_ms).toBe(1200)
    expect(modelCall.children.map((c) => c.type)).toEqual(['tool_call'])
  })

  it('ToolResult 回填对应 ToolCall 的状态，结束后不再显示 running', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model_call_id: 'mc-2' }),
      event('ModelCallCompleted', { model_call_id: 'mc-2' }),
      event('ToolCall', { tool_call_id: 'tc-2', name: 'read_note', parent_model_call_id: 'mc-2' }),
      event('ToolResult', { tool_call_id: 'tc-2', name: 'read_note', success: true, duration_ms: 55, parent_model_call_id: 'mc-2' }),
    ])

    const toolCall = nodes[0].children[0]
    expect(toolCall.type).toBe('tool_call')
    expect(toolCall.status).toBe('completed')
    expect(toolCall.duration_ms).toBe(55)
    // 结果数据合并进调用节点，展开详情时能看到 output。
    expect((toolCall.data.result as Record<string, unknown>).success).toBe(true)
  })

  it('工具失败时把 ToolCall 标记为 error 并带上 error_code', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model_call_id: 'mc-3' }),
      event('ToolCall', { tool_call_id: 'tc-3', name: 'write_note', parent_model_call_id: 'mc-3' }),
      event('ToolResult', { tool_call_id: 'tc-3', name: 'write_note', success: false, error_code: 'TOOL_DENIED', parent_model_call_id: 'mc-3' }),
    ])

    const toolCall = nodes[0].children[0]
    expect(toolCall.status).toBe('error')
    expect(toolCall.subtitle).toContain('TOOL_DENIED')
  })

  it('并发工具交错到达时各自归属到正确的模型调用', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model_call_id: 'mc-a' }),
      event('ModelCallCompleted', { model_call_id: 'mc-a' }),
      event('ToolCall', { tool_call_id: 'a1', name: 'toolA1', parent_model_call_id: 'mc-a' }),
      event('ToolCall', { tool_call_id: 'a2', name: 'toolA2', parent_model_call_id: 'mc-a' }),
      event('ModelCallStarted', { model_call_id: 'mc-b' }),
      event('ModelCallCompleted', { model_call_id: 'mc-b' }),
      event('ToolCall', { tool_call_id: 'b1', name: 'toolB1', parent_model_call_id: 'mc-b' }),
      // 第一个模型调用的工具结果比第二轮的工具调用还晚到
      event('ToolResult', { tool_call_id: 'a2', name: 'toolA2', success: true, parent_model_call_id: 'mc-a' }),
      event('ToolResult', { tool_call_id: 'a1', name: 'toolA1', success: true, parent_model_call_id: 'mc-a' }),
      event('ToolResult', { tool_call_id: 'b1', name: 'toolB1', success: true, parent_model_call_id: 'mc-b' }),
    ])

    const [callA, callB] = nodes.filter((n) => n.type === 'model_call')
    expect(callA.children.map((c) => c.title)).toEqual(['工具调用：toolA1', '工具调用：toolA2'])
    expect(callB.children.map((c) => c.title)).toEqual(['工具调用：toolB1'])
    expect(callA.children.every((c) => c.status === 'completed')).toBe(true)
  })

  it('模型调用失败时标记为 error 并附带 error_code', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model_call_id: 'mc-4', model: 'mock-1' }),
      event('ModelCallFailed', { model_call_id: 'mc-4', error_code: 'PROVIDER_TIMEOUT', duration_ms: 900 }),
    ])

    expect(nodes).toHaveLength(1)
    expect(nodes[0].status).toBe('error')
    expect(nodes[0].duration_ms).toBe(900)
    expect(nodes[0].subtitle).toContain('PROVIDER_TIMEOUT')
  })

  it('PermissionRequired 不带父 id，留在顶层', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model_call_id: 'mc-5' }),
      event('PermissionRequired', { request_id: 'r1', permission: 'notes.write' }),
    ])

    expect(nodes.map((n) => n.type)).toEqual(['model_call', 'permission'])
    expect(nodes[1].status).toBe('pending')
  })

  it('运行级事件始终留在顶层，不会被模型调用吞掉', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model_call_id: 'mc-6' }),
      event('RunFailed', { error_code: 'RUN_TIMEOUT' }),
    ])

    expect(nodes.map((n) => n.type)).toEqual(['model_call', 'error'])
  })

  it('SSE 断点恢复只拿到后半段时，孤立事件退回顶层而不是被丢弃', () => {
    // 没有 ModelCallStarted，也没有对应的 ToolCall
    const nodes = buildTraceNodes([
      event('ModelCallCompleted', { model_call_id: 'mc-lost', duration_ms: 10 }),
      event('ToolResult', { tool_call_id: 'tc-lost', name: 'read_note', success: false, error_code: 'TOOL_FAILED' }),
    ])

    expect(nodes).toHaveLength(2)
    expect(nodes[0].type).toBe('model_call')
    // 落单的失败结果不能显示成 completed
    expect(nodes[1].status).toBe('error')
  })

  it('Usage 副标题读后端真实字段 token_usage', () => {
    const nodes = buildTraceNodes([event('Usage', { token_usage: 1234 })])
    expect(nodes[0].subtitle).toBe('1234 tokens')
  })

  it('空事件列表返回空树', () => {
    expect(buildTraceNodes([])).toEqual([])
  })
})

describe('getToolCallsFromEvents', () => {
  it('按 tool_call_id 配对 ToolCall 与 ToolResult', () => {
    const calls = getToolCallsFromEvents([
      event('ToolCall', { tool_call_id: 'c1', name: 'read_note' }),
      event('ToolResult', { tool_call_id: 'c1', success: true, duration_ms: 40 }),
    ])

    expect(calls).toHaveLength(1)
    expect(calls[0].name).toBe('read_note')
    expect(calls[0].status).toBe('completed')
    expect(calls[0].duration_ms).toBe(40)
  })

  it('工具失败时状态为 error', () => {
    const calls = getToolCallsFromEvents([
      event('ToolCall', { tool_call_id: 'c2', name: 'write_note' }),
      event('ToolResult', { tool_call_id: 'c2', success: false, error_code: 'TOOL_DENIED' }),
    ])

    expect(calls[0].status).toBe('error')
  })

  it('尚未返回结果的工具调用保持 running', () => {
    const calls = getToolCallsFromEvents([
      event('ToolCall', { tool_call_id: 'c9', name: 'write_note' }),
    ])

    expect(calls).toHaveLength(1)
    expect(calls[0].status).toBe('running')
  })
})

describe('getTotalDuration', () => {
  it('返回首尾事件的时间差', () => {
    const duration = getTotalDuration([
      event('RunStarted', {}, '2026-01-01T00:00:00.000Z'),
      event('RunCompleted', {}, '2026-01-01T00:00:02.500Z'),
    ])

    expect(duration).toBe(2500)
  })

  it('单个事件或空列表时为 0', () => {
    expect(getTotalDuration([])).toBe(0)
    expect(getTotalDuration([event('RunStarted')])).toBe(0)
  })
})
