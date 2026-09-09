/** 原生命令复用活动编辑器边界；保存失败时保持窗口及内存内容。 */
import { listen } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { useEditorStore } from '@/stores/editor'
import { executeEditorCommand, updateNativeEditorMenu } from '@/services/editorCommandService'
import { watch } from 'vue'
import { isDesktop } from './desktop'
import { resolveEditorShortcut } from '@/services/editorMenu'

export { resolveEditorShortcut } from '@/services/editorMenu'

async function executeMetadataCommand() {
  const result = await executeEditorCommand('editor.import-note-properties')
  if (!result.ok) await executeEditorCommand('editor.metadata.edit')
}

export async function installDesktopLifecycle() {
  if (!isDesktop()) return
  const activeEditor = useEditorStore()
  // 保存属于窗口级命令；编辑区原生处理撤销/重做，避免重复派发。
  window.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.repeat) return
    const target = event.target as HTMLElement | null
    if (target?.closest('input, textarea, select, dialog[open], [role="dialog"]')) return
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 's') {
      if (!activeEditor.currentFilePath || ['saving', 'conflict', 'external_changed'].includes(activeEditor.saveStatus)) return
      event.preventDefault()
      void activeEditor.save()
      return
    }
    if ((event.ctrlKey || event.metaKey) && event.altKey && !event.shiftKey && event.code === 'KeyP') {
      event.preventDefault()
      void executeMetadataCommand()
      return
    }
    const shortcut = resolveEditorShortcut(event)
    if (!shortcut) return
    if (!activeEditor.currentFilePath || ['saving', 'conflict', 'external_changed'].includes(activeEditor.saveStatus)) return
    event.preventDefault()
    void executeEditorCommand(shortcut.id, shortcut.params)
  })
  watch(() => [activeEditor.currentFilePath, activeEditor.saveStatus, activeEditor.mode], updateNativeEditorMenu, { flush: 'post' })
  await listen<string>('editor-command', event => {
    const focused = document.activeElement as HTMLElement | null
    if (focused?.closest('input, textarea, select, dialog[open], [role="dialog"]')) return
    if (event.payload === 'editor.import-note-properties') void executeMetadataCommand()
    else void executeEditorCommand(event.payload)
  })
  let closing = false
  await listen('host-close-requested', async () => {
    if (closing) return
    closing = true
    try {
      const editor = useEditorStore()
      if (['dirty', 'saving', 'save_failed'].includes(editor.saveStatus)) await editor.save()
      if (['saved', 'idle'].includes(editor.saveStatus)) await getCurrentWindow().destroy()
    } finally { closing = false }
  })
}
