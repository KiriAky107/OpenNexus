import type { EditorCommandId } from './editorCommandService'

export type EditorMenuCommand = {
  id: EditorCommandId
  label: readonly [zh: string, en: string]
  shortcut?: string
  params?: unknown
}

export type ShortcutEvent = Pick<KeyboardEvent, 'code' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>
export type ShortcutCommand = Pick<EditorMenuCommand, 'id' | 'params'>

export const headingMenuCommands: readonly EditorMenuCommand[] = [1, 2, 3, 4, 5, 6].map(level => ({
  id: 'editor.heading',
  label: [`${level} 级标题`, `Heading ${level}`],
  shortcut: `Ctrl+${level}`,
  params: level,
}))

export const paragraphMenuSections: readonly (readonly EditorMenuCommand[])[] = [
  [{ id: 'editor.paragraph', label: ['正文', 'Body text'], shortcut: 'Ctrl+0' }, ...headingMenuCommands],
  [
    { id: 'editor.bullet-list', label: ['无序列表', 'Bullet list'], shortcut: 'Ctrl+Shift+]' },
    { id: 'editor.ordered-list', label: ['有序列表', 'Ordered list'], shortcut: 'Ctrl+Shift+[' },
    { id: 'editor.task-list', label: ['任务列表', 'Task list'], shortcut: 'Ctrl+Shift+X' },
    { id: 'editor.blockquote', label: ['引用', 'Blockquote'], shortcut: 'Ctrl+Shift+Q' },
    { id: 'editor.code-block', label: ['代码块', 'Code block'], shortcut: 'Ctrl+Shift+K' },
  ],
]

export const formatMenuSections: readonly (readonly EditorMenuCommand[])[] = [
  [
    { id: 'editor.bold', label: ['加粗', 'Bold'], shortcut: 'Ctrl+B' },
    { id: 'editor.italic', label: ['斜体', 'Italic'], shortcut: 'Ctrl+I' },
    { id: 'editor.strikethrough', label: ['删除线', 'Strikethrough'], shortcut: 'Alt+Shift+5' },
    { id: 'editor.inline-code', label: ['行内代码', 'Inline code'], shortcut: 'Ctrl+`' },
    { id: 'editor.link', label: ['链接', 'Link'], shortcut: 'Ctrl+K' },
  ],
  [
    { id: 'editor.code-block', label: ['代码块', 'Code block'], shortcut: 'Ctrl+Shift+K' },
    { id: 'editor.math-block', label: ['公式块', 'Math block'], shortcut: 'Ctrl+Shift+M' },
    { id: 'editor.inline-math', label: ['行内公式', 'Inline math'], shortcut: 'Ctrl+Shift+L' },
    { id: 'editor.table', label: ['表格', 'Table'], shortcut: 'Ctrl+Alt+T' },
  ],
  [
    { id: 'editor.horizontal-rule', label: ['水平分割线', 'Horizontal rule'], shortcut: 'Ctrl+Shift+H' },
    { id: 'editor.hard-break', label: ['硬换行', 'Hard break'] },
  ],
]

export const calloutMenuCommands: readonly EditorMenuCommand[] = [
  ['note', '笔记', 'Note'], ['abstract', '摘要', 'Abstract'], ['info', '信息', 'Info'],
  ['todo', '待办', 'Todo'], ['tip', '技巧', 'Tip'], ['important', '重要', 'Important'],
  ['success', '成功', 'Success'], ['question', '问题', 'Question'], ['warning', '警告', 'Warning'],
  ['failure', '失败', 'Failure'], ['danger', '危险', 'Danger'], ['bug', '缺陷', 'Bug'],
  ['example', '示例', 'Example'], ['quote', '引用', 'Quote'],
].map(([type, zh, en]) => ({
  id: 'editor.callout',
  label: [zh, en],
  params: { type, body: '提示内容' },
}))

const shortcutCommands: Readonly<Record<string, ShortcutCommand>> = {
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

/** 唯一桌面快捷键解析表；菜单文字与生命周期分发都引用本模块。 */
export function resolveEditorShortcut(event: ShortcutEvent): ShortcutCommand | undefined {
  const mod = event.ctrlKey || event.metaKey
  if (mod && !event.altKey && !event.shiftKey && /^Digit[0-6]$/.test(event.code)) {
    const level = Number(event.code.at(-1))
    return level === 0 ? { id: 'editor.paragraph' } : { id: 'editor.heading', params: level }
  }
  const key = `${mod ? 'M' : ''}${event.altKey ? 'A' : ''}${event.shiftKey ? 'S' : ''}:${event.code}`
  return shortcutCommands[key]
}
