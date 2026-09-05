import apiClient from './apiClient'
import type { ApiModelInfo, ApiProviderConfig, ApiProviderPreset, ModelCapability, ModelInfo, OperationResponse, ProviderConfig, ProviderPreset, ProviderUpdateRequest } from '@/contracts'

function capabilityMap(capabilities: string[]): Partial<ModelCapability> {
  return Object.fromEntries(capabilities.map((capability) => [capability, true])) as Partial<ModelCapability>
}

function toProvider(provider: ApiProviderConfig): ProviderConfig {
  return {
    provider_id: provider.provider_id,
    version: provider.version,
    request_overrides: provider.request_overrides || [],
    provider_type: provider.provider_type,
    name: provider.name,
    base_url: provider.base_url ?? undefined,
    default_model: provider.default_model ?? '',
    enabled: provider.enabled,
    capabilities: capabilityMap(provider.capabilities),
    credential_id: provider.credential_id ?? undefined,
    has_credential: Boolean(provider.credential_id),
  }
}

function toModel(model: ApiModelInfo): ModelInfo {
  return { model_id: model.model, name: model.display_name, capabilities: capabilityMap(model.capabilities) }
}

export async function listProviders(): Promise<ProviderConfig[]> {
  const response = await apiClient.get<{ items: ApiProviderConfig[] }>('/api/providers')
  return response.items.filter(provider => provider.provider_type !== 'mock').map(toProvider)
}

export async function getProvider(providerId: string): Promise<ProviderConfig> {
  return toProvider(await apiClient.get<ApiProviderConfig>(`/api/providers/${providerId}`))
}

export async function createProvider(data: Omit<ProviderConfig, 'provider_id'>): Promise<ProviderConfig> {
  const response = await apiClient.post<ApiProviderConfig>('/api/providers', {
    provider_type: data.provider_type,
    request_overrides: data.request_overrides,
    name: data.name,
    base_url: data.base_url,
    default_model: data.default_model || null,
    credential_id: data.credential_id,
    enabled: data.enabled,
  })
  return toProvider(response)
}

export async function listProviderPresets(): Promise<ProviderPreset[]> {
  const response = await apiClient.get<{ items: ApiProviderPreset[] }>('/api/providers/presets')
  return response.items
}

export async function getCredentialStatus(credentialId: string): Promise<boolean> {
  const response = await apiClient.get<{ credential_id: string; configured: boolean }>(`/api/credentials/${encodeURIComponent(credentialId)}`)
  return response.configured
}

export async function putCredential(credentialId: string, apiKey: string): Promise<void> {
  await apiClient.put<{ credential_id: string; configured: boolean }>(
    `/api/credentials/${encodeURIComponent(credentialId)}`,
    { api_key: apiKey },
  )
}

export async function updateProvider(providerId: string, data: ProviderUpdateRequest): Promise<ProviderConfig> {
  const response = await apiClient.patch<ApiProviderConfig>(`/api/providers/${providerId}`, {
    provider_type: data.provider_type,
    version: data.version,
    request_overrides: data.request_overrides,
    name: data.name,
    base_url: data.base_url,
    default_model: data.default_model,
    credential_id: data.credential_id,
    enabled: data.enabled,
  })
  return toProvider(response)
}

export async function deleteProvider(providerId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/providers/${providerId}`)
}

export async function listModels(providerId: string): Promise<ModelInfo[]> {
  const response = await apiClient.get<{ provider_id: string; items: ApiModelInfo[] }>(`/api/providers/${providerId}/models`)
  return response.items.map(toModel)
}

export interface TestResult {
  success: boolean
  latency_ms?: number
  error_code?: string
  error_message?: string
}

export async function testProvider(providerId: string): Promise<TestResult> {
  try {
    const result = await apiClient.post<{ success: boolean; latency_ms?: number | null; message: string }>('/api/providers/test', { provider_id: providerId })
    return { success: result.success, latency_ms: result.latency_ms ?? undefined, error_message: result.success ? undefined : result.message }
  } catch (e: any) {
    return { success: false, error_code: e.code || 'TEST_FAILED', error_message: e.message }
  }
}
