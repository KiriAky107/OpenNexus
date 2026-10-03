import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { HighlightClient } from './codeHighlightClient'
import type { HighlightRequest, HighlightResponse } from './codeHighlightProtocol'

class FakeWorker {
  onmessage: ((event: MessageEvent<HighlightResponse>) => void) | null = null
  onerror: ((event: ErrorEvent) => void) | null = null
  onmessageerror: ((event: MessageEvent) => void) | null = null
  postMessage = vi.fn<(request: HighlightRequest) => void>()
  terminate = vi.fn()
  complete(index = this.postMessage.mock.calls.length - 1) {
    const request = this.postMessage.mock.calls[index]![0]
    this.onmessage?.({ data: { id: request.id, result: { spans: new Uint32Array([0, request.source.length, 0]), styles: ['color:red'] } } } as MessageEvent<HighlightResponse>)
  }
}
const input = (source: string) => ({ source, language: 'typescript', theme: 'github-light' as const })
let worker: FakeWorker, client: HighlightClient
beforeEach(() => { vi.useFakeTimers(); worker = new FakeWorker(); client = new HighlightClient(() => worker) })
afterEach(() => { client.dispose(); vi.useRealTimers() })

it('runs one job and coalesces each view to its latest pending snapshot', () => {
  const owner = {}, done = vi.fn()
  client.request(owner, input('initial'), done)
  for (let index = 0; index < 100; index++) client.request(owner, input(`edit-${index}`), done)
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  worker.complete()
  expect(done).not.toHaveBeenCalled()
  expect(worker.postMessage).toHaveBeenCalledTimes(2)
  expect(worker.postMessage.mock.calls[1]![0].source).toBe('edit-99')
  worker.complete()
  expect(done).toHaveBeenCalledTimes(1)
})

it('drops cancelled view replies and ignores stale worker IDs', () => {
  const first = {}, second = {}, firstDone = vi.fn(), secondDone = vi.fn()
  client.request(first, input('first'), firstDone)
  client.request(second, input('second'), secondDone)
  client.cancel(first)
  worker.complete()
  worker.complete(0)
  expect(firstDone).not.toHaveBeenCalled()
  expect(secondDone).not.toHaveBeenCalled()
  client.cancel(second)
  worker.complete()
  expect(secondDone).not.toHaveBeenCalled()
})

it('bounds the number of queued code blocks and preserves the newest requests', () => {
  const done = vi.fn()
  for (let index = 0; index < 50; index++) client.request({}, input(`block-${index}`), done)
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  expect(done).toHaveBeenCalledTimes(17)
  for (let index = 0; index < 33; index++) worker.complete()
  expect(worker.postMessage).toHaveBeenCalledTimes(33)
  expect(worker.postMessage.mock.calls[1]![0].source).toBe('block-18')
  expect(done).toHaveBeenCalledTimes(50)
})

it('bounds queued source characters separately from job count', () => {
  const done = vi.fn()
  for (let index = 0; index < 8; index++) client.request({}, input(`${index}${'x'.repeat(999_999)}`), done)
  expect(done).toHaveBeenCalledTimes(3)
  for (let index = 0; index < 5; index++) worker.complete()
  expect(worker.postMessage).toHaveBeenCalledTimes(5)
  expect(done).toHaveBeenCalledTimes(8)
})

it('reuses bounded small-block results across recreated views and isolates language and theme', () => {
  const done = vi.fn()
  client.request({}, input('original'), done); worker.complete()
  client.request({}, input('original'), done)
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  client.request({}, { ...input('original'), theme: 'github-dark' }, done); worker.complete()
  client.request({}, { ...input('original'), language: 'python' }, done); worker.complete()
  expect(worker.postMessage).toHaveBeenCalledTimes(3)
  for (let index = 0; index < 33; index++) { client.request({}, input(`other-${index}`), done); worker.complete() }
  const previous = worker.postMessage.mock.calls.length
  client.request({}, input('original'), done)
  expect(worker.postMessage).toHaveBeenCalledTimes(previous + 1)
})

it('does not retain large one-time documents', () => {
  const done = vi.fn(), source = 'x'.repeat(16_001)
  client.request({}, input(source), done); worker.complete()
  client.request({}, input(source), done)
  expect(worker.postMessage).toHaveBeenCalledTimes(2)
})

it.each(['error', 'timeout'] as const)('releases every pending callback on worker %s and backs off', async failure => {
  const done = vi.fn()
  client.request({}, input('one'), done); client.request({}, input('two'), done)
  if (failure === 'error') worker.onerror?.({} as ErrorEvent)
  else await vi.advanceTimersByTimeAsync(15_000)
  expect(worker.terminate).toHaveBeenCalledTimes(1)
  expect(done).toHaveBeenCalledTimes(2)
  expect(done.mock.calls.every(call => call[0] === undefined)).toBe(true)
  client.request({}, input('cooldown'), done)
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(5_000)
  client.request({}, input('retry'), done); worker.complete()
  expect(worker.postMessage).toHaveBeenCalledTimes(2)
})

it('releases idle workers and allows editing without Worker support', async () => {
  client.request({}, input('one'), vi.fn()); worker.complete()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(worker.terminate).toHaveBeenCalledTimes(1)
  client = new HighlightClient(() => { throw new Error('Workers disabled') })
  const done = vi.fn()
  expect(() => client.request({}, input('editable'), done)).not.toThrow()
  expect(done).toHaveBeenCalledWith()
})
