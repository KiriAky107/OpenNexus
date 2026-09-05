import apiClient from './apiClient'
import type { SystemStatus } from '@/contracts'

export function healthCheck(): Promise<{ status: string }> {
  return apiClient.get('/health', { timeoutMs: 10000 })
}

export function getStatus(): Promise<SystemStatus> {
  return apiClient.get('/api/status', { timeoutMs: 10000 })
}

export function getPermissionPolicy(): Promise<Record<string, 'allow' | 'confirm' | 'deny'>> {
  return apiClient.get('/api/permissions/policy', { timeoutMs: 10000 })
}
