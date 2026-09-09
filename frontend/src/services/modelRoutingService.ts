import type { ModelRoutingConfig, ModelRoutingResponse } from '@/contracts'
import apiClient from './apiClient'

export function getModelRouting(): Promise<ModelRoutingResponse> {
  return apiClient.get('/api/model-routing')
}

// 版本是从服务器读取的最后一个版本（乐观并发）。
export function saveModelRouting(config: ModelRoutingConfig): Promise<ModelRoutingResponse> {
  return apiClient.put('/api/model-routing', config)
}
