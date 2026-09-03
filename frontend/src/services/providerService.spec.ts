// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createProvider, listProviderPresets, putCredential, updateProvider } from './providerService'

const provider = { provider_id: 'provider-1', provider_type: 'openai_chat', name: 'Custom', capabilities: [], enabled: true }
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { vi.unstubAllGlobals() })

describe('provider wire contracts', () => {
  it('retains preset logos, descriptions and capabilities', async () => {
    const preset = { preset_id: 'qwen', logo_id: 'qwen', description: '通义千问', capabilities: ['chat', 'embedding'] }
    vi.mocked(fetch).mockResolvedValue(json({ items: [preset] }))
    expect(await listProviderPresets()).toEqual([preset])
  })

  it('persists protocol edits and explicit credential unlinking', async () => {
    vi.mocked(fetch).mockResolvedValue(json(provider))
    await updateProvider('provider-1', { provider_type: 'openai_responses', default_model: '', credential_id: null })
    expect(fetch).toHaveBeenCalledWith('/api/providers/provider-1', expect.objectContaining({ method: 'PATCH' }))
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({ provider_type: 'openai_responses', default_model: '', credential_id: null })
  })

  it('sends secrets only to credentials and a reference to provider configuration', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ configured: true })).mockResolvedValueOnce(json(provider))
    await putCredential('provider-key-test', 'test-only-key')
    await createProvider({ name: 'Custom', provider_type: 'openai_compatible', default_model: '', enabled: true, credential_id: 'provider-key-test', has_credential: true, capabilities: {} })
    const calls = vi.mocked(fetch).mock.calls
    expect(calls[0][0]).toBe('/api/credentials/provider-key-test')
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ api_key: 'test-only-key' })
    expect(JSON.parse(String(calls[1][1]?.body))).toMatchObject({ credential_id: 'provider-key-test' })
    expect(calls[1][1]?.body).not.toContain('test-only-key')
    expect(calls[1][1]?.body).not.toContain('has_credential')
  })
})
