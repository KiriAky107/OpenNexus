// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceStore } from './workspace'

afterEach(() => localStorage.clear())

it('keeps recent document extensions and updates paths after rename or removal', () => {
  setActivePinia(createPinia())
  const workspace = useWorkspaceStore()
  workspace.vaultId = 'vault-a'
  workspace.fileTree = [{ id: 'folder', name: 'course', path: '/course', type: 'folder', children: [
    { id: 'note-id', name: 'note.md', path: '/course/note.md', type: 'file' },
    { id: 'canvas-id', name: 'map.canvas', path: '/course/map.canvas', type: 'file' },
    { id: 'image-id', name: 'chart.png', path: '/course/chart.png', type: 'file' },
  ] }]
  workspace.openFile('/course/note.md')
  workspace.openFile('/course/map.canvas')
  workspace.rememberRecentFile('/course/chart.png')
  expect(workspace.recentFiles).toEqual(['/course/chart.png', '/course/map.canvas', '/course/note.md'])
  workspace.renamePath('/course', '/lesson', 'lesson')
  expect(workspace.recentFiles).toEqual(['/lesson/chart.png', '/lesson/map.canvas', '/lesson/note.md'])
  workspace.closePath('/lesson/chart.png')
  expect(workspace.recentFiles).toEqual(['/lesson/map.canvas', '/lesson/note.md'])
  expect(JSON.parse(localStorage.getItem('workspace-recent-files:vault-a')!)).toEqual(workspace.recentFiles)
})
