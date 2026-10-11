// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { useEditorStore } from './editor'
import * as service from '@/services/workspaceService'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}

afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

it('keeps the current text until the newest confirmed external reload completes', async () => {
  setActivePinia(createPinia())
  const read = vi.spyOn(service, 'readFileContent').mockResolvedValue('original')
  vi.spyOn(service, 'getNoteId').mockResolvedValue('id')
  const editor = useEditorStore()
  await editor.loadFile('/a.md')
  editor.setExternalChanged()
  const older = deferred<string>(), newer = deferred<string>()
  read.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
  const first = editor.reloadExternalFile(), retry = editor.reloadExternalFile()

  older.resolve('earlier disk revision'); await first
  expect([editor.content, editor.saveStatus]).toEqual(['original', 'external_changed'])
  newer.resolve('latest disk revision'); await retry
  expect([editor.content, editor.saveStatus]).toEqual(['latest disk revision', 'saved'])
  await flushPromises()
})
