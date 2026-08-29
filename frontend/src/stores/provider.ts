import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ProviderConfig, ModelInfo } from '@/contracts'
import { listModels, listProviders, mockProviders, mockModels } from '@/services/providerService'

export const useProviderStore = defineStore('provider', () => {
  const providers = ref<ProviderConfig[]>(mockProviders)
  const modelsByProvider = ref<Record<string, ModelInfo[]>>(mockModels)
  const defaultProviderId = ref('mock')
  const isLoading = ref(false)

  const enabledProviders = computed(() => providers.value.filter((p) => p.enabled))
  const defaultProvider = computed(() =>
    providers.value.find((p) => p.provider_id === defaultProviderId.value) || null
  )

  async function loadProviders() {
    isLoading.value = true
    try {
      providers.value = await listProviders()
    } finally {
      isLoading.value = false
    }
  }

  async function loadModels(providerId: string) {
    modelsByProvider.value[providerId] = await listModels(providerId)
  }

  async function addProvider(data: Omit<ProviderConfig, 'provider_id'>) {
    const newProvider: ProviderConfig = {
      ...data,
      provider_id: `prov-${Date.now()}`,
    }
    providers.value.push(newProvider)
    return newProvider
  }

  async function updateProvider(providerId: string, data: Partial<ProviderConfig>) {
    const p = providers.value.find((p) => p.provider_id === providerId)
    if (p) Object.assign(p, data)
  }

  async function deleteProvider(providerId: string) {
    const idx = providers.value.findIndex((p) => p.provider_id === providerId)
    if (idx > -1) providers.value.splice(idx, 1)
    delete modelsByProvider.value[providerId]
  }

  async function testProvider(providerId: string): Promise<{ success: boolean; latency_ms?: number; error?: string }> {
    await new Promise((r) => setTimeout(r, 1000))
    const p = providers.value.find((p) => p.provider_id === providerId)
    if (p?.enabled && p.has_credential) {
      return { success: true, latency_ms: 230 + Math.floor(Math.random() * 200) }
    }
    return { success: false, error: '认证失败，请检查 API Key' }
  }

  function setDefaultProvider(providerId: string) {
    defaultProviderId.value = providerId
  }

  return {
    providers,
    modelsByProvider,
    defaultProviderId,
    enabledProviders,
    defaultProvider,
    isLoading,
    loadProviders,
    loadModels,
    addProvider,
    updateProvider,
    deleteProvider,
    testProvider,
    setDefaultProvider,
  }
})
