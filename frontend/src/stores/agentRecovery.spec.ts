// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { useAgentStore } from './agent'
const mock = vi.hoisted(() => ({ stream: vi.fn(), get: vi.fn(), trace: vi.fn(), cancel: vi.fn() }))
vi.mock('@/services/agentService', () => ({ streamAgentEvents: mock.stream, getAgentRun: mock.get, getAgentTrace: mock.trace, cancelAgentRun: mock.cancel }))
let pinia: ReturnType<typeof createPinia>
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  pinia = createPinia()
  setActivePinia(pinia)
  mock.stream.mockReturnValue({ cancel: vi.fn() })
  mock.get.mockImplementation(async id => ({ run_id: id, status: 'running', current_step: 0, max_steps: 6 }))
  mock.trace.mockImplementation(async id => ({ run_id: id, status: 'running', items: [], next_sequence: -1, has_more: false }))
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
