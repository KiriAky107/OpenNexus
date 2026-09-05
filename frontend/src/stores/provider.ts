import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ProviderConfig, ModelInfo, ProviderPreset } from '@/contracts'
import { createProvider, deleteProvider as deleteProviderRequest, getCredentialStatus, listModels, listProviderPresets, listProviders, putCredential, testProvider as testProviderRequest, updateProvider as updateProviderRequest } from '@/services/providerService'
import { ApiErrorClass } from '@/services/apiClient'
import { t } from '@/i18n'

export const useProviderStore = defineStore('provider', () => {
  const providers = ref<ProviderConfig[]>([])
  const presets = ref<ProviderPreset[]>([])
  const modelsByProvider = ref<Record<string, ModelInfo[]>>({})
  const modelLoadingByProvider = ref<Record<string, boolean>>({})
  const modelErrorsByProvider = ref<Record<string, string>>({})
  const credentialConfiguredById = ref<Record<string, boolean>>({})
  const defaultProviderId = ref('')
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
      if (!enabledProviders.value.some(p => p.provider_id === defaultProviderId.value)) {
        defaultProviderId.value = enabledProviders.value[0]?.provider_id ?? ''
      }
      error.value = null
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : t('Provider 加载失败', 'Failed to load Providers')
    } finally {
      isLoading.value = false
    }
  }

  async function loadPresets() {
    try {
      presets.value = await listProviderPresets()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : t('Provider 预设加载失败', 'Failed to load Provider presets')
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
      const provider = providers.value.find((item) => item.provider_id === providerId)
      const credentialId = provider?.credential_id
      let message = reason instanceof Error ? reason.message : t('模型列表获取失败', 'Failed to load the model list')
      if (reason instanceof ApiErrorClass && reason.code === 'PROVIDER_CREDENTIAL_MISSING') {
        message = t('尚未配置 API Key，请编辑该 Provider 后填写并保存。', 'No API key is configured. Edit this Provider, enter a key, and save it.')
      } else if (reason instanceof ApiErrorClass && reason.code === 'PROVIDER_AUTH_FAILED') {
        message = t(`鉴权失败，请检查凭据“${credentialId || '未设置'}”对应的 API Key 是否有效。`, `Authentication failed. Check the API key for credential “${credentialId || 'not set'}”.`)
      }
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

  async function loadCredentialStatus(credentialId: string): Promise<boolean> {
    const configured = await getCredentialStatus(credentialId)
    credentialConfiguredById.value[credentialId] = configured
    return configured
  }

  async function saveCredential(credentialId: string, apiKey: string) {
    await putCredential(credentialId, apiKey)
    credentialConfiguredById.value[credentialId] = true
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
    credentialConfiguredById,
    defaultProviderId,
    enabledProviders,
    defaultProvider,
    isLoading,
    error,
    loadProviders,
    loadPresets,
    loadModels,
    refreshEnabledModels,
    loadCredentialStatus,
    saveCredential,
    addProvider,
    updateProvider,
    deleteProvider,
    testProvider,
    setDefaultProvider,
  }
})
