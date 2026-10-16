import { invoke, isTauri } from '@tauri-apps/api/core'
import type { MobileRequest } from './types'

export interface MobileCapabilities {
  protocol: number
  platform: 'android'
  version: string
  workspace: boolean
  notes: boolean
  core: boolean
  sync: boolean
  credentials: boolean
  experiments: boolean
}

export interface MobileVault { vault_id: string; note_count: number }

export async function mobileCapabilities(): Promise<MobileCapabilities> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  const result = await invoke<MobileCapabilities>('mobile_capabilities')
  if (result.protocol !== 1 || result.platform !== 'android' || result.workspace !== true) {
    throw new Error('MOBILE_HOST_INCOMPATIBLE')
  }
  return result
}

export async function mobileVault(): Promise<MobileVault> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<MobileVault>('mobile_vault')
}

export async function mobileRequest<T>(request: MobileRequest): Promise<T> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<T>('mobile_request', { request })
}
