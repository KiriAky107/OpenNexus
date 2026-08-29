import apiClient from './apiClient'
import type { ProviderConfig, ModelInfo } from '@/contracts'

export async function listProviders(): Promise<ProviderConfig[]> {
  try {
    return await apiClient.get('/api/providers')
  } catch {
    return mockProviders
  }
}

export async function getProvider(providerId: string): Promise<ProviderConfig> {
  return apiClient.get(`/api/providers/${providerId}`)
}

export async function createProvider(data: Omit<ProviderConfig, 'provider_id'> & { api_key?: string }): Promise<ProviderConfig> {
  return apiClient.post('/api/providers', data)
}

export async function updateProvider(providerId: string, data: Partial<ProviderConfig> & { api_key?: string }): Promise<ProviderConfig> {
  return apiClient.patch(`/api/providers/${providerId}`, data)
}

export async function deleteProvider(providerId: string): Promise<void> {
  return apiClient.delete(`/api/providers/${providerId}`)
}

export async function listModels(providerId: string): Promise<ModelInfo[]> {
  try {
    return await apiClient.get(`/api/providers/${providerId}/models`)
  } catch {
    return mockModels[providerId] || []
  }
}

export interface TestResult {
  success: boolean
  latency_ms?: number
  error_code?: string
  error_message?: string
}

export async function testProvider(providerId: string): Promise<TestResult> {
  try {
    const result = await apiClient.post<{ success: boolean; latency_ms: number }>('/api/providers/test', { provider_id: providerId })
    return { success: result.success, latency_ms: result.latency_ms }
  } catch (e: any) {
    return { success: false, error_code: e.code || 'TEST_FAILED', error_message: e.message }
  }
}

export const mockProviders: ProviderConfig[] = [
  {
    provider_id: 'mock-provider',
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
    provider_type: 'openai-compatible',
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
  'mock-provider': [
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
