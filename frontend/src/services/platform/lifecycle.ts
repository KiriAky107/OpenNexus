/** 原生命令复用活动编辑器边界；保存失败时保持窗口及内存内容。 */
import { listen } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { useEditorStore } from '@/stores/editor'
import { executeEditorCommand, updateNativeEditorMenu } from '@/services/editorCommandService'
import { watch } from 'vue'
import { isDesktop } from './desktop'

type ShortcutCommand = { id: Parameters<typeof executeEditorCommand>[0]; params?: unknown }

/** 桌面菜单快捷键表；使用 code 避免数字键受键盘布局影响。 */
export function resolveEditorShortcut(event: Pick<KeyboardEvent, 'code' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>): ShortcutCommand | undefined {
  const mod = event.ctrlKey || event.metaKey
  if (mod && !event.altKey && !event.shiftKey && /^Digit[0-6]$/.test(event.code)) {
    const level = Number(event.code.at(-1))
    return level === 0 ? { id: 'editor.paragraph' } : { id: 'editor.heading', params: level }
  }
  const key = `${mod ? 'M' : ''}${event.altKey ? 'A' : ''}${event.shiftKey ? 'S' : ''}:${event.code}`
  const commands: Record<string, ShortcutCommand> = {
    'AS:Digit5': { id: 'editor.strikethrough' },
    'M:Backquote': { id: 'editor.inline-code' },
    'M:KeyK': { id: 'editor.link' },
    'MS:KeyK': { id: 'editor.code-block' },
    'MS:KeyM': { id: 'editor.math-block' },
    'MA:KeyC': { id: 'editor.callout', params: { type: 'note', body: '提示内容' } },
    'MA:KeyT': { id: 'editor.table' },
    'MS:KeyL': { id: 'editor.inline-math' },
    'MS:KeyH': { id: 'editor.horizontal-rule' },
    'MS:BracketLeft': { id: 'editor.ordered-list' },
    'MS:BracketRight': { id: 'editor.bullet-list' },
    'MS:KeyX': { id: 'editor.task-list' },
    'MS:KeyQ': { id: 'editor.blockquote' },
  }
  return commands[key]
}

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
