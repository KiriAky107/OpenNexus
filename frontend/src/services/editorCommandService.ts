/** 活动编辑器命令边界；原生菜单复用能力检测和处理器。 */
import { hostInvoke, isDesktop } from './platform/desktop'
export const editorCommandVersion = 1
export const editorCommandIds = [
  'editor.bold', 'editor.italic', 'editor.strikethrough', 'editor.inline-code',
  'editor.paragraph', 'editor.heading', 'editor.bullet-list', 'editor.ordered-list',
  'editor.task-list', 'editor.blockquote', 'editor.callout', 'editor.code-block',
  'editor.inline-math', 'editor.math-block', 'editor.mermaid', 'editor.link',
  'editor.image', 'editor.table', 'editor.horizontal-rule', 'editor.hard-break',
  'editor.font-size', 'editor.insert-markdown', 'editor.import-note-properties',
  'editor.metadata.edit', 'editor.metadata.title', 'editor.metadata.tags',
  'editor.reference-link', 'editor.html', 'editor.undo', 'editor.redo',
  'editor.heading.toggle-fold', 'editor.heading.fold-all', 'editor.heading.unfold-all',
] as const
export type EditorCommandId = typeof editorCommandIds[number]
export type CommandResult = { ok: true } | { ok: false; reason: 'unsupported' | 'unavailable' | 'invalid-params' | 'failed' }
export type CommandHandler = (params: unknown) => CommandResult | Promise<CommandResult>
type Target = { available: () => boolean; handlers: Partial<Record<EditorCommandId, CommandHandler>> }
let active: Target | undefined

export function registerEditorCommands(target: Target) {
  active = target
  updateNativeEditorMenu()
  return () => { if (active === target) { active = undefined; updateNativeEditorMenu() } }
}
export function updateNativeEditorMenu() {
  if (isDesktop()) void hostInvoke('editor_capabilities', { importEnabled: !!active?.handlers['editor.import-note-properties'] && active.available() }).catch(() => undefined)
}
export function getEditorCommandCapabilities() {
  return editorCommandIds.map(id => ({ id, supported: !!active?.handlers[id], enabled: !!active?.handlers[id] && active.available() }))
}
export async function executeEditorCommand(id: string, params?: unknown): Promise<CommandResult> {
  if (!(editorCommandIds as readonly string[]).includes(id)) return { ok: false, reason: 'unsupported' }
  const target = active
  if (!target || !target.available()) return { ok: false, reason: 'unavailable' }
  const handler = target.handlers[id as EditorCommandId]
  if (!handler) return { ok: false, reason: 'unsupported' }
  try { return await handler(params) } catch { return { ok: false, reason: 'failed' } }
}
