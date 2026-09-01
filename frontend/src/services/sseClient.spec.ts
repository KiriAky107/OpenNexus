import { afterEach, describe, expect, it, vi } from 'vitest'

import { SseClient } from './sseClient'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('SseClient resumable event transport', () => {
  it('sends Last-Event-ID and exposes the returned SSE id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        'id: 3\nevent: ModelCallCompleted\ndata: {"sequence":3,"data":{"duration_ms":12}}\n\n',
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const received = vi.fn()
    const client = new SseClient({
      url: '/api/agent/runs/run-1/events?after_sequence=2',
      method: 'GET',
      lastEventId: '2',
      onEvent: received,
    })

    await client.connect()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent/runs/run-1/events?after_sequence=2',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ 'Last-Event-ID': '2' }),
      }),
    )
    expect(received).toHaveBeenCalledWith(
      'ModelCallCompleted',
      { sequence: 3, data: { duration_ms: 12 } },
      '3',
    )
  })
})
