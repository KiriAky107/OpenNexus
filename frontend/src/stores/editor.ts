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
    if (saveStatus.value === 'saved' || saveStatus.value === 'idle') {
      saveStatus.value = 'dirty'
    }
  }

  let saveTimer: ReturnType<typeof setTimeout> | null = null

  function scheduleAutoSave(delay = 1500) {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      void save()
    }, delay)
  }

  async function save() {
    if (!currentFilePath.value) return
    if (saveStatus.value === 'saving') return
    saveStatus.value = 'saving'
    try {
      await workspaceService.saveFileContent(currentFilePath.value, content.value)
      saveStatus.value = 'saved'
      lastSavedAt.value = new Date().toISOString()
    } catch {
      saveStatus.value = 'save_failed'
    }
  }

  async function loadFile(filePath: string) {
    currentFilePath.value = filePath
    saveStatus.value = 'saving'
    try {
      content.value = await workspaceService.readFileContent(filePath)
      saveStatus.value = 'saved'
      lastSavedAt.value = new Date().toISOString()
    } catch {
      content.value = ''
      saveStatus.value = 'idle'
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

  function closeFile() {
    if (saveTimer) clearTimeout(saveTimer)
    currentFilePath.value = null
    currentNoteId.value = null
    content.value = ''
    saveStatus.value = 'idle'
    lastSavedAt.value = null
    highlightBlockId.value = null
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
  }
})
