// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import FileTreePanel from './FileTreePanel.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'

let wrapper: VueWrapper | null = null

async function waitForPath(path: string) {
  const editorStore = useEditorStore()
  for (let attempt = 0; attempt < 100; attempt++) {
    if (editorStore.currentFilePath === path) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error(`Editor did not open ${path}`)
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('FileTreePanel file switching', () => {
  it('switches both workspace selection and editor content on consecutive clicks', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/workspace', component: { template: '<div />' } }],
    })
    await router.push('/workspace')
    await router.isReady()

    const workspaceStore = useWorkspaceStore()
    const editorStore = useEditorStore()
    await workspaceStore.openVault('/mock-vault')
    wrapper = mount(FileTreePanel, { attachTo: document.body, global: { plugins: [router] } })

    const findNode = (name: string) => wrapper!.findAll('.tree-node').find((node) => node.text().includes(name))!
    await findNode('红黑树.md').trigger('click')
    await waitForPath('/数据结构/红黑树.md')
    expect(workspaceStore.activeFilePath).toBe('/数据结构/红黑树.md')
    expect(editorStore.content).toContain('# 红黑树')

    await findNode('二叉搜索树.md').trigger('click')
    await waitForPath('/数据结构/二叉搜索树.md')
    expect(workspaceStore.activeFilePath).toBe('/数据结构/二叉搜索树.md')
    expect(editorStore.content).toContain('# 二叉搜索树')
  })
})
