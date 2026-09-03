import type { ModelRoutingConfig, ModelRoutingResponse } from '@/contracts'
import apiClient from './apiClient'

export function getModelRouting(): Promise<ModelRoutingResponse> {
  return apiClient.get('/api/model-routing')
}

// version is the last version read from the server (optimistic concurrency).
export function saveModelRouting(config: ModelRoutingConfig): Promise<ModelRoutingResponse> {
  return apiClient.put('/api/model-routing', config)
}
