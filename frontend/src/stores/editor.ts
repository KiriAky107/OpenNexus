import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { SaveStatus } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'
import { getNote } from '@/services/noteService'
import { resolveBlockLocation } from '@/features/editor/blockLocation'
import { t } from '@/i18n'
import { ApiErrorClass } from '@/services/apiClient'
import { DesktopError } from '@/services/platform/desktop'
import { workspaceDocumentType } from '@/services/workspaceDocuments'
import { normalizeWorkspacePath } from '@/services/workspacePaths'
import { useWorkspaceStore } from './workspace'

export interface FileNavigation { path: string; isCurrent: () => boolean }

export const useEditorStore = defineStore('editor', () => {
  const mode = ref<'wysiwyg' | 'source'>('wysiwyg')
  const content = ref('')
  const contentRevision = ref(0)
  let diskContent: string | undefined
  const saveStatus = ref<SaveStatus>('idle')
  const lastSavedAt = ref<string | null>(null)
  const currentNoteId = ref<string | null>(null)
  const currentFilePath = ref<string | null>(null)
  const loadingFilePath = ref<string | null>(null)
  const externalReadError = ref(false)
  let documentVersion = 0
  const highlightBlockId = ref<string | null>(null)
  const blockRequest = ref<{ path: string; offset: number; length: number } | null>(null)
  const blockNavigationNotice = ref('')
  let blockVersion = 0
  const cursorPosition = ref({ line: 0, column: 0 })
  const headingRequest = ref<{ index: number; offset: number; path: string | null } | null>(null)
  const canvasNodeRequest = ref<{ path: string | null; nodeId: string } | null>(null)
  const referenceRequest = ref<{ path: string | null; offset: number; length: number; raw: string; occurrence: number } | null>(null)
  function locateReference(offset: number, length: number, raw: string, occurrence = 0) { blockVersion++; blockRequest.value = null; blockNavigationNotice.value = ''; referenceRequest.value = { path: currentFilePath.value, offset, length, raw, occurrence } }
  function selectCanvasNode(nodeId: string) { canvasNodeRequest.value = { path: currentFilePath.value, nodeId } }
  function jumpToHeading(index: number, offset: number) {
    blockVersion++; blockRequest.value = null; blockNavigationNotice.value = ''
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
    blockVersion++; blockRequest.value = null; blockNavigationNotice.value = ''; highlightBlockId.value = null
    content.value = newContent
    if (saveStatus.value !== 'conflict' && saveStatus.value !== 'external_changed') saveStatus.value = 'dirty'
  }

  /** A view may finish an input/render callback after navigation has replaced it. */
  function captureDocument() {
    const document = documentVersion, path = currentFilePath.value
    return () => document === documentVersion && path === currentFilePath.value
  }

  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let pendingSave: Promise<void> | null = null

  function cancelPendingAutoSave() {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = null
  }

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
    const document = documentVersion
    const snapshot = content.value
    const baseline = diskContent
    saveStatus.value = 'saving'
    pendingSave = (async () => {
      try {
        await workspaceService.saveFileContent(targetPath, snapshot, baseline)
        if (document === documentVersion && currentFilePath.value === targetPath) {
          diskContent = snapshot
          saveStatus.value = content.value === snapshot ? 'saved' : 'dirty'
          lastSavedAt.value = new Date().toISOString()
        }
      } catch (error) {
        if (document === documentVersion && currentFilePath.value === targetPath) saveStatus.value = (error instanceof ApiErrorClass && ['NOTE_CONTENT_CONFLICT', 'CANVAS_CONTENT_CONFLICT'].includes(error.code)) || (error instanceof DesktopError && error.code === 'REVISION_CONFLICT') ? 'conflict' : 'save_failed'
      } finally {
        pendingSave = null
        if (document === documentVersion && currentFilePath.value === targetPath && saveStatus.value === 'dirty') scheduleAutoSave()
      }
    })()
    return pendingSave
  }

  let loadVersion = 0

  async function loadFile(filePath: string): Promise<FileNavigation | null> {
    filePath = normalizeWorkspacePath(filePath)
    // Allocate before any await, including saves and same-file navigation.
    const version = ++loadVersion
    const workspace = useWorkspaceStore(), vault = workspace.vaultId
    let workspaceRevision = workspace.navigationRevision
    const isCurrent = () => version === loadVersion && vault === workspace.vaultId
      && workspaceRevision === workspace.navigationRevision && !workspace.isLoading
    const commitTab = () => {
      workspace.openFile(filePath)
      workspaceRevision = workspace.navigationRevision
      return { path: filePath, isCurrent }
    }
    loadingFilePath.value = filePath
    async function saveBeforeLeaving() {
      while (isCurrent()) {
        if (saveStatus.value === 'saving' && !pendingSave) {
          saveStatus.value = 'conflict'
          break
        }
        if (!currentFilePath.value) break
        if (pendingSave) await pendingSave
        else if (saveStatus.value === 'dirty' || saveStatus.value === 'save_failed') await save()
        else break
        if (!isCurrent()) return false
        if (saveStatus.value === 'save_failed' || saveStatus.value === 'conflict' || saveStatus.value === 'external_changed') break
      }
      if (!isCurrent()) return false
      if (['dirty', 'save_failed', 'conflict', 'external_changed'].includes(saveStatus.value)) {
        throw new Error(t('当前文件保存失败，已阻止切换以避免内容丢失。', 'The current file could not be saved. Switching was blocked to prevent data loss.'))
      }
      return true
    }
    try {
      if (!isCurrent()) return null
      if (currentFilePath.value === filePath) return commitTab()
      cancelPendingAutoSave()
      if (!(await saveBeforeLeaving())) return null
      const [loadedContent, loadedNoteId] = await Promise.all([
        workspaceService.readFileContent(filePath),
        workspaceDocumentType(filePath) === 'markdown' ? workspaceService.getNoteId(filePath) : Promise.resolve(null),
      ])
      while (true) {
        if (!isCurrent() || !(await saveBeforeLeaving())) return null
        if (!isCurrent()) return null
        if (!pendingSave && !['dirty', 'saving', 'save_failed', 'conflict', 'external_changed'].includes(saveStatus.value)) break
      }
      // No await between the final edit check, document replacement and tab commit.
      cancelPendingAutoSave()
      documentVersion++
      currentFilePath.value = filePath
      referenceRequest.value = null; canvasNodeRequest.value = null; headingRequest.value = null
      currentNoteId.value = loadedNoteId
      content.value = loadedContent
      diskContent = loadedContent
      externalReadError.value = false
      saveStatus.value = 'saved'
      lastSavedAt.value = new Date().toISOString()
      highlightBlockId.value = null
      blockRequest.value = null; blockNavigationNotice.value = ''; blockVersion++
      return commitTab()
    } catch (error) {
      if (!isCurrent()) return null
      throw error
    } finally {
      if (version === loadVersion) loadingFilePath.value = null
    }
  }

  async function highlightBlock(blockId: string) {
    const path = currentFilePath.value, noteId = currentNoteId.value
    if (!path || !noteId) return
    const workspace = useWorkspaceStore(), vault = workspace.vaultId, workspaceRevision = workspace.navigationRevision
    const version = ++blockVersion, navigation = loadVersion, ownsDocument = captureDocument()
    const isCurrent = () => version === blockVersion && navigation === loadVersion && ownsDocument()
      && vault === workspace.vaultId && workspaceRevision === workspace.navigationRevision && !workspace.isLoading
    blockNavigationNotice.value = ''; blockRequest.value = null
    headingRequest.value = null; referenceRequest.value = null
    try {
      const note = await getNote(noteId)
      if (!isCurrent()) return
      const block = note.blocks.find(item => item.block_id === blockId)
      const location = normalizeWorkspacePath(note.file_path) === path && block ? resolveBlockLocation(content.value, note.markdown, block) : null
      if (location) {
        highlightBlockId.value = blockId
        blockRequest.value = { path, ...location }
        return
      }
      blockNavigationNotice.value = t('引用段落已更新或删除，已定位到文件开头。', 'The referenced passage changed or was removed. Showing the start of the file.')
    } catch {
      if (!isCurrent()) return
      blockNavigationNotice.value = t('暂时无法读取引用位置，已定位到文件开头，可重试引用。', 'Could not load the passage location. Showing the start of the file; try the reference again.')
    }
    highlightBlockId.value = null
    blockRequest.value = { path, offset: 0, length: 0 }
  }

  function setExternalChanged() {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    if (saveStatus.value === 'dirty' || saveStatus.value === 'save_failed' || saveStatus.value === 'conflict') {
      saveStatus.value = 'conflict'
    } else {
      saveStatus.value = 'external_changed'
    }
  }

  async function checkExternalFile(): Promise<boolean> {
    if (!currentFilePath.value || pendingSave || diskContent === undefined) return false
    // Conflicts already require explicit recovery; never replace unsaved text.
    if (saveStatus.value === 'conflict') return true
    const path = currentFilePath.value, baseline = diskContent, document = documentVersion
    try {
      const latest = await workspaceService.readFileContent(path)
      if (document !== documentVersion || path !== currentFilePath.value || pendingSave || diskContent !== baseline) return false
      externalReadError.value = false
      if (latest === baseline) return true
      if (content.value === baseline && saveStatus.value === 'saved') {
        documentVersion++
        content.value = latest; diskContent = latest; contentRevision.value++
        blockRequest.value = null; blockNavigationNotice.value = ''; highlightBlockId.value = null; blockVersion++
      } else {
        setExternalChanged(); saveStatus.value = 'conflict'
      }
      return true
    } catch {
      if (document === documentVersion && path === currentFilePath.value) externalReadError.value = true
      return false
    }
  }

  async function reloadExternalFile() {
    loadVersion++; loadingFilePath.value = null
    const path = currentFilePath.value, snapshot = content.value, document = documentVersion
    if (!path || pendingSave) return
    const latest = await workspaceService.readFileContent(path)
    if (document !== documentVersion || path !== currentFilePath.value || content.value !== snapshot || pendingSave) return
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    documentVersion++
    content.value = latest; diskContent = latest; saveStatus.value = 'saved'; contentRevision.value++
    blockRequest.value = null; blockNavigationNotice.value = ''; highlightBlockId.value = null; blockVersion++
    externalReadError.value = false
  }

  async function discardExternalChanges(path: string, snapshot: string): Promise<boolean> {
    if (pendingSave) await pendingSave
    if (currentFilePath.value !== path || content.value !== snapshot) return false
    closeFile()
    return true
  }


  function closeFile() {
    loadVersion++
    documentVersion++; loadingFilePath.value = null
    cancelPendingAutoSave()
    currentFilePath.value = null
    referenceRequest.value = null; canvasNodeRequest.value = null; headingRequest.value = null
    currentNoteId.value = null
    content.value = ''
    diskContent = undefined
    externalReadError.value = false
    saveStatus.value = 'idle'
    lastSavedAt.value = null
    highlightBlockId.value = null
    blockRequest.value = null; blockNavigationNotice.value = ''; blockVersion++
  }

  function renameFilePath(oldPath: string, newPath: string) {
    loadVersion++; loadingFilePath.value = null
    if (currentFilePath.value === oldPath || currentFilePath.value?.startsWith(`${oldPath}/`)) {
      // A rename racing a write needs an explicit reload/review at the new path.
      // The old write must not mark this document saved or leave it saving forever.
      if (pendingSave || ['dirty', 'saving', 'save_failed'].includes(saveStatus.value)) {
        cancelPendingAutoSave()
        saveStatus.value = 'conflict'
      }
      documentVersion++
      currentFilePath.value = `${newPath}${currentFilePath.value.slice(oldPath.length)}`
    }
  }

  return {
    referenceRequest,
    locateReference,
    canvasNodeRequest,
    selectCanvasNode,
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
    loadingFilePath,
    externalReadError,
    highlightBlockId,
    blockRequest,
    blockNavigationNotice,
    cursorPosition,
    wordCount,
    lineCount,
    setMode,
    toggleMode,
    updateContent,
    captureDocument,
    scheduleAutoSave,
    cancelPendingAutoSave,
    save,
    loadFile,
    highlightBlock,
    setExternalChanged,
    closeFile,
    renameFilePath,
  }
})
