// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { ProviderConfig, ProviderPreset } from '@/contracts'

vi.mock('@/services/providerService', () => ({
  mockProviders: [],
  mockModels: {},
  listProviders: vi.fn(),
  listProviderPresets: vi.fn(),
  listModels: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  testProvider: vi.fn(),
}))

import { useProviderStore } from './provider'
import { listModels, listProviderPresets, listProviders } from '@/services/providerService'

const providers: ProviderConfig[] = [
  {
    provider_id: 'openai',
    provider_type: 'openai_chat',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    default_model: '',
    enabled: true,
    capabilities: { chat: true },
    has_credential: true,
  },
]

const presets: ProviderPreset[] = [
  {
    preset_id: 'deepseek',
    name: 'DeepSeek',
    provider_type: 'openai_compatible',
    base_url: 'https://api.deepseek.com',
    requires_credential: true,
  },
]

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(listProviders).mockResolvedValue(providers)
  vi.mocked(listProviderPresets).mockResolvedValue(presets)
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
})
