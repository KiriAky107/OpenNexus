// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { useAgentStore } from './agent'
const mock = vi.hoisted(() => ({ stream: vi.fn(), get: vi.fn(), cancel: vi.fn() }))
vi.mock('@/services/agentService', () => ({ streamAgentEvents: mock.stream, getAgentRun: mock.get, cancelAgentRun: mock.cancel }))
let pinia: ReturnType<typeof createPinia>
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); pinia = createPinia(); setActivePinia(pinia); mock.stream.mockReturnValue({ cancel: vi.fn() }); mock.get.mockImplementation(async id => ({ run_id: id, status: 'running' })) })
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
