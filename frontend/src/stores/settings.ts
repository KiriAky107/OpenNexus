import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { AiCoreStatus, IndexStatus } from '@/contracts'
import { resolveApiUrl } from '@/services/apiClient'
import packageInfo from '../../package.json'
import * as indexService from '@/services/indexService'
import * as systemService from '@/services/systemService'

export const useSettingsStore = defineStore('settings', () => {
  const saved = (() => {
    try { return JSON.parse(localStorage.getItem('app-settings') ?? '{}') as Record<string, unknown> }
    catch { localStorage.removeItem('app-settings'); return {} }
  })()
  // General
  const restoreLastVault = ref(saved.restoreLastVault !== false)
  const autoSaveInterval = ref(typeof saved.autoSaveInterval === 'number' ? saved.autoSaveInterval : 1500)
  const language = ref<'zh-CN' | 'en'>(saved.language === 'en' ? 'en' : 'zh-CN')
  const appVersion = ref(packageInfo.version)
  const aiCoreVersion = ref('未获取')

  // Editor
  const defaultEditorMode = ref<'wysiwyg' | 'source'>(saved.defaultEditorMode === 'source' ? 'source' : 'wysiwyg')
  const editorLineWidth = ref(typeof saved.editorLineWidth === 'number' ? saved.editorLineWidth : 80)
  const spellCheck = ref(saved.spellCheck === true)

  // AI Core
  const aiCoreStatus = ref<AiCoreStatus>('unknown')
  const aiCoreAddress = ref(resolveApiUrl('/api') || '/api')

  // Index
  const emptyIndex = (): IndexStatus => ({ status: 'unknown', pending_jobs: 0, total_notes: null, total_blocks: null })
  const indexStatus = ref<IndexStatus>(emptyIndex())

  // Permissions
  const permissionPolicy = ref<Record<string, 'allow' | 'confirm' | 'deny'>>({})
  const diagnosticsError = ref<string | null>(null)

  watch(() => ({
    restoreLastVault: restoreLastVault.value, autoSaveInterval: autoSaveInterval.value,
    language: language.value, defaultEditorMode: defaultEditorMode.value,
    editorLineWidth: editorLineWidth.value, spellCheck: spellCheck.value,
  }), (value) => localStorage.setItem('app-settings', JSON.stringify(value)), { deep: true })

  async function loadDiagnostics() {
    const results = await Promise.allSettled([
      systemService.healthCheck(), systemService.getStatus(), indexService.getIndexStatus(), systemService.getPermissionPolicy(),
    ])
    const [health, status, index, policy] = results
    aiCoreStatus.value = health.status === 'fulfilled' && health.value.status === 'ok' ? 'running' : 'error'
    aiCoreVersion.value = status.status === 'fulfilled' ? status.value.version : '未获取'
    indexStatus.value = index.status === 'fulfilled' ? index.value : emptyIndex()
    permissionPolicy.value = policy.status === 'fulfilled' ? policy.value : {}
    diagnosticsError.value = results.filter(item => item.status === 'rejected').map(item => item.reason instanceof Error ? item.reason.message : '后端请求失败').join('；') || null
  }

  function setAutoSaveInterval(ms: number) {
    autoSaveInterval.value = ms
  }

  function setDefaultEditorMode(mode: 'wysiwyg' | 'source') {
    defaultEditorMode.value = mode
  }

  async function rebuildIndex(scope: 'full' | 'fts' | 'vector' = 'full') {
    indexStatus.value.status = 'indexing'
    try {
      await indexService.rebuildIndex(scope)
      indexStatus.value = await indexService.getIndexStatus()
    } catch (reason) {
      indexStatus.value.status = 'error'
      indexStatus.value.error = reason instanceof Error ? reason.message : '索引重建失败'
    }
  }

  return {
    restoreLastVault,
    autoSaveInterval,
    language,
    appVersion,
    aiCoreVersion,
    defaultEditorMode,
    editorLineWidth,
    spellCheck,
    aiCoreStatus,
    aiCoreAddress,
    indexStatus,
    permissionPolicy,
    diagnosticsError,
    loadDiagnostics,
    setAutoSaveInterval,
    setDefaultEditorMode,
    rebuildIndex,
  }
})
