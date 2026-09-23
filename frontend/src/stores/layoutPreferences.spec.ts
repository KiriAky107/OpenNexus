// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { createPinia } from 'pinia'
import { useLayoutPreferencesStore } from './layoutPreferences'

beforeEach(() => localStorage.clear())
it('persists toolbar visibility, collapsed panel and last tab without losing width', () => {
  const layout = useLayoutPreferencesStore(createPinia())
  expect(layout.editorToolbarVisible).toBe(true)
  layout.workspaceWidth = 336
  layout.editorToolbarVisible = false
  layout.showWorkspacePanel('outline')
  layout.toggleWorkspacePanel('outline')
  const restored = useLayoutPreferencesStore(createPinia())
  expect(restored.workspaceCollapsed).toBe(true)
  expect(restored.workspaceTab).toBe('outline')
  expect(restored.workspaceWidth).toBe(336)
  expect(restored.editorToolbarVisible).toBe(false)
  restored.toggleWorkspacePanel('files')
  expect(restored.workspaceCollapsed).toBe(false)
  expect(restored.workspaceTab).toBe('files')
  expect(restored.editorToolbarVisible).toBe(false)
})
it('uses valid defaults for malformed stored choices', () => {
  localStorage.setItem('workspace-sidebar-tab', 'bad')
  localStorage.setItem('workspace-sidebar-width', '-100')
  const layout = useLayoutPreferencesStore(createPinia())
  expect(layout.workspaceTab).toBe('files')
  expect(layout.workspaceWidth).toBe(272)
})
