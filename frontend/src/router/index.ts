import { createRouter, createWebHashHistory } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'

const routes = [
  { path: '/community', name: 'community', component: () => import('@/features/community/CommunityView.vue'), meta: { title: '社区目录' } },
  { path: '/benchmarks', name: 'benchmarks', component: () => import('@/features/benchmarks/BenchmarkView.vue'), meta: { title: 'Benchmark' } },
  { path: '/logs', name: 'logs', component: () => import('@/features/logs/LogsView.vue'), meta: { title: '运行日志' } },
  { path: '/help/function-plot', name: 'function-plot-help', component: () => import('@/features/help/FunctionPlotHelpView.vue'), meta: { title: 'Function Plot 教程' } },
  { path: '/media', name: 'media', component: () => import('@/features/media/MediaView.vue'), meta: { title: '音视频转写', requiresVault: true } },
  {
    path: '/',
    name: 'vault-entry',
    component: () => import('@/features/vault/VaultEntry.vue'),
    meta: { title: '选择知识库' },
  },
  {
    path: '/workspace',
    name: 'workspace',
    component: () => import('@/features/workspace/WorkspaceView.vue'),
    meta: { title: '工作区', requiresVault: true },
  },
  {
    path: '/search',
    name: 'search',
    component: () => import('@/features/search/SearchView.vue'),
    meta: { title: '搜索', requiresVault: true },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/features/chat/ChatView.vue'),
    meta: { title: 'AI 对话', requiresVault: true },
  },
  {
    path: '/agent/runs/:runId?',
    name: 'agent',
    component: () => import('@/features/agent/AgentView.vue'),
    meta: { title: 'Agent Trace', requiresVault: true },
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('@/features/tasks/TasksView.vue'),
    meta: { title: '任务', requiresVault: true },
  },
  {
    path: '/extensions/skills',
    name: 'skills',
    component: () => import('@/features/skills/SkillsView.vue'),
    meta: { title: 'Skill 管理', requiresVault: true },
  },
  {
    path: '/extensions/mcp',
    name: 'mcp-servers',
    component: () => import('@/features/mcp/McpServersView.vue'),
    meta: { title: 'MCP 服务器', requiresVault: true },
  },
  {
    path: '/extensions/plugins',
    name: 'plugins',
    component: () => import('@/features/plugins/PluginsView.vue'),
    meta: { title: 'Plugin 与 MCP', requiresVault: true },
  },
  {
    path: '/themes',
    name: 'themes',
    component: () => import('@/features/themes/ThemesView.vue'),
    meta: { title: '主题管理', requiresVault: true },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/features/settings/SettingsView.vue'),
    meta: { title: '设置', requiresVault: true },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const workspaceStore = useWorkspaceStore()
  // Benchmark 不依赖工作区界面；报告中的持久化 Trace 和待决权限入口必须仍可访问。
  const existingAgentRun = to.name === 'agent' && Boolean(to.params.runId)
  if (to.meta.requiresVault && !workspaceStore.hasVault && !existingAgentRun) {
    return { path: '/' }
  }
  if (to.path === '/' && workspaceStore.hasVault) {
    return { path: '/workspace' }
  }
  return true
})

export function updateDocumentTitle(to = router.currentRoute.value) {
  const baseTitle = 'OpenNexus'
  const titles: Record<string, string> = {
    logs: t('运行日志', 'Operation logs'),
    'function-plot-help': t('Function Plot 教程', 'Function Plot Tutorial'),
    media: t('音视频转写', 'Media Transcription'),
    'vault-entry': t('选择知识库', 'Select Knowledge Base'),
    workspace: t('工作区', 'Workspace'),
    search: t('搜索', 'Search'),
    chat: t('AI 对话', 'AI Chat'),
    agent: 'Agent Trace',
    tasks: t('任务', 'Tasks'),
    skills: t('Skill 管理', 'Skill Management'),
    'mcp-servers': t('MCP 服务器', 'MCP Servers'),
    plugins: t('Plugin 与 MCP', 'Plugins and MCP'),
    themes: t('主题管理', 'Theme Management'),
    settings: t('设置', 'Settings'),
  }
  const title = titles[String(to.name ?? '')] ?? (to.meta.title as string | undefined)
  document.title = title ? `${title} · ${baseTitle}` : baseTitle
}

router.afterEach(updateDocumentTitle)

export default router
