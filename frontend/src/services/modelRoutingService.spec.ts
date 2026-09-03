// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getModelRouting, saveModelRouting } from './modelRoutingService'

const config = { version: 7, embedding: { provider_id: 'p1', model: 'embedding', endpoint: '/embeddings', dimensions: 1024 }, transcription: null, speaker_matching: null }
const result = { config, local_backends: [{ capability: 'embedding', status: 'placeholder', message: 'hash' }] }
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { vi.unstubAllGlobals() })

describe('model routing service', () => {
  it('round-trips versioned routing without an extra config wrapper', async () => {
    vi.mocked(fetch).mockImplementation(async () => json(result))
    expect(await getModelRouting()).toEqual(result)
    expect(await saveModelRouting(config)).toEqual(result)
    expect(fetch).toHaveBeenNthCalledWith(1, '/api/model-routing', expect.objectContaining({ method: 'GET' }))
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/model-routing', expect.objectContaining({ method: 'PUT', body: JSON.stringify(config) }))
  })

  it('surfaces load, save and version conflict errors instead of returning local defaults', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(json({ error: { code: 'MODEL_ROUTING_VERSION_CONFLICT', message: 'conflict' } }, 409))
      .mockResolvedValueOnce(json({ error: { code: 'SAVE_FAILED', message: 'disk full' } }, 500))
    await expect(getModelRouting()).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
    await expect(saveModelRouting(config)).rejects.toMatchObject({ code: 'MODEL_ROUTING_VERSION_CONFLICT' })
    await expect(saveModelRouting(config)).rejects.toMatchObject({ code: 'SAVE_FAILED' })
  })
})
