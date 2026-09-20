import { hostInvoke, isDesktop } from '@/services/platform/desktop'

export interface StorageLocationInfo {
  active_path: string
  default_path: string
  configured_path: string | null
  custom: boolean
  restart_required: boolean
}

export const storageLocationService = {
  available: isDesktop,
  info: () => hostInvoke<StorageLocationInfo>('storage_info'),
  choose: () => hostInvoke<StorageLocationInfo>('storage_choose'),
  useDefault: () => hostInvoke<StorageLocationInfo>('storage_use_default'),
}
