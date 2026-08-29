import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { AiCoreStatus, IndexStatus } from '@/contracts'
import { mockIndexStatus } from '@/services/indexService'
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
  const appVersion = ref('0.1.0')
  const aiCoreVersion = ref('0.1.0')

  // Editor
  const defaultEditorMode = ref<'wysiwyg' | 'source'>(saved.defaultEditorMode === 'source' ? 'source' : 'wysiwyg')
  const editorLineWidth = ref(typeof saved.editorLineWidth === 'number' ? saved.editorLineWidth : 80)
  const spellCheck = ref(saved.spellCheck === true)

  // AI Core
  const aiCoreStatus = ref<AiCoreStatus>('running')
  const aiCoreAddress = ref('http://127.0.0.1:8000')

  // Index
  const indexStatus = ref<IndexStatus>(mockIndexStatus)

  // Permissions
  const permissionPolicy = ref<Record<string, 'allow' | 'confirm' | 'deny'>>({
    'notes.read': 'allow',
    'notes.search': 'allow',
    'notes.write': 'confirm',
    'notes.delete': 'confirm',
    'tasks.read': 'allow',
    'tasks.write': 'confirm',
    'attachments.read': 'confirm',
    'network.request': 'confirm',
    'secrets.use': 'confirm',
  })
  const diagnosticsError = ref<string | null>(null)

  watch(() => ({
    restoreLastVault: restoreLastVault.value, autoSaveInterval: autoSaveInterval.value,
    language: language.value, defaultEditorMode: defaultEditorMode.value,
    editorLineWidth: editorLineWidth.value, spellCheck: spellCheck.value,
  }), (value) => localStorage.setItem('app-settings', JSON.stringify(value)), { deep: true })

  async function loadDiagnostics() {
    try {
      const [health, status, index] = await Promise.all([
        systemService.healthCheck(), systemService.getStatus(), indexService.getIndexStatus(),
      ])
      aiCoreStatus.value = health.status === 'ok' ? 'running' : 'error'
      aiCoreVersion.value = status.version
      indexStatus.value = index
      diagnosticsError.value = null
    } catch (reason) {
      aiCoreStatus.value = 'error'
      diagnosticsError.value = reason instanceof Error ? reason.message : '诊断信息加载失败'
    }
  }

  function setAutoSaveInterval(ms: number) {
    autoSaveInterval.value = ms
  }

  function setDefaultEditorMode(mode: 'wysiwyg' | 'source') {
    defaultEditorMode.value = mode
  }

  function setPermission(permission: string, policy: 'allow' | 'confirm' | 'deny') {
    permissionPolicy.value[permission] = policy
  }

  function setAiCoreStatus(status: AiCoreStatus) {
    aiCoreStatus.value = status
  }

  async function restartAiCore(): Promise<boolean> {
    aiCoreStatus.value = 'starting'
    await new Promise((r) => setTimeout(r, 1500))
    aiCoreStatus.value = 'running'
    return true
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
    setPermission,
    setAiCoreStatus,
    restartAiCore,
    rebuildIndex,
  }
})
