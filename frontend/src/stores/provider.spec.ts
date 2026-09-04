// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { ProviderConfig, ProviderPreset } from '@/contracts'

vi.mock('@/services/providerService', () => ({
  listProviders: vi.fn(),
  listProviderPresets: vi.fn(),
  getCredentialStatus: vi.fn(),
  putCredential: vi.fn(),
  listModels: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  testProvider: vi.fn(),
}))

import { useProviderStore } from './provider'
import { getCredentialStatus, listModels, listProviderPresets, listProviders, putCredential } from '@/services/providerService'
import { ApiErrorClass } from '@/services/apiClient'

const providers: ProviderConfig[] = [
  {
    provider_id: 'openai',
    provider_type: 'openai_chat',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    default_model: '',
    enabled: true,
    capabilities: { chat: true },
    credential_id: 'deepseek',
    has_credential: true,
  },
]

const presets: ProviderPreset[] = [
  {
    preset_id: 'deepseek',
    name: 'DeepSeek',
    provider_type: 'openai_compatible',
    base_url: 'https://api.deepseek.com',
    default_credential_id: 'deepseek',
    requires_credential: true,
  },
]

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(listProviders).mockResolvedValue(providers)
  vi.mocked(listProviderPresets).mockResolvedValue(presets)
  vi.mocked(getCredentialStatus).mockResolvedValue(false)
  vi.mocked(putCredential).mockResolvedValue(undefined)
})

describe('provider store model discovery', () => {
  it('loads provider presets and automatically refreshes enabled providers', async () => {
    vi.mocked(listModels).mockResolvedValue([
      { model_id: 'model-z', name: 'Zulu', capabilities: { chat: true } },
      { model_id: 'model-a', name: 'Alpha', capabilities: { chat: true } },
      { model_id: 'model-a', name: 'Alpha duplicate', capabilities: { chat: true } },
    ])
    const store = useProviderStore()

    await store.loadProviders()
    await store.loadPresets()
    await store.refreshEnabledModels()

    expect(store.presets[0].preset_id).toBe('deepseek')
    expect(listModels).toHaveBeenCalledWith('openai')
    expect(store.modelsByProvider.openai.map((model) => model.model_id)).toEqual(['model-a', 'model-z'])
    expect(store.modelErrorsByProvider.openai).toBeUndefined()
  })

  it('records a provider-specific error when model discovery fails', async () => {
    vi.mocked(listModels).mockRejectedValue(new Error('认证失败'))
    const store = useProviderStore()

    await expect(store.loadModels('openai')).rejects.toThrow('认证失败')

    expect(store.modelLoadingByProvider.openai).toBe(false)
    expect(store.modelErrorsByProvider.openai).toBe('认证失败')
  })

  it('explains how to inject a missing DeepSeek credential', async () => {
    vi.mocked(listModels).mockRejectedValue(
      new ApiErrorClass('PROVIDER_CREDENTIAL_MISSING', 'Credential is unavailable')
    )
    const store = useProviderStore()
    await store.loadProviders()

    await expect(store.loadModels('openai')).rejects.toThrow('Credential is unavailable')

    expect(store.modelErrorsByProvider.openai).toContain('填写并保存')
  })

  it('sends an API key to the credential endpoint without storing it in Pinia', async () => {
    const store = useProviderStore()

    await store.saveCredential('deepseek', 'sk-test-sensitive-value')

    expect(putCredential).toHaveBeenCalledWith('deepseek', 'sk-test-sensitive-value')
    expect(store.credentialConfiguredById.deepseek).toBe(true)
    expect(JSON.stringify(store.$state)).not.toContain('sk-test-sensitive-value')
  })
})
