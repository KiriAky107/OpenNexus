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

describe('WorkspaceView empty state', () => {
  it('does not fabricate a Mock welcome note when no backend file is selected', async () => {
    const workspaceStore = useWorkspaceStore()
    wrapper = mount(WorkspaceView, {
      global: { stubs: { EditorHeader: true, EditorPane: true } },
    })

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(workspaceStore.activeFilePath).toBeNull()
    expect(wrapper.find('.empty-workspace').exists()).toBe(true)
  })
})
