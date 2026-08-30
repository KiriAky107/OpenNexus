import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { SaveStatus } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'

export const useEditorStore = defineStore('editor', () => {
  const mode = ref<'wysiwyg' | 'source'>('wysiwyg')
  const content = ref('')
  const saveStatus = ref<SaveStatus>('idle')
  const lastSavedAt = ref<string | null>(null)
  const currentNoteId = ref<string | null>(null)
  const currentFilePath = ref<string | null>(null)
  const highlightBlockId = ref<string | null>(null)
  const cursorPosition = ref({ line: 0, column: 0 })

  const wordCount = computed(() => {
    const text = content.value.replace(/[#*`>\-_\[\]()!]/g, '')
    return text.trim().length
  })

  const lineCount = computed(() => content.value.split('\n').length)

  function setMode(newMode: 'wysiwyg' | 'source') {
    mode.value = newMode
  }

  function toggleMode() {
    mode.value = mode.value === 'wysiwyg' ? 'source' : 'wysiwyg'
  }

  function updateContent(newContent: string) {
    content.value = newContent
    saveStatus.value = 'dirty'
  }

  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let pendingSave: Promise<void> | null = null

  function scheduleAutoSave(delay = 1500) {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveTimer = null
      void save()
    }, delay)
  }

  async function save() {
    if (!currentFilePath.value) return
    if (pendingSave) return pendingSave
    // 保存路径与正文都取快照；请求完成时用户可能已继续输入或切换文件。
    const targetPath = currentFilePath.value
    const snapshot = content.value
    saveStatus.value = 'saving'
    pendingSave = (async () => {
      try {
        await workspaceService.saveFileContent(targetPath, snapshot)
        if (currentFilePath.value === targetPath) {
          saveStatus.value = content.value === snapshot ? 'saved' : 'dirty'
          lastSavedAt.value = new Date().toISOString()
        }
      } catch {
        if (currentFilePath.value === targetPath) saveStatus.value = 'save_failed'
      } finally {
        pendingSave = null
      }
    })()
    return pendingSave
  }

  let loadVersion = 0

  async function loadFile(filePath: string) {
    if (currentFilePath.value === filePath) return
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    if (saveStatus.value === 'conflict') {
      throw new Error('当前文件存在编辑冲突，请处理后再切换文件。')
    }
    if (pendingSave) await pendingSave
    if (saveStatus.value === 'dirty' || saveStatus.value === 'save_failed') await save()
    if (saveStatus.value === 'dirty' || saveStatus.value === 'save_failed') {
      throw new Error('当前文件保存失败，已阻止切换以避免内容丢失。')
    }
    // 版本号使较慢的旧读取不能覆盖用户后选择的新文件。
    const version = ++loadVersion
    const previousStatus = saveStatus.value
    saveStatus.value = 'saving'
    try {
      const loadedContent = await workspaceService.readFileContent(filePath)
      if (version !== loadVersion) return
      currentFilePath.value = filePath
      content.value = loadedContent
      saveStatus.value = 'saved'
      lastSavedAt.value = new Date().toISOString()
    } catch (error) {
      if (version !== loadVersion) return
      saveStatus.value = previousStatus
      throw error
    }
    highlightBlockId.value = null
  }

  function highlightBlock(blockId: string) {
    highlightBlockId.value = blockId
    setTimeout(() => {
      if (highlightBlockId.value === blockId) {
        highlightBlockId.value = null
      }
    }, 3000)
  }

  function setExternalChanged() {
    if (saveStatus.value === 'dirty') {
      saveStatus.value = 'conflict'
    } else {
      saveStatus.value = 'external_changed'
    }
  }

  // TODO(editor): 桌面文件监听接入后提供冲突对比/合并界面，而非只阻止切换。

  function closeFile() {
    loadVersion++
    if (saveTimer) clearTimeout(saveTimer)
    currentFilePath.value = null
    currentNoteId.value = null
    content.value = ''
    saveStatus.value = 'idle'
    lastSavedAt.value = null
    highlightBlockId.value = null
  }

  function renameFilePath(oldPath: string, newPath: string) {
    if (currentFilePath.value === oldPath || currentFilePath.value?.startsWith(`${oldPath}/`)) {
      currentFilePath.value = `${newPath}${currentFilePath.value.slice(oldPath.length)}`
    }
  }

  return {
    mode,
    content,
    saveStatus,
    lastSavedAt,
    currentNoteId,
    currentFilePath,
    highlightBlockId,
    cursorPosition,
    wordCount,
    lineCount,
    setMode,
    toggleMode,
    updateContent,
    scheduleAutoSave,
    save,
    loadFile,
    highlightBlock,
    setExternalChanged,
    closeFile,
    renameFilePath,
  }
})
