import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ProviderConfig, ModelInfo } from '@/contracts'
import { createProvider, deleteProvider as deleteProviderRequest, listModels, listProviders, mockProviders, mockModels, testProvider as testProviderRequest, updateProvider as updateProviderRequest } from '@/services/providerService'

export const useProviderStore = defineStore('provider', () => {
  const providers = ref<ProviderConfig[]>(mockProviders)
  const modelsByProvider = ref<Record<string, ModelInfo[]>>(mockModels)
  const defaultProviderId = ref('mock')
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const enabledProviders = computed(() => providers.value.filter((p) => p.enabled))
  const defaultProvider = computed(() =>
    providers.value.find((p) => p.provider_id === defaultProviderId.value) || null
  )

  async function loadProviders() {
    isLoading.value = true
    try {
      providers.value = await listProviders()
      error.value = null
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : 'Provider 加载失败'
    } finally {
      isLoading.value = false
    }
  }

  async function loadModels(providerId: string) {
    modelsByProvider.value[providerId] = await listModels(providerId)
  }

  async function addProvider(data: Omit<ProviderConfig, 'provider_id'>) {
    const newProvider = await createProvider(data)
    providers.value.push(newProvider)
    return newProvider
  }

  async function updateProvider(providerId: string, data: Partial<ProviderConfig>) {
    const updated = await updateProviderRequest(providerId, data)
    const index = providers.value.findIndex((provider) => provider.provider_id === providerId)
    if (index >= 0) providers.value[index] = updated
  }

  async function deleteProvider(providerId: string) {
    await deleteProviderRequest(providerId)
    const idx = providers.value.findIndex((p) => p.provider_id === providerId)
    if (idx > -1) providers.value.splice(idx, 1)
    delete modelsByProvider.value[providerId]
  }

  async function testProvider(providerId: string): Promise<{ success: boolean; latency_ms?: number; error?: string }> {
    const result = await testProviderRequest(providerId)
    return { success: result.success, latency_ms: result.latency_ms, error: result.error_message }
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
    error,
    loadProviders,
    loadModels,
    addProvider,
    updateProvider,
    deleteProvider,
    testProvider,
    setDefaultProvider,
  }
})
