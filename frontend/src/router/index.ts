import { createRouter, createWebHashHistory } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'

const routes = [
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
    path: '/extensions/plugins',
    name: 'plugins',
    component: () => import('@/features/plugins/PluginsView.vue'),
    meta: { title: 'Plugin 管理', requiresVault: true },
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

router.beforeEach((to, _from, next) => {
  const workspaceStore = useWorkspaceStore()
  if (to.meta.requiresVault && !workspaceStore.hasVault) {
    next({ path: '/' })
    return
  }
  if (to.path === '/' && workspaceStore.hasVault) {
    next({ path: '/workspace' })
    return
  }
  next()
})

router.afterEach((to) => {
  const baseTitle = 'NotesAgent'
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} · ${baseTitle}` : baseTitle
})

export default router
