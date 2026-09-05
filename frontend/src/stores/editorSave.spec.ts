// @vitest-environment happy-dom
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { useEditorStore } from './editor'
import * as workspace from '@/services/workspaceService'

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

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
  expect(write).toHaveBeenLastCalledWith('/draft.md', 'latest')
  expect(store.saveStatus).toBe('saved')
})
