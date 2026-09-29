// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { useAgentStore } from './agent'
const mock = vi.hoisted(() => ({ stream: vi.fn(), get: vi.fn(), trace: vi.fn(), cancel: vi.fn(), list: vi.fn() }))
vi.mock('@/services/agentService', () => ({ streamAgentEvents: mock.stream, getAgentRun: mock.get, getAgentTrace: mock.trace, cancelAgentRun: mock.cancel, listAgentRuns: mock.list }))
let pinia: ReturnType<typeof createPinia>
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  pinia = createPinia()
  setActivePinia(pinia)
  mock.stream.mockReturnValue({ cancel: vi.fn() })
  mock.get.mockImplementation(async id => ({ run_id: id, status: 'running', current_step: 0, max_steps: 6 }))
  mock.trace.mockImplementation(async id => ({ run_id: id, status: 'running', items: [], next_sequence: -1, has_more: false }))
  mock.list.mockResolvedValue({ items: [], total: 0 })
})
afterEach(() => { disposePinia(pinia); vi.useRealTimers() })
const handler = () => mock.stream.mock.calls.at(-1)![1]
const event = (sequence: number, name = 'RunStarted') => ({ run_id: 'r', sequence, event: name, data: {}, timestamp: 'now' })
it.each(['error', 'eof'])('retains cancellation and resumes after sequence on %s', async kind => {
  const store = useAgentStore(); await store.loadRun('r')
  handler().onEvent(event(5))
  kind === 'error' ? handler().onError(new Error('offline')) : handler().onDone()
  expect(store.isRunning).toBe(true)
  await vi.advanceTimersByTimeAsync(1000)
  expect(mock.stream.mock.calls.at(-1)![2]).toBe(5)
  handler().onEvent(event(5)); handler().onEvent(event(6, 'RunCompleted')); handler().onDone()
  expect(store.events).toHaveLength(2)
  expect(store.isRunning).toBe(false)
  await vi.advanceTimersByTimeAsync(40000)
  expect(mock.stream).toHaveBeenCalledTimes(2)
})
it('invalidates old streams and pending retry when changing run or cancelling', async () => {
  const store = useAgentStore(); await store.loadRun('r')
  const old = handler(); old.onError(new Error('offline'))
  await store.loadRun('s'); old.onEvent(event(8))
  expect(store.events).toHaveLength(0)
  handler().onError(new Error('offline')); await store.cancelRun('s')
  await vi.advanceTimersByTimeAsync(40000)
  expect(mock.stream).toHaveBeenCalledTimes(2)
})
it('bounds retry attempts and supports explicit retry without losing events', async () => {
  const store = useAgentStore(); await store.loadRun('r')
  handler().onEvent(event(1))
  for (let attempt = 0; attempt < 6; attempt++) { handler().onError(new Error('offline')); await vi.advanceTimersByTimeAsync(16000) }
  expect(mock.stream).toHaveBeenCalledTimes(6)
  expect(store.connectionState).toBe('disconnected')
  expect(store.isRunning).toBe(true)
  store.reconnect()
  expect(mock.stream.mock.calls.at(-1)![2]).toBe(1)
})

it('loads every persisted trace page for a completed run without opening SSE', async () => {
  mock.get.mockResolvedValue({ run_id: 'r', status: 'completed', current_step: 2, max_steps: 6 })
  mock.trace
    .mockResolvedValueOnce({
      run_id: 'r', status: 'completed',
      items: [event(0), event(1, 'ModelCallStarted')], next_sequence: 1, has_more: true,
    })
    .mockResolvedValueOnce({
      run_id: 'r', status: 'completed',
      items: [event(2, 'ToolCall'), event(3, 'RunCompleted')], next_sequence: 3, has_more: false,
    })

  const store = useAgentStore()
  await store.loadRun('r')

  expect(mock.trace).toHaveBeenNthCalledWith(1, 'r', { after_sequence: -1, limit: 500 })
  expect(mock.trace).toHaveBeenNthCalledWith(2, 'r', { after_sequence: 1, limit: 500 })
  expect(store.events.map(item => item.sequence)).toEqual([0, 1, 2, 3])
  expect(store.currentStep).toBe(2)
  expect(mock.stream).not.toHaveBeenCalled()
})

it('fills missing events through REST before reconnecting a live run', async () => {
  const store = useAgentStore()
  await store.loadRun('r')
  handler().onEvent(event(0))
  handler().onError(new Error('desktop SSE unavailable'))
  mock.trace.mockResolvedValueOnce({
    run_id: 'r', status: 'running',
    items: [event(1, 'ModelCallStarted'), event(2, 'ToolCall')], next_sequence: 2, has_more: false,
  })

  await vi.advanceTimersByTimeAsync(1000)

  expect(store.events.map(item => item.sequence)).toEqual([0, 1, 2])
  expect(mock.stream.mock.calls.at(-1)![2]).toBe(2)
})

it('drops a previously selected run when the current vault list does not contain it', async () => {
  const store = useAgentStore()
  await store.loadRun('legacy-run')
  expect(store.sortedRuns.map(run => run.run_id)).toEqual(['legacy-run'])
  await store.loadRuns()
  expect(store.sortedRuns).toEqual([])
  expect(store.activeRunId).toBeNull()
})

it('retains every pending permission when another parallel tool completes or is resolved', async () => {
  const store = useAgentStore(); await store.loadRun('r')
  const permission = (sequence: number, requestId: string, toolId: string) => ({ ...event(sequence, 'PermissionRequired'), data: { request_id: requestId, permission: 'notes.write', tool_call: { name: 'notes.update', tool_call_id: toolId, arguments: { note_id: 'note' } } } })
  handler().onEvent(permission(1, 'p1', 't1'))
  handler().onEvent(permission(2, 'p2', 't2'))
  expect(store.permissionRequest?.request_id).toBe('p1')
  handler().onEvent({ ...event(3, 'ToolResult'), data: { tool_call_id: 'unrelated', success: true } })
  expect(store.permissionRequest?.request_id).toBe('p1')
  handler().onEvent({ ...event(4, 'PermissionResolved'), data: { request_id: 'p1' } })
  expect(store.permissionRequest?.request_id).toBe('p2')
  handler().onEvent({ ...event(5, 'ToolResult'), data: { tool_call_id: 't1', success: true } })
  expect(store.permissionRequest?.request_id).toBe('p2')
  handler().onEvent({ ...event(6, 'PermissionResolved'), data: { request_id: 'p2' } })
  expect(store.permissionRequest).toBeNull()
})

it('rebuilds unresolved permission queue from a persisted trace', async () => {
  mock.trace.mockResolvedValue({ run_id: 'r', status: 'waiting_permission', has_more: false, next_sequence: 4, items: [
    { ...event(1, 'PermissionRequired'), data: { request_id: 'remaining', tool_call: { name: 'notes.create', tool_call_id: 't1' } } },
    { ...event(2, 'PermissionRequired'), data: { request_id: 'resolved', tool_call: { name: 'notes.create', tool_call_id: 't2' } } },
    { ...event(3, 'PermissionResolved'), data: { request_id: 'resolved' } },
    { ...event(4, 'ToolResult'), data: { tool_call_id: 't2', success: true } },
  ] })
  const store = useAgentStore(); await store.loadRun('r')
  expect(store.permissionRequest?.request_id).toBe('remaining')
})
