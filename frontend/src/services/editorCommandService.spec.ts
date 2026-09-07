import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { executeEditorCommand, registerEditorCommands, getEditorCommandCapabilities, subscribeEditorCommandCapabilities, updateNativeEditorMenu } from './editorCommandService'
const hostInvoke = vi.hoisted(() => vi.fn(() => Promise.resolve()))
vi.mock('./platform/desktop', () => ({ isDesktop: () => true, hostInvoke }))
let dispose: (() => void) | undefined
beforeEach(() => hostInvoke.mockClear())
afterEach(() => dispose?.())
it('reports unsupported, disabled and invalid commands without side effects', async () => {
  expect(await executeEditorCommand('editor.bold')).toEqual({ ok: false, reason: 'unavailable' })
  const handler = vi.fn(() => ({ ok: true as const }))
  let available = false
  dispose = registerEditorCommands({ available: () => available, handlers: { 'editor.bold': handler } })
  expect(getEditorCommandCapabilities().find(item => item.id === 'editor.bold')).toMatchObject({ supported: true, enabled: false })
  await executeEditorCommand('editor.bold')
  expect(handler).not.toHaveBeenCalled()
  available = true
  expect(await executeEditorCommand('editor.bold')).toEqual({ ok: true })
  expect(await executeEditorCommand('editor.metadata.edit')).toEqual({ ok: false, reason: 'unsupported' })
  expect(await executeEditorCommand('arbitrary-command')).toEqual({ ok: false, reason: 'unsupported' })
})
it('old editor disposal cannot unregister the replacement editor', async () => {
  const old = registerEditorCommands({ available: () => true, handlers: {} })
  dispose = registerEditorCommands({ available: () => true, handlers: { 'editor.bold': () => ({ ok: true }) } })
  old()
  expect(await executeEditorCommand('editor.bold')).toEqual({ ok: true })
  dispose()
  expect(await executeEditorCommand('editor.bold')).toEqual({ ok: false, reason: 'unavailable' })
})
it('notifies the visible menu when the active editor capability changes', () => {
  const listener = vi.fn()
  const unsubscribe = subscribeEditorCommandCapabilities(listener)
  dispose = registerEditorCommands({ available: () => true, handlers: { 'editor.bold': () => ({ ok: true }) } })
  expect(listener).toHaveBeenCalledTimes(1)
  updateNativeEditorMenu()
  expect(listener).toHaveBeenCalledTimes(2)
  unsubscribe()
  updateNativeEditorMenu()
  expect(listener).toHaveBeenCalledTimes(2)
})
it('keeps the native metadata accelerator enabled in writing mode', () => {
  dispose = registerEditorCommands({ available: () => true, handlers: { 'editor.metadata.edit': () => ({ ok: true }) } })
  expect(hostInvoke).toHaveBeenLastCalledWith('editor_capabilities', { metadataEnabled: true })
})
