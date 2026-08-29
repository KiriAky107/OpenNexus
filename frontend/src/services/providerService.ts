import apiClient from './apiClient'
import type { ApiModelInfo, ApiProviderConfig, ModelCapability, ModelInfo, OperationResponse, ProviderConfig } from '@/contracts'

function capabilityMap(capabilities: string[]): Partial<ModelCapability> {
  return Object.fromEntries(capabilities.map((capability) => [capability, true])) as Partial<ModelCapability>
}

function toProvider(provider: ApiProviderConfig): ProviderConfig {
  return {
    provider_id: provider.provider_id,
    provider_type: provider.provider_type,
    name: provider.name,
    base_url: provider.base_url ?? undefined,
    default_model: provider.default_model ?? '',
    enabled: provider.enabled,
    capabilities: capabilityMap(provider.capabilities),
    credential_id: provider.credential_id ?? undefined,
    has_credential: Boolean(provider.credential_id) || provider.provider_type === 'mock',
  }
}

function toModel(model: ApiModelInfo): ModelInfo {
  return { model_id: model.model, name: model.display_name, capabilities: capabilityMap(model.capabilities) }
}

export async function listProviders(): Promise<ProviderConfig[]> {
  const response = await apiClient.get<{ items: ApiProviderConfig[] }>('/api/providers')
  return response.items.map(toProvider)
}

export async function getProvider(providerId: string): Promise<ProviderConfig> {
  return toProvider(await apiClient.get<ApiProviderConfig>(`/api/providers/${providerId}`))
}

export async function createProvider(data: Omit<ProviderConfig, 'provider_id'>): Promise<ProviderConfig> {
  const response = await apiClient.post<ApiProviderConfig>('/api/providers', {
    provider_type: data.provider_type,
    name: data.name,
    base_url: data.base_url,
    default_model: data.default_model || null,
    credential_id: data.credential_id,
    enabled: data.enabled,
  })
  return toProvider(response)
}

export async function updateProvider(providerId: string, data: Partial<ProviderConfig>): Promise<ProviderConfig> {
  const response = await apiClient.patch<ApiProviderConfig>(`/api/providers/${providerId}`, {
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

export const mockProviders: ProviderConfig[] = [
  {
    provider_id: 'mock',
    provider_type: 'mock',
    name: 'Mock Provider (测试)',
    default_model: 'mock-1',
    enabled: true,
    has_credential: true,
    capabilities: {
      chat: true,
      tool_calling: true,
      streaming: true,
      vision: false,
      reasoning: false,
      structured_output: true,
      embedding: false,
    },
  },
  {
    provider_id: 'openai-compat-1',
    provider_type: 'openai_compatible',
    name: 'OpenAI 兼容服务',
    base_url: 'https://api.openai.com/v1',
    default_model: 'gpt-4o-mini',
    enabled: true,
    has_credential: true,
    capabilities: {
      chat: true,
      tool_calling: true,
      streaming: true,
      vision: true,
      reasoning: false,
      structured_output: true,
      embedding: true,
    },
  },
  {
    provider_id: 'ollama-local',
    provider_type: 'ollama',
    name: 'Ollama (本地)',
    base_url: 'http://127.0.0.1:11434',
    default_model: 'qwen2.5:7b',
    enabled: false,
    has_credential: false,
    capabilities: {
      chat: true,
      tool_calling: false,
      streaming: true,
      vision: false,
      reasoning: false,
      structured_output: false,
      embedding: true,
    },
  },
]

export const mockModels: Record<string, ModelInfo[]> = {
  mock: [
    {
      model_id: 'mock-1',
      name: 'Mock Model v1',
      capabilities: { chat: true, tool_calling: true, streaming: true, structured_output: true },
      context_window: 8192,
    },
  ],
  'openai-compat-1': [
    {
      model_id: 'gpt-4o-mini',
      name: 'GPT-4o Mini',
      capabilities: { chat: true, tool_calling: true, streaming: true, vision: true, structured_output: true },
      context_window: 128000,
    },
    {
      model_id: 'gpt-4o',
      name: 'GPT-4o',
      capabilities: { chat: true, tool_calling: true, streaming: true, vision: true, structured_output: true, reasoning: true },
      context_window: 128000,
    },
    {
      model_id: 'text-embedding-3-small',
      name: 'Text Embedding 3 Small',
      capabilities: { embedding: true },
    },
  ],
  'ollama-local': [
    {
      model_id: 'qwen2.5:7b',
      name: 'Qwen 2.5 7B',
      capabilities: { chat: true, streaming: true },
      context_window: 32768,
    },
    {
      model_id: 'bge-m3',
      name: 'BGE M3',
      capabilities: { embedding: true },
    },
  ],
}
