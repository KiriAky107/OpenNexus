import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AiCoreStatus, IndexStatus } from '@/contracts'
import { mockIndexStatus } from '@/services/indexService'

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
    setTimeout(() => {
      indexStatus.value.status = 'idle'
    }, 3000)
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
    setAutoSaveInterval,
    setDefaultEditorMode,
    setPermission,
    setAiCoreStatus,
    restartAiCore,
    rebuildIndex,
  }
})
