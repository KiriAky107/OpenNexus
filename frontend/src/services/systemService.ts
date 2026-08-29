import apiClient from './apiClient'
import type { SystemStatus } from '@/contracts'

export async function healthCheck(): Promise<{ status: string }> {
  try {
    return await apiClient.get<{ status: string }>('/health')
  } catch {
    return { status: 'unavailable' }
  }
}

export async function getStatus(): Promise<SystemStatus> {
  try {
    return await apiClient.get<SystemStatus>('/api/status')
  } catch {
    return {
      name: 'notes-agent',
      version: '0.1.0',
      environment: import.meta.env.DEV ? 'development' : 'production',
      ai_core_available: false,
    }
  }
}
