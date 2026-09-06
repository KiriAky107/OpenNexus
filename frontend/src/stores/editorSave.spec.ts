// @vitest-environment happy-dom
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { useEditorStore } from './editor'
import * as workspace from '@/services/workspaceService'

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

it('reloads clean external changes but preserves unsaved edits and blocks overwrite', async () => {
  setActivePinia(createPinia())
  vi.spyOn(workspace, 'getNoteId').mockResolvedValue('id')
  const read = vi.spyOn(workspace, 'readFileContent').mockResolvedValue('original')
  const write = vi.spyOn(workspace, 'saveFileContent').mockResolvedValue()
  const store = useEditorStore()
  await store.loadFile('/draft.md')
  read.mockResolvedValue('external')
  await store.checkExternalFile()
  expect(store.content).toBe('external')
  expect(store.contentRevision).toBe(1)
  store.updateContent('my unsaved changes')
  read.mockResolvedValue('new external')
  await store.checkExternalFile()
  expect(store.saveStatus).toBe('conflict')
  expect(store.content).toBe('my unsaved changes')
  store.updateContent('keep editing')
  await store.save()
  expect(write).not.toHaveBeenCalled()
  await store.reloadExternalFile()
  expect(store.content).toBe('new external')
  expect(store.saveStatus).toBe('saved')
})

it('saves text typed while the previous save is still pending', async () => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  const store = useEditorStore()
  store.currentFilePath = '/draft.md'
  let release!: () => void
  const write = vi.spyOn(workspace, 'saveFileContent').mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve })).mockResolvedValue()
  store.updateContent('first')
  const saving = store.save()
  store.updateContent('latest')
  release()
  await saving
  expect(store.saveStatus).toBe('dirty')
  await vi.advanceTimersByTimeAsync(1500)
  expect(write).toHaveBeenLastCalledWith('/draft.md', 'latest', 'first')
  expect(store.saveStatus).toBe('saved')
})
