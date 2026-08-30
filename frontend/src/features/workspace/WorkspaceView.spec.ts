// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WorkspaceView from './WorkspaceView.vue'
import { useWorkspaceStore } from '@/stores/workspace'

let wrapper: VueWrapper | null = null

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('WorkspaceView initial file', () => {
  it('does not overwrite a file selected while the welcome note is loading', async () => {
    const workspaceStore = useWorkspaceStore()
    wrapper = mount(WorkspaceView, {
      global: { stubs: { EditorHeader: true, EditorPane: true } },
    })

    workspaceStore.openFile('/数据结构/红黑树.md')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(workspaceStore.activeFilePath).toBe('/数据结构/红黑树.md')
  })
})
