import apiClient from './apiClient'
import type { McpServer, McpServerInput, McpToolSummary, OperationResponse } from '@/contracts'

const base = '/api/mcp/servers'

export async function listMcpServers(): Promise<McpServer[]> {
  return (await apiClient.get<{ items: McpServer[] }>(base)).items
}
export const createMcpServer = (input: McpServerInput) => apiClient.post<McpServer>(base, input)
export const updateMcpServer = (id: string, input: McpServerInput) => apiClient.put<McpServer>(`${base}/${id}`, input)
export const listMcpServerTools = async (id: string) => (await apiClient.get<{ items: McpToolSummary[] }>(`${base}/${id}/tools`)).items
export const deleteMcpServer = (id: string) => apiClient.delete<OperationResponse>(`${base}/${id}`)
export const trustMcpServer = (server: McpServer) => apiClient.post<McpServer>(`${base}/${server.server_id}/trust`, { command_digest: server.command_digest })
export const testMcpServer = (id: string) => apiClient.post<McpServer>(`${base}/${id}/test`)
export const enableMcpServer = (id: string) => apiClient.post<McpServer>(`${base}/${id}/enable`)
export const disableMcpServer = (id: string) => apiClient.post<McpServer>(`${base}/${id}/disable`)
export const putMcpServerSecret = (id: string, key: string, secret: string, kind: 'environment' | 'header' = 'environment') => apiClient.put(`${base}/${id}/secrets/${encodeURIComponent(key)}?kind=${kind}`, { secret })
export const deleteMcpServerSecret = (id: string, key: string, kind: 'environment' | 'header' = 'environment') => apiClient.delete(`${base}/${id}/secrets/${encodeURIComponent(key)}?kind=${kind}`)
