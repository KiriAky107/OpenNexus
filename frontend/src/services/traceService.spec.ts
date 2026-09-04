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

describe('buildTraceNodes', () => {
  it('把模型调用期间的事件挂到该模型调用之下', () => {
    const nodes = buildTraceNodes([
      event('RunStarted'),
      event('ModelCallStarted', { model: 'mock-1' }),
      event('ToolCall', { name: 'read_note' }),
      event('ToolResult', { success: true }),
      event('ModelCallCompleted', { duration_ms: 1200 }),
      event('RunCompleted'),
    ])

    // 顶层只剩：运行开始、模型调用、运行完成
    expect(nodes).toHaveLength(3)
    const modelCall = nodes[1]
    expect(modelCall.type).toBe('model_call')
    expect(modelCall.status).toBe('completed')
    expect(modelCall.duration_ms).toBe(1200)
    expect(modelCall.children.map((c) => c.type)).toEqual(['tool_call', 'tool_result'])
  })

  it('模型调用失败时标记为 error', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model: 'mock-1' }),
      event('ModelCallFailed', { error_code: 'PROVIDER_TIMEOUT' }),
    ])

    expect(nodes).toHaveLength(1)
    expect(nodes[0].status).toBe('error')
  })

  it('运行级事件始终留在顶层，不会被模型调用吞掉', () => {
    const nodes = buildTraceNodes([
      event('ModelCallStarted', { model: 'mock-1' }),
      event('RunFailed', { error_code: 'RUN_TIMEOUT' }),
    ])

    expect(nodes.map((n) => n.type)).toEqual(['model_call', 'error'])
  })

  it('模型调用之外的事件保持在顶层', () => {
    const nodes = buildTraceNodes([
      event('RunStarted'),
      event('ToolCall', { name: 'search' }),
      event('RunCompleted'),
    ])

    expect(nodes).toHaveLength(3)
    expect(nodes.every((n) => n.children.length === 0)).toBe(true)
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
