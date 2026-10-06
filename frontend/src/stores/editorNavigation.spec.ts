// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { useEditorStore } from './editor'
import { useWorkspaceStore } from './workspace'
import * as service from '@/services/workspaceService'

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
beforeEach(async () => {
  vi.useFakeTimers(); setActivePinia(createPinia())
  vi.spyOn(service, 'readFileContent').mockImplementation(async path => `body:${path}`)
  vi.spyOn(service, 'getNoteId').mockImplementation(async path => path)
  vi.spyOn(service, 'saveFileContent').mockResolvedValue()
  await useEditorStore().loadFile('/a.md')
})
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear() })

it('saves text typed during a slow read before replacing the document and tab', async () => {
  const editor = useEditorStore(), workspace = useWorkspaceStore(), read = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const opening = editor.loadFile('/b.md'); await flushPromises()
  expect(workspace.activeFilePath).toBe('/a.md')
  editor.updateContent('new text in A'); editor.scheduleAutoSave()
  read.resolve('body B'); await opening
  expect(service.saveFileContent).toHaveBeenCalledExactlyOnceWith('/a.md', 'new text in A', 'body:/a.md')
  expect([editor.currentFilePath, workspace.activeFilePath, editor.content]).toEqual(['/b.md', '/b.md', 'body B'])
  await vi.advanceTimersByTimeAsync(1600)
  expect(service.saveFileContent).toHaveBeenCalledTimes(1)
})

it('drains edits made during an outstanding save without moving them to the new file', async () => {
  const editor = useEditorStore(), save = deferred<void>()
  vi.mocked(service.saveFileContent).mockReturnValueOnce(save.promise)
  editor.updateContent('first edit')
  const opening = editor.loadFile('/b.md'); await flushPromises()
  editor.updateContent('second edit'); save.resolve(); await opening
  expect(vi.mocked(service.saveFileContent).mock.calls).toEqual([
    ['/a.md', 'first edit', 'body:/a.md'], ['/a.md', 'second edit', 'first edit'],
  ])
  expect(editor.content).toBe('body:/b.md')
})

it('commits only the newest read and does not create a tab for the superseded target', async () => {
  const editor = useEditorStore(), workspace = useWorkspaceStore(), b = deferred<string>(), c = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(b.promise).mockReturnValueOnce(c.promise)
  const first = editor.loadFile('/b.md'); await flushPromises()
  const second = editor.loadFile('/c.md'); await flushPromises()
  c.resolve('C'); expect((await second)?.isCurrent()).toBe(true)
  b.resolve('B'); expect(await first).toBeNull()
  expect([workspace.activeFilePath, editor.currentFilePath, editor.content]).toEqual(['/c.md', '/c.md', 'C'])
  expect(workspace.openFiles).toEqual(['/a.md', '/c.md'])
})

it('clicking the current file cancels a pending read without reloading or losing edits', async () => {
  const editor = useEditorStore(), workspace = useWorkspaceStore(), read = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const opening = editor.loadFile('/b.md'); await flushPromises()
  editor.updateContent('keep A')
  expect((await editor.loadFile('/a.md'))?.isCurrent()).toBe(true)
  read.resolve('B'); expect(await opening).toBeNull()
  expect([editor.currentFilePath, workspace.activeFilePath, editor.content, editor.saveStatus]).toEqual(['/a.md', '/a.md', 'keep A', 'dirty'])
})

it.each(['close', 'folder', 'vault'] as const)('does not reopen a document after %s changes the navigation scope', async action => {
  const editor = useEditorStore(), workspace = useWorkspaceStore(), read = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const opening = editor.loadFile('/b.md'); await flushPromises()
  if (action === 'close') { editor.closeFile(); workspace.setActiveFile(null) }
  if (action === 'folder') workspace.selectFolder('/folder')
  if (action === 'vault') workspace.vaultId = 'another-vault'
  read.resolve('late B'); expect(await opening).toBeNull()
  expect(editor.content).not.toBe('late B')
  expect(workspace.openFiles).not.toContain('/b.md')
})

it('keeps the old content and tab when the final save fails', async () => {
  const editor = useEditorStore(), workspace = useWorkspaceStore(), read = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const opening = editor.loadFile('/b.md'); await flushPromises()
  editor.updateContent('must keep'); vi.mocked(service.saveFileContent).mockRejectedValueOnce(new Error('disk full'))
  read.resolve('B'); await expect(opening).rejects.toThrow('已阻止切换')
  expect([editor.currentFilePath, workspace.activeFilePath, editor.content, editor.saveStatus]).toEqual(['/a.md', '/a.md', 'must keep', 'save_failed'])
})

