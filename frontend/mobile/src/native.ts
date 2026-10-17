import { invoke, isTauri } from '@tauri-apps/api/core'
import { onBackButtonPress } from '@tauri-apps/api/app'
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow'
import type { ExportDelivery, MobileRequest } from './types'
import type { AuthRequest, AuthResponse } from './syncAuthTypes'
import type { AssociationRequest, AssociationResponse } from './syncAssociationTypes'
import type { CycleCommand, CycleReply, CycleSnapshot } from './syncCycleTypes'

export interface MobileCapabilities {
  protocol: number
  platform: 'android'
  version: string
  workspace: boolean
  notes: boolean
  exports?: boolean
  external_links?: boolean
  navigation?: boolean
  appearance?: boolean
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
export async function mobileSyncAuth(request: AuthRequest): Promise<AuthResponse> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<AuthResponse>('mobile_sync_auth', { request })
}
export async function mobileSyncAssociation(request: AssociationRequest): Promise<AssociationResponse> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<AssociationResponse>('mobile_sync_association', { request })
}
export async function mobileSyncCycle(request: CycleCommand): Promise<CycleReply> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<CycleReply>('mobile_sync_cycle', { request })
}
export async function mobileOnSyncActivity(handler: (snapshot: CycleSnapshot) => void): Promise<() => void> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return getCurrentWebviewWindow().listen<CycleSnapshot>('mobile-sync-activity', event => handler(event.payload))
}
export async function mobileSaveExport(ticket: string): Promise<ExportDelivery> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<ExportDelivery>('mobile_save_export', { ticket })
}
export async function mobileExportDelivery(ticket: string, deliveryId?: string): Promise<ExportDelivery> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke<ExportDelivery>('mobile_export_delivery', { ticket, deliveryId: deliveryId ?? null })
}
export async function mobileOpenExternal(href: string): Promise<{ state: string }> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke('mobile_open_external', { href })
}
export interface MobileBackListener { unregister(): Promise<void> | void }
export async function mobileOnBack(handler: () => void): Promise<MobileBackListener> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return onBackButtonPress(() => handler())
}
export async function mobileBackground(): Promise<{ state: 'backgrounded' }> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  return invoke('mobile_background')
}
export async function mobileAppearance(theme: 'light' | 'dark' | 'sepia', background: string): Promise<{ state: 'applied' }> {
  if (!isTauri()) throw new Error('MOBILE_HOST_REQUIRED')
  if (!['light', 'dark', 'sepia'].includes(theme) || !/^#[\da-f]{6}$/i.test(background)) throw new Error('MOBILE_APPEARANCE_INVALID')
  return invoke('mobile_appearance', { theme, background })
}
