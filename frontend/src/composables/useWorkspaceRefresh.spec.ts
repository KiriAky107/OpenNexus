// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceRefresh } from './useWorkspaceRefresh'
import type { FileNode } from '@/contracts'
import * as service from '@/services/workspaceService'

const native = vi.hoisted(() => ({ desktop: true, listen: vi.fn(), stop: vi.fn() }))
vi.mock('@tauri-apps/api/event', () => ({ listen: native.listen }))
vi.mock('@/services/platform/desktop', async original => ({ ...await original<object>(), isDesktop: () => native.desktop }))
let event: (event: { payload: { vault_id: string; revision: number; paths: string[] } }) => void
let wrapper: ReturnType<typeof mount> | undefined
const node = (hash: string): FileNode => ({ id: 'a', path: '/a.md', name: 'a.md', type: 'file', content_hash: hash })
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); setActivePinia(createPinia()); native.desktop = true
  native.listen.mockImplementation(async (_name, callback) => { event = callback; return native.stop })
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); vi.restoreAllMocks() })
async function start() {
  const workspace = useWorkspaceStore(), editor = useEditorStore()
  workspace.hasVault = true; workspace.vaultId = 'vault'; workspace.fileTree = [node('old')]
  workspace.activeFilePath = '/a.md'; editor.currentFilePath = '/a.md'
  const refresh = vi.spyOn(workspace, 'refreshFileTree').mockResolvedValue()
  const check = vi.spyOn(editor, 'checkExternalFile').mockResolvedValue(true)
  const missing = vi.spyOn(editor, 'setExternalChanged').mockImplementation(() => undefined)
  wrapper = mount(defineComponent({ setup() { useWorkspaceRefresh(); return () => null } }))
  await flushPromises()
  return { workspace, editor, refresh, check, missing }
}

it('does not poll every two seconds or reread the active body when idle', async () => {
  const { refresh, check } = await start()
  await vi.advanceTimersByTimeAsync(10000)
  expect(refresh).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(20000)
  expect(refresh).toHaveBeenCalledTimes(2)
  expect(refresh).toHaveBeenLastCalledWith(false)
  expect(check).not.toHaveBeenCalled()
})
it('ignores other vault events and rereads only changed active content', async () => {
  const { workspace, refresh, check, missing } = await start()
  event({ payload: { vault_id: 'other', revision: 1, paths: ['a.md'] } }); await flushPromises()
  expect(refresh).toHaveBeenCalledTimes(1)
  event({ payload: { vault_id: 'vault', revision: 2, paths: ['unrelated.md'] } }); await flushPromises()
  expect(check).not.toHaveBeenCalled()
  refresh.mockImplementationOnce(async () => { workspace.fileTree = [node('new')] })
  event({ payload: { vault_id: 'vault', revision: 3, paths: ['a.md'] } }); await flushPromises()
  expect(check).toHaveBeenCalledOnce()
  refresh.mockImplementationOnce(async () => { workspace.fileTree = [] })
  event({ payload: { vault_id: 'vault', revision: 4, paths: ['a.md'] } }); await flushPromises()
  expect(missing).toHaveBeenCalledOnce()
})
it('queues one follow-up refresh and preserves focus rescan while an event is running', async () => {
  const { refresh } = await start()
  let release!: () => void
  refresh.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  event({ payload: { vault_id: 'vault', revision: 2, paths: ['a.md'] } })
  for (let index = 0; index < 10; index++) event({ payload: { vault_id: 'vault', revision: index + 3, paths: ['a.md'] } })
  window.dispatchEvent(new Event('focus'))
  expect(refresh).toHaveBeenCalledTimes(2)
  release(); await flushPromises()
  expect(refresh).toHaveBeenCalledTimes(3)
  expect(refresh).toHaveBeenLastCalledWith(true)
})
it('does not let a previous vault request inspect the next editor', async () => {
  const { workspace, refresh, check, missing } = await start()
  let release!: () => void
  refresh.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  event({ payload: { vault_id: 'vault', revision: 2, paths: ['a.md'] } })
  workspace.vaultId = 'next'; workspace.fileTree = []
  release(); await flushPromises()
  expect(check).not.toHaveBeenCalled(); expect(missing).not.toHaveBeenCalled()
})
it('unsubscribes even if listener registration finishes after unmount', async () => {
  let release!: (stop: () => void) => void
  native.listen.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  await start(); wrapper!.unmount(); wrapper = undefined
  release(native.stop); await flushPromises()
  expect(native.stop).toHaveBeenCalledOnce()
})
it('keeps a visible-only five-second Web fallback without a native subscription', async () => {
  native.desktop = false
  const { refresh } = await start()
  expect(native.listen).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(5000)
  expect(refresh).toHaveBeenCalledTimes(2)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  await vi.advanceTimersByTimeAsync(10000)
  expect(refresh).toHaveBeenCalledTimes(2)
})

it('retries a failed body read even after the tree has accepted the new hash', async () => {
  const { workspace, editor, refresh, check } = await start()
  check.mockRestore()
  editor.closeFile()
  vi.spyOn(service, 'getNoteId').mockResolvedValue('a')
  const read = vi.spyOn(service, 'readFileContent').mockResolvedValue('original')
  await editor.loadFile('/a.md')
  refresh.mockImplementationOnce(async () => { workspace.fileTree = [node('new')] })
  read.mockRejectedValueOnce(new Error('temporary file lock'))
  window.dispatchEvent(new Event('focus')); await flushPromises()
  expect(editor.content).toBe('original')
  expect(editor.externalReadError).toBe(true)
  read.mockResolvedValue('external update')
  window.dispatchEvent(new Event('focus')); await flushPromises()
  expect(editor.content).toBe('external update')
  expect(editor.externalReadError).toBe(false)
  expect(read).toHaveBeenCalledTimes(3)
  window.dispatchEvent(new Event('focus')); await flushPromises()
  expect(read).toHaveBeenCalledTimes(3)
})

it('retries a body check skipped while saving without replacing newer unsaved input', async () => {
  const { workspace, editor, refresh, check } = await start()
  check.mockRestore(); editor.closeFile()
  vi.spyOn(service, 'getNoteId').mockResolvedValue('a')
  const read = vi.spyOn(service, 'readFileContent').mockResolvedValue('original')
  await editor.loadFile('/a.md')
  let release!: () => void
  vi.spyOn(service, 'saveFileContent').mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve }))
  editor.updateContent('saved input')
  const saving = editor.save()
  refresh.mockImplementationOnce(async () => { workspace.fileTree = [node('new')] })
  window.dispatchEvent(new Event('focus')); await flushPromises()
  expect(read).toHaveBeenCalledTimes(1)
  release(); await saving
  editor.updateContent('new unsaved input'); editor.cancelPendingAutoSave()
  read.mockResolvedValue('external update')
  window.dispatchEvent(new Event('focus')); await flushPromises()
  expect(read).toHaveBeenCalledTimes(2)
  expect(editor.content).toBe('new unsaved input')
  expect(editor.saveStatus).toBe('conflict')
})
