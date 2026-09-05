import apiClient from './apiClient'
import type {
  ApiPlugin,
  OperationResponse,
  Plugin,
  PluginCommand,
  PluginCommandContext,
  PluginCommandLocation,
  PluginCommandResult,
  PluginContribution,
  PluginHostStatus,
  PluginSecretStatus,
  PluginSettingsSchema,
} from '@/contracts'

function toPlugin(plugin: ApiPlugin): Plugin {
  const { manifest } = plugin
  const contributions: PluginContribution[] = []
  const append = (type: PluginContribution['type'], values: string[]) => {
    values.forEach((id) => contributions.push({ type, id, name: id }))
  }
  append('tool', manifest.contributes.tools)
  append('command', manifest.contributes.commands)
  append('importer', manifest.contributes.importers)
  append('exporter', manifest.contributes.exporters)
  append('sidebar_panel', manifest.contributes.panels)
  append('settings_section', manifest.contributes.settings_sections)
  return {
    plugin_id: manifest.plugin_id,
    name: manifest.name,
    version: manifest.version,
    description: manifest.description,
    status: plugin.status,
    enabled: plugin.enabled,
    permissions: manifest.permissions,
    granted_permissions: plugin.granted_permissions,
    contributions,
    backend_type: manifest.backend.type,
    transport: manifest.backend.transport,
    last_error: plugin.error_message ?? undefined,
  }
}

export async function listPlugins(): Promise<Plugin[]> {
  const response = await apiClient.get<{ items: ApiPlugin[] }>('/api/plugins')
  return response.items.map(toPlugin)
}

export async function getPlugin(pluginId: string): Promise<Plugin> {
  return toPlugin(await apiClient.get<ApiPlugin>(`/api/plugins/${pluginId}`))
}

export async function installPlugin(source: string | File): Promise<Plugin> {
  const installed = typeof source === 'string'
    ? await apiClient.post<ApiPlugin>('/api/plugins/install', { package_path: source })
    : await apiClient.postBinary<ApiPlugin>('/api/plugins/install-zip', source)
  return toPlugin(installed)
}

export async function enablePlugin(pluginId: string): Promise<Plugin> {
  return toPlugin(await apiClient.post<ApiPlugin>(`/api/plugins/${pluginId}/enable`))
}

export async function disablePlugin(pluginId: string): Promise<Plugin> {
  return toPlugin(await apiClient.post<ApiPlugin>(`/api/plugins/${pluginId}/disable`))
}

export async function grantPluginPermissions(pluginId: string, permissions: string[]): Promise<Plugin> {
  return toPlugin(await apiClient.put<ApiPlugin>(`/api/plugins/${pluginId}/permissions`, { permissions }))
}

export async function getPluginHostStatus(pluginId: string): Promise<PluginHostStatus> {
  return apiClient.get(`/api/plugins/${pluginId}/host`)
}

export async function restartPluginHost(pluginId: string): Promise<OperationResponse> {
  return apiClient.post(`/api/plugins/${pluginId}/host/restart`)
}

export async function listPluginCommands(location?: PluginCommandLocation): Promise<PluginCommand[]> {
  const query = location ? `?location=${encodeURIComponent(location)}` : ''
  const response = await apiClient.get<{ items: PluginCommand[] }>(`/api/plugin-contributions/commands${query}`)
  return response.items
}

export async function executePluginCommand(
  commandId: string,
  argumentsValue: Record<string, unknown> = {},
  context: PluginCommandContext = {},
): Promise<PluginCommandResult> {
  return apiClient.post(`/api/plugin-contributions/commands/${encodeURIComponent(commandId)}/execute`, {
    arguments: argumentsValue,
    context,
  })
}

export async function getPluginSettings(pluginId: string): Promise<PluginSettingsSchema> {
  return apiClient.get(`/api/plugins/${encodeURIComponent(pluginId)}/settings`)
}

export async function updatePluginSettings(
  pluginId: string,
  schemaVersion: number,
  values: Record<string, unknown>,
): Promise<PluginSettingsSchema> {
  return apiClient.put(`/api/plugins/${encodeURIComponent(pluginId)}/settings`, {
    schema_version: schemaVersion,
    values,
  })
}

export async function putPluginSecret(
  pluginId: string,
  key: string,
  secret: string,
): Promise<PluginSecretStatus> {
  return apiClient.put(
    `/api/plugins/${encodeURIComponent(pluginId)}/settings/${encodeURIComponent(key)}/secret`,
    { secret },
  )
}

export async function deletePluginSecret(pluginId: string, key: string): Promise<PluginSecretStatus> {
  return apiClient.delete(
    `/api/plugins/${encodeURIComponent(pluginId)}/settings/${encodeURIComponent(key)}/secret`,
  )
}

export async function uninstallPlugin(pluginId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/plugins/${pluginId}`)
}
