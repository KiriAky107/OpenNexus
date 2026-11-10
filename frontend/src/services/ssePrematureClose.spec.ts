import { afterEach, expect, it, vi } from 'vitest'
import { SseClient } from './sseClient'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const event = (name: string, data: unknown = {}) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
const response = (body: BodyInit) => new Response(body, { status: 200,
  headers: { 'Content-Type': 'text/event-stream' } })

it('SSE terminal marker rejects partial clean EOF without the chat completion event', async () => {
  const fetchMock = vi.fn().mockResolvedValue(response(event('TextDelta', { data: { text: 'partial' } })))
  vi.stubGlobal('fetch', fetchMock)
  const onDone = vi.fn(), onError = vi.fn(), onEvent = vi.fn()
  const client = new SseClient({ url: '/api/chat', terminalEvent: 'Done', onDone, onError, onEvent })
  await client.connect()
  expect(onError).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ message: 'SSE_INCOMPLETE' }))
  expect(onDone).not.toHaveBeenCalled()
  expect(onEvent).toHaveBeenCalledTimes(1)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(client.isConnected()).toBe(false)
})

it('SSE terminal marker rejects an empty successful response without inventing completion', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('')))
  const onDone = vi.fn(), onError = vi.fn()
  await new SseClient({ url: '/api/chat', terminalEvent: 'Done', onDone, onError }).connect()
  expect(onError).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ message: 'SSE_INCOMPLETE' }))
  expect(onDone).not.toHaveBeenCalled()
})

it('SSE terminal marker accepts fragmented UTF8 and the explicit final Done once', async () => {
  const raw = new TextEncoder().encode(event('TextDelta', { text: '中文 😀' }) + 'event: Done\ndata: {}')
  const stream = new ReadableStream<Uint8Array>({ start(controller) {
    for (let offset = 0; offset < raw.length; offset += 3) controller.enqueue(raw.slice(offset, offset + 3))
    controller.close()
  } })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(stream)))
  const onDone = vi.fn(), onError = vi.fn(), onEvent = vi.fn()
  await new SseClient({ url: '/api/chat', terminalEvent: 'Done', onDone, onError, onEvent }).connect()
  expect(onDone).toHaveBeenCalledTimes(1)
  expect(onError).not.toHaveBeenCalled()
  expect(onEvent.mock.calls[0]?.[1]).toEqual({ text: '中文 😀' })
})

it('SSE terminal marker does not accept a different protocol terminal event as chat Done', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(event('RunCompleted'))))
  const onDone = vi.fn(), onError = vi.fn()
  await new SseClient({ url: '/api/chat', terminalEvent: 'Done', onDone, onError }).connect()
  expect(onError).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ message: 'SSE_INCOMPLETE' }))
  expect(onDone).not.toHaveBeenCalled()
})

it('SSE terminal marker keeps resumable Agent GET segment EOF compatible', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(event('ModelCallCompleted', { sequence: 3 }))))
  const onDone = vi.fn(), onError = vi.fn()
  await new SseClient({ url: '/api/agent/runs/original/events', method: 'GET', onDone, onError }).connect()
  expect(onDone).toHaveBeenCalledTimes(1)
  expect(onError).not.toHaveBeenCalled()
})

it('SSE terminal marker does not report premature EOF after explicit user cancellation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(event('TextDelta', { text: 'partial' }))))
  const onDone = vi.fn(), onError = vi.fn()
  const client = new SseClient({ url: '/api/chat', terminalEvent: 'Done', onDone, onError,
    onEvent: () => client.cancel() })
  await client.connect()
  expect(onError).not.toHaveBeenCalled()
  expect(onDone).not.toHaveBeenCalled()
})

it('SSE terminal marker accepts application Error followed by its persisted failed Done', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(event('Error', { message: 'controlled failure' })
    + event('Done', { status: 'failed' }))))
  const onDone = vi.fn(), onError = vi.fn(), onEvent = vi.fn()
  await new SseClient({ url: '/api/chat', terminalEvent: 'Done', onDone, onError, onEvent }).connect()
  expect(onDone).toHaveBeenCalledTimes(1)
  expect(onError).not.toHaveBeenCalled()
  expect(onEvent).toHaveBeenCalledTimes(2)
})
