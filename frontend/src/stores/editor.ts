import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { SaveStatus } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'
import { t } from '@/i18n'
import { ApiErrorClass } from '@/services/apiClient'
import { DesktopError } from '@/services/platform/desktop'

export const useEditorStore = defineStore('editor', () => {
  const mode = ref<'wysiwyg' | 'source'>('wysiwyg')
  const content = ref('')
  const contentRevision = ref(0)
  let diskContent: string | undefined
  const saveStatus = ref<SaveStatus>('idle')
  const lastSavedAt = ref<string | null>(null)
  const currentNoteId = ref<string | null>(null)
  const currentFilePath = ref<string | null>(null)
  const highlightBlockId = ref<string | null>(null)
  const cursorPosition = ref({ line: 0, column: 0 })
  const headingRequest = ref<{ index: number; offset: number; path: string | null } | null>(null)
  function jumpToHeading(index: number, offset: number) {
    headingRequest.value = { index, offset, path: currentFilePath.value }
  }

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
    if (saveStatus.value !== 'conflict' && saveStatus.value !== 'external_changed') saveStatus.value = 'dirty'
  }

  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let pendingSave: Promise<void> | null = null

  function scheduleAutoSave(delay = 1500) {
    if (saveStatus.value === 'conflict' || saveStatus.value === 'external_changed') return
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveTimer = null
      void save()
    }, delay)
  }

  async function save() {
    if (!currentFilePath.value) return
    if (saveStatus.value === 'conflict' || saveStatus.value === 'external_changed') return
    if (pendingSave) return pendingSave
    // 保存路径与正文都取快照；请求完成时用户可能已继续输入或切换文件。
    const targetPath = currentFilePath.value
    const snapshot = content.value
    const baseline = diskContent
    saveStatus.value = 'saving'
    pendingSave = (async () => {
      try {
        await workspaceService.saveFileContent(targetPath, snapshot, baseline)
        if (currentFilePath.value === targetPath) {
          diskContent = snapshot
          saveStatus.value = content.value === snapshot ? 'saved' : 'dirty'
          lastSavedAt.value = new Date().toISOString()
        }
      } catch (error) {
        if (currentFilePath.value === targetPath) saveStatus.value = (error instanceof ApiErrorClass && error.code === 'NOTE_CONTENT_CONFLICT') || (error instanceof DesktopError && error.code === 'REVISION_CONFLICT') ? 'conflict' : 'save_failed'
      } finally {
        pendingSave = null
        if (currentFilePath.value === targetPath && saveStatus.value === 'dirty') scheduleAutoSave()
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
      throw new Error(t('当前文件存在编辑冲突，请处理后再切换文件。', 'The current file has an editing conflict. Resolve it before switching files.'))
    }
    if (pendingSave) await pendingSave
    if (saveStatus.value === 'dirty' || saveStatus.value === 'save_failed') await save()
    if (['dirty', 'save_failed', 'conflict', 'external_changed'].includes(saveStatus.value)) {
      throw new Error(t('当前文件保存失败，已阻止切换以避免内容丢失。', 'The current file could not be saved. Switching was blocked to prevent data loss.'))
    }
    // 版本号使较慢的旧读取不能覆盖用户后选择的新文件。
    const version = ++loadVersion
    const previousStatus = saveStatus.value
    saveStatus.value = 'saving'
    try {
      const [loadedContent, loadedNoteId] = await Promise.all([
        workspaceService.readFileContent(filePath),
        workspaceService.getNoteId(filePath),
      ])
      if (version !== loadVersion) return
      currentFilePath.value = filePath
      currentNoteId.value = loadedNoteId
      content.value = loadedContent
      diskContent = loadedContent
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
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    if (saveStatus.value === 'dirty' || saveStatus.value === 'save_failed' || saveStatus.value === 'conflict') {
      saveStatus.value = 'conflict'
    } else {
      saveStatus.value = 'external_changed'
    }
  }

  async function checkExternalFile() {
    if (!currentFilePath.value || pendingSave || diskContent === undefined || saveStatus.value === 'conflict') return
    const path = currentFilePath.value, baseline = diskContent
    try {
      const latest = await workspaceService.readFileContent(path)
      if (path !== currentFilePath.value || pendingSave || diskContent !== baseline || latest === baseline) return
      if (content.value === baseline && saveStatus.value === 'saved') {
        content.value = latest; diskContent = latest; contentRevision.value++
      } else {
        setExternalChanged(); saveStatus.value = 'conflict'
      }
    } catch { /* Tree polling reports missing files; transient network errors retain edits. */ }
  }

  async function reloadExternalFile() {
    const path = currentFilePath.value, snapshot = content.value
    if (!path || pendingSave) return
    const latest = await workspaceService.readFileContent(path)
    if (path !== currentFilePath.value || content.value !== snapshot || pendingSave) return
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    content.value = latest; diskContent = latest; saveStatus.value = 'saved'; contentRevision.value++
  }

  async function discardExternalChanges(path: string, snapshot: string): Promise<boolean> {
    if (pendingSave) await pendingSave
    if (currentFilePath.value !== path || content.value !== snapshot) return false
    closeFile()
    return true
  }


  function closeFile() {
    loadVersion++
    if (saveTimer) clearTimeout(saveTimer)
    currentFilePath.value = null
    currentNoteId.value = null
    content.value = ''
    diskContent = undefined
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
    headingRequest,
    jumpToHeading,
    mode,
    content,
    contentRevision,
    checkExternalFile,
    reloadExternalFile,
    discardExternalChanges,
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
