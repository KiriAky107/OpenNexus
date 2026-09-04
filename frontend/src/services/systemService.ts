import apiClient from './apiClient'
import type { SystemStatus } from '@/contracts'

export function healthCheck(): Promise<{ status: string }> {
  return apiClient.get('/health')
}

export function getStatus(): Promise<SystemStatus> {
  return apiClient.get('/api/status')
}

export function getPermissionPolicy(): Promise<Record<string, 'allow' | 'confirm' | 'deny'>> {
  return apiClient.get('/api/permissions/policy')
}