it('a read failure cannot reset edits made while it was pending to saved', async () => {
  const editor = useEditorStore(), read = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const opening = editor.loadFile('/b.md'); await flushPromises()
  editor.updateContent('keep dirty'); read.reject(new Error('missing'))
  await expect(opening).rejects.toThrow('missing')
  expect(editor.content).toBe('keep dirty'); expect(editor.saveStatus).toBe('dirty')
  expect(editor.loadingFilePath).toBeNull()
})

it('does not let an older request waiting for save supersede a newer request', async () => {
  const editor = useEditorStore(), save = deferred<void>()
  editor.updateContent('A edit'); vi.mocked(service.saveFileContent).mockReturnValueOnce(save.promise)
  const first = editor.loadFile('/b.md'); await flushPromises()
  const second = editor.loadFile('/c.md'); save.resolve()
  expect(await first).toBeNull(); expect((await second)?.isCurrent()).toBe(true)
  expect(service.readFileContent).not.toHaveBeenCalledWith('/b.md')
  expect(editor.currentFilePath).toBe('/c.md')
})

it('invalidates continuation receipts as soon as another navigation begins', async () => {
  const editor = useEditorStore(), receipt = await editor.loadFile('/b.md'), read = deferred<string>()
  expect(receipt?.isCurrent()).toBe(true)
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const opening = editor.loadFile('/c.md')
  expect(receipt?.isCurrent()).toBe(false)
  read.resolve('C'); await opening
})

it('invalidates old view callbacks even when the same path is reopened', async () => {
  const editor = useEditorStore(), current = editor.captureDocument()
  editor.updateContent('same document'); expect(current()).toBe(true)
  await editor.loadFile('/b.md'); await editor.loadFile('/a.md')
  expect(current()).toBe(false)
})

it('ignores an external read from an earlier opening of the same path', async () => {
  const editor = useEditorStore(), read = deferred<string>()
  vi.mocked(service.readFileContent).mockReturnValueOnce(read.promise)
  const checking = editor.checkExternalFile()
  await editor.loadFile('/b.md'); await editor.loadFile('/a.md')
  read.resolve('outdated A'); await checking
  expect(editor.content).toBe('body:/a.md'); expect(editor.saveStatus).toBe('saved')
})

it('preserves a rename racing an outstanding save as a recoverable conflict', async () => {
  const editor = useEditorStore(), workspace = useWorkspaceStore(), write = deferred<void>()
  editor.updateContent('keep moved edit')
  vi.mocked(service.saveFileContent).mockReturnValueOnce(write.promise)
  const saving = editor.save()
  workspace.renamePath('/a.md', '/moved.md', 'moved.md')
  editor.renameFilePath('/a.md', '/moved.md')
  write.resolve(); await saving
  expect(editor.saveStatus).toBe('conflict')
  await expect(editor.loadFile('/b.md')).rejects.toThrow('已阻止切换')
  expect(editor.currentFilePath).toBe('/moved.md')
  expect(editor.content).toBe('keep moved edit')
})

it('reloads a clean renamed note after the watcher reported its old path missing', async () => {
  const editor = useEditorStore()
  editor.setExternalChanged()
  editor.externalReadError = true
  editor.renameFilePath('/a.md', '/moved.md')
  vi.mocked(service.readFileContent).mockResolvedValueOnce('reviewed moved references')
  expect(await editor.checkExternalFile()).toBe(true)
  expect(editor.currentFilePath).toBe('/moved.md')
  expect(editor.content).toBe('reviewed moved references')
  expect(editor.saveStatus).toBe('saved')
  expect(editor.externalReadError).toBe(false)
  expect((await editor.loadFile('/b.md'))?.isCurrent()).toBe(true)
})

it('moves open tabs even if the watcher already replaced the old tree entry', () => {
  const workspace = useWorkspaceStore()
  workspace.fileTree = [{ id: 'moved', path: '/moved.md', name: 'moved.md', type: 'file' }]
  workspace.renamePath('/a.md', '/moved.md', 'moved.md')
  expect(workspace.activeFilePath).toBe('/moved.md')
  expect(workspace.openFiles).toContain('/moved.md')
  expect(workspace.openFiles).not.toContain('/a.md')
})

it('fails recoverably for an orphaned saving state instead of looping', async () => {
  const editor = useEditorStore()
  editor.saveStatus = 'saving'
  await expect(editor.loadFile('/b.md')).rejects.toThrow('已阻止切换')
  expect(editor.saveStatus).toBe('conflict')
  expect(editor.currentFilePath).toBe('/a.md')
})
