// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import FileTreePanel from './FileTreePanel.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import * as workspaceService from '@/services/workspaceService'

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
  vi.spyOn(workspaceService, 'openVault').mockResolvedValue({ vault_id: 'default', path: 'C:/vault', name: 'vault' })
  vi.spyOn(workspaceService, 'getFileTree').mockResolvedValue([
    {
      id: 'folder-data', name: '数据结构', path: '/数据结构', type: 'folder', is_open: true,
      children: [
        { id: 'note-rbt', note_id: 'note-rbt', name: '红黑树.md', path: '/数据结构/红黑树.md', type: 'file' },
        { id: 'note-bst', note_id: 'note-bst', name: '二叉搜索树.md', path: '/数据结构/二叉搜索树.md', type: 'file' },
      ],
    },
  ])
  vi.spyOn(workspaceService, 'readFileContent').mockImplementation(async (path) =>
    path.includes('红黑树') ? '# 红黑树\n' : '# 二叉搜索树\n'
  )
  vi.spyOn(workspaceService, 'getNoteId').mockImplementation(async (path) =>
    path.includes('红黑树') ? 'note-rbt' : 'note-bst'
  )
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
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
    await workspaceStore.openVault('C:/vault')
    wrapper = mount(FileTreePanel, { attachTo: document.body, global: { plugins: [router] } })

    const findNode = (name: string) => wrapper!.findAll('.tree-node').find((node) => node.text().includes(name))!
    await findNode('红黑树.md').trigger('click')
    await waitForPath('/数据结构/红黑树.md')
    expect(workspaceStore.activeFilePath).toBe('/数据结构/红黑树.md')
    expect(editorStore.content).toContain('# 红黑树')
    expect(editorStore.currentNoteId).toBe('note-rbt')

    await findNode('二叉搜索树.md').trigger('click')
    await waitForPath('/数据结构/二叉搜索树.md')
    expect(workspaceStore.activeFilePath).toBe('/数据结构/二叉搜索树.md')
    expect(editorStore.content).toContain('# 二叉搜索树')
    expect(editorStore.currentNoteId).toBe('note-bst')
  })

  it('creates a Markdown note inside the selected folder', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/workspace', component: { template: '<div />' } }],
    })
    await router.push('/workspace')
    await router.isReady()

    const workspaceStore = useWorkspaceStore()
    await workspaceStore.openVault('C:/vault')
    const createFile = vi.spyOn(workspaceService, 'createFile').mockResolvedValue({
      id: 'note-new', note_id: 'note-new', name: '新笔记.md',
      path: '/数据结构/新笔记.md', type: 'file',
    })
    wrapper = mount(FileTreePanel, { attachTo: document.body, global: { plugins: [router] } })

    await wrapper.findAll('.tree-node').find((node) => node.text().includes('数据结构'))!.trigger('click')
    await wrapper.get('button[aria-label="新建笔记"]').trigger('click')
    await wrapper.get('.new-item input').setValue('新笔记')
    await wrapper.get('.new-item').trigger('submit')
    await waitForPath('/数据结构/新笔记.md')
    await vi.waitFor(() => {
      expect(workspaceStore.activeFilePath).toBe('/数据结构/新笔记.md')
    })

    expect(createFile).toHaveBeenCalledWith('/数据结构', '新笔记.md', '# 新笔记\n\n')
    expect(wrapper.findAll('.tree-node').some((node) => node.classes().includes('active') && node.text().includes('新笔记.md'))).toBe(true)
  })
})
