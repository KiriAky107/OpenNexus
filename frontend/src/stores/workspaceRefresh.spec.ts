// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceStore } from './workspace'
import * as service from '@/services/workspaceService'
beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })
it('fetches external entries while keeping folder state and ignoring stale responses', async () => {
  const store = useWorkspaceStore()
  store.hasVault = true; store.vaultPath = '/vault'
  store.fileTree = [{ id: 'folder', path: '/folder', name: 'folder', type: 'folder', is_open: true, children: [] }]
  let release!: (value: typeof store.fileTree) => void
  vi.spyOn(service, 'refreshTree').mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    .mockResolvedValueOnce([{ id: 'folder', path: '/folder', name: 'folder', type: 'folder', children: [{ id: 'new', path: '/folder/new.md', name: 'new.md', type: 'file' }] }])
  const old = store.refreshFileTree()
  await store.refreshFileTree()
  expect(store.fileTree[0]!.is_open).toBe(true)
  expect(store.fileTree[0]!.children).toHaveLength(1)
  release([]); await old
  expect(store.fileTree).toHaveLength(1)
})
it('clears document tabs only after another vault opens successfully', async () => {
  const store = useWorkspaceStore()
  store.openFile('/old.md')
  vi.spyOn(service, 'openVault').mockResolvedValue({ vault_id: 'new-vault', path: 'D:/notes', name: 'notes' })
  vi.spyOn(service, 'getFileTree').mockResolvedValue([{ id: 'new', path: '/new.md', name: 'new.md', type: 'file' }])

  await store.openVault('D:/notes')

  expect(store.openFiles).toEqual([])
  expect(store.activeFilePath).toBeNull()
  expect(store.fileTree.map(item => item.path)).toEqual(['/new.md'])
})
