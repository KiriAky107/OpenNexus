import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AiCoreStatus, IndexStatus } from '@/contracts'
import { mockIndexStatus } from '@/services/indexService'
import * as indexService from '@/services/indexService'
import * as systemService from '@/services/systemService'

export const useSettingsStore = defineStore('settings', () => {
  // General
  const restoreLastVault = ref(true)
  const autoSaveInterval = ref(1500)
  const language = ref<'zh-CN' | 'en'>('zh-CN')
  const appVersion = ref('0.1.0')
  const aiCoreVersion = ref('0.1.0')

  // Editor
  const defaultEditorMode = ref<'wysiwyg' | 'source'>('wysiwyg')
  const editorFontSize = ref(15)
  const editorLineHeight = ref(1.7)
  const editorLineWidth = ref(80)
  const spellCheck = ref(false)

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
    editorFontSize,
    editorLineHeight,
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
