import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ProviderConfig, ModelInfo, ProviderPreset } from '@/contracts'
import { createProvider, deleteProvider as deleteProviderRequest, listModels, listProviderPresets, listProviders, mockProviders, mockModels, testProvider as testProviderRequest, updateProvider as updateProviderRequest } from '@/services/providerService'

export const useProviderStore = defineStore('provider', () => {
  const providers = ref<ProviderConfig[]>(mockProviders)
  const presets = ref<ProviderPreset[]>([])
  const modelsByProvider = ref<Record<string, ModelInfo[]>>(mockModels)
  const modelLoadingByProvider = ref<Record<string, boolean>>({})
  const modelErrorsByProvider = ref<Record<string, string>>({})
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

  async function loadPresets() {
    try {
      presets.value = await listProviderPresets()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : 'Provider 预设加载失败'
    }
  }

  async function loadModels(providerId: string): Promise<ModelInfo[]> {
    modelLoadingByProvider.value[providerId] = true
    delete modelErrorsByProvider.value[providerId]
    try {
      const models = await listModels(providerId)
      const uniqueModels = [...new Map(models.map((model) => [model.model_id, model])).values()]
        .sort((left, right) => left.name.localeCompare(right.name))
      modelsByProvider.value[providerId] = uniqueModels
      return uniqueModels
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '模型列表获取失败'
      modelErrorsByProvider.value[providerId] = message
      throw reason
    } finally {
      modelLoadingByProvider.value[providerId] = false
    }
  }

  async function refreshEnabledModels() {
    await Promise.allSettled(
      providers.value.filter((provider) => provider.enabled).map((provider) => loadModels(provider.provider_id))
    )
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
    return updated
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
    presets,
    modelsByProvider,
    modelLoadingByProvider,
    modelErrorsByProvider,
    defaultProviderId,
    enabledProviders,
    defaultProvider,
    isLoading,
    error,
    loadProviders,
    loadPresets,
    loadModels,
    refreshEnabledModels,
    addProvider,
    updateProvider,
    deleteProvider,
    testProvider,
    setDefaultProvider,
  }
})
