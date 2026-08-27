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
    throw new Error(`后端请求失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<ServiceStatus>
}
