/** 原生命令复用活动编辑器边界；保存失败时保持窗口及内存内容。 */
import { listen } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { useEditorStore } from '@/stores/editor'
import { executeEditorCommand, updateNativeEditorMenu } from '@/services/editorCommandService'
import { watch } from 'vue'
import { isDesktop } from './desktop'

export async function installDesktopLifecycle() {
  if (!isDesktop()) return
  const activeEditor = useEditorStore()
  watch(() => [activeEditor.currentFilePath, activeEditor.saveStatus, activeEditor.mode], updateNativeEditorMenu, { flush: 'post' })
  await listen<string>('editor-command', event => { void executeEditorCommand(event.payload) })
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
