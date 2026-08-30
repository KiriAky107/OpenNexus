import apiClient from './apiClient'
import type { ApiPlugin, OperationResponse, Plugin, PluginContribution } from '@/contracts'

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

export async function installPlugin(packagePath: string): Promise<Plugin> {
  return toPlugin(await apiClient.post<ApiPlugin>('/api/plugins/install', { package_path: packagePath }))
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

export async function uninstallPlugin(pluginId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/plugins/${pluginId}`)
}

export const mockPlugins: Plugin[] = [
  {
    plugin_id: 'github-integration',
    name: 'GitHub 集成',
    version: '1.3.2',
    description: '接入 GitHub API，支持搜索 Issue、查看 PR 和管理仓库',
    icon: '',
    author: 'NotesAgent 团队',
    status: 'ready',
    enabled: true,
    permissions: ['notes.read', 'network.request'],
    contributions: [
      { type: 'tool', id: 'github.search_issues', name: '搜索 Issue', description: '搜索 GitHub 仓库中的 Issue' },
      { type: 'tool', id: 'github.get_pr', name: '获取 PR 详情', description: '获取 Pull Request 的详细信息' },
      { type: 'command', id: 'github.open_repo', name: '打开仓库', description: '在浏览器中打开对应 GitHub 仓库' },
    ],
    backend_type: 'mcp',
    transport: 'stdio',
    dependent_skills: ['research-assistant'],
  },
  {
    plugin_id: 'translator',
    name: '翻译助手',
    version: '1.0.0',
    description: '提供多语言翻译能力，支持文档批量翻译',
    icon: '',
    author: '社区贡献',
    status: 'ready',
    enabled: false,
    permissions: ['notes.read', 'notes.write', 'network.request'],
    contributions: [
      { type: 'tool', id: 'translator.translate', name: '翻译文本', description: '翻译指定文本到目标语言' },
      { type: 'command', id: 'translator.translate_note', name: '翻译当前笔记', description: '翻译当前打开的笔记' },
      { type: 'settings_section', id: 'translator.settings', name: '翻译设置', description: '配置翻译服务和默认语言' },
    ],
    backend_type: 'mcp',
    transport: 'stdio',
  },
  {
    plugin_id: 'kanban',
    name: '看板视图',
    version: '0.8.0',
    description: '为任务提供看板视图，支持拖拽排序和多维度筛选',
    icon: '',
    author: '社区贡献',
    status: 'installed',
    enabled: false,
    permissions: ['tasks.read', 'tasks.write'],
    contributions: [
      { type: 'sidebar_panel', id: 'kanban.panel', name: '任务看板', description: '以看板方式查看和管理任务' },
    ],
    backend_type: 'internal_rpc',
  },
  {
    plugin_id: 'pdf-importer',
    name: 'PDF 导入',
    version: '2.1.0',
    description: '导入 PDF 文档，提取文本和目录结构生成笔记',
    icon: '',
    author: 'NotesAgent 团队',
    status: 'error',
    enabled: false,
    permissions: ['notes.write', 'attachments.read'],
    contributions: [
      { type: 'importer', id: 'pdf.import', name: 'PDF 导入器', description: '从 PDF 文件导入内容' },
    ],
    backend_type: 'mcp',
    transport: 'stdio',
    last_error: 'PDF 解析库初始化失败，请检查 Python 依赖',
  },
  {
    plugin_id: 'calendar',
    name: '日历同步',
    version: '0.5.0',
    description: '同步日历事件，自动生成相关笔记和任务提醒',
    icon: '',
    author: '社区贡献',
    status: 'dependency_missing',
    enabled: false,
    permissions: ['tasks.read', 'tasks.write', 'network.request'],
    contributions: [
      { type: 'tool', id: 'calendar.events', name: '日历事件', description: '获取日历事件列表' },
      { type: 'sidebar_panel', id: 'calendar.widget', name: '日历小部件', description: '侧边栏日历视图' },
    ],
    backend_type: 'mcp',
    transport: 'http',
  },
]
