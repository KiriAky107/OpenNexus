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
  it('expands every nested folder from the toolbar', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workspace', component: { template: '<div />' } }] })
    await router.push('/workspace')
    const store = useWorkspaceStore()
    store.fileTree = [{ id: 'a', name: 'A', path: '/a', type: 'folder', is_open: false, children: [{ id: 'b', name: 'B', path: '/a/b', type: 'folder', is_open: false }] }]
    wrapper = mount(FileTreePanel, { global: { plugins: [router] } })
    await wrapper.get('[aria-label="全部展开文件夹"]').trigger('click')
    expect(store.fileTree[0]!.is_open).toBe(true)
    expect(store.fileTree[0]!.children![0]!.is_open).toBe(true)
  })
  it('switches full-height panels using tabs and preserves the file search', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workspace', component: { template: '<div />' } }] })
    await router.push('/workspace')
    wrapper = mount(FileTreePanel, { attachTo: document.body, global: { plugins: [router] } })
    expect(wrapper.get('#workspace-files-panel').isVisible()).toBe(true)
    expect(wrapper.get('#workspace-outline-panel').isVisible()).toBe(false)
    await wrapper.get('.file-tree-panel').trigger('wheel', { deltaY: -50 })
    await wrapper.get('.file-search input').setValue('笔记')
    await wrapper.get('#workspace-outline-tab').trigger('click')
    expect(wrapper.get('#workspace-files-panel').isVisible()).toBe(false)
    expect(wrapper.get('#workspace-outline-panel').isVisible()).toBe(true)
    expect(wrapper.get('#workspace-outline-tab').attributes('aria-selected')).toBe('true')
    await wrapper.get('#workspace-outline-tab').trigger('keydown', { key: 'ArrowLeft' })
    expect(wrapper.get('#workspace-files-panel').isVisible()).toBe(true)
    expect((wrapper.get('.file-search input').element as HTMLInputElement).value).toBe('笔记')
  })
  it('reveals search on upward wheel and filters without changing folder state', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workspace', component: { template: '<div />' } }] })
    await router.push('/workspace')
    const store = useWorkspaceStore()
    await store.openVault('C:/vault')
    store.toggleFolder('/数据结构')
    wrapper = mount(FileTreePanel, { global: { plugins: [router] } })
    expect(wrapper.find('.file-search').exists()).toBe(false)
    await wrapper.get('.file-tree-panel').trigger('wheel', { deltaY: -50 })
    await wrapper.get('.file-search input').setValue('红黑')
    expect(wrapper.findAll('.tree-node').map(node => node.text())).toEqual(['数据结构', '红黑树.md'])
    expect(store.fileTree[0]!.is_open).toBe(false)
    await wrapper.get('.file-tree-panel').trigger('wheel', { deltaY: 50 })
    expect(wrapper.find('.file-search').exists()).toBe(true)
  })

  it('creates a folder through the file context menu in its containing directory', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workspace', component: { template: '<div />' } }] })
    await router.push('/workspace')
    await useWorkspaceStore().openVault('C:/vault')
    const create = vi.spyOn(workspaceService, 'createFolder').mockResolvedValue({ id: 'new', name: '子目录', path: '/数据结构/子目录', type: 'folder' })
    wrapper = mount(FileTreePanel, { attachTo: document.body, global: { plugins: [router] } })
    await wrapper.findAll('.tree-node').find(node => node.text().includes('红黑树'))!.trigger('contextmenu')
    const button = [...document.querySelectorAll<HTMLButtonElement>('.context-menu button')].find(item => item.textContent === '新建文件夹')!
    button.click()
    await wrapper.vm.$nextTick()
    await wrapper.get('.new-item input').setValue('子目录')
    await wrapper.get('.new-item').trigger('submit')
    expect(create).toHaveBeenCalledWith('/数据结构', '子目录')
  })

  it('collapses nested headings and requests navigation to a duplicate heading', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workspace', component: { template: '<div />' } }] })
    await router.push('/workspace')
    const store = useEditorStore()
    store.currentFilePath = '/note.md'
    store.content = '# 标题\n\n## 子标题\n\n# 标题\n'
    wrapper = mount(FileTreePanel, { global: { plugins: [router] } })
    await wrapper.get('#workspace-outline-tab').trigger('click')
    expect(wrapper.findAll('.outline-title')).toHaveLength(3)
    await wrapper.get('.outline-row button[aria-expanded]').trigger('click')
    expect(wrapper.findAll('.outline-title')).toHaveLength(2)
    await wrapper.findAll('.outline-title')[1]!.trigger('click')
    expect(store.headingRequest).toEqual({ index: 2, offset: store.content.lastIndexOf('# 标题'), path: '/note.md' })
  })
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
