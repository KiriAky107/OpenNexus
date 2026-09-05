import { t } from '@/i18n'

export interface ServiceStatus {
  name: string
  version: string
  environment: string
  status: 'ok'
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export async function getServiceStatus(): Promise<ServiceStatus> {
  const response = await fetch(`${apiBaseUrl}/api/status`)
  if (!response.ok) {
    throw new Error(`${t('后端请求失败：', 'Backend request failed: ')}HTTP ${response.status}`)
  }
  return response.json() as Promise<ServiceStatus>
}
