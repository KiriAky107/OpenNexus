// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import * as service from '@/services/workspaceService'
import { navigateMarkdownHref } from '@/services/markdownLinkService'
import EditorHeader from '@/features/editor/EditorHeader.vue'
import { navigateToCitation } from './useCitationNavigation'
import { useWorkspaceRefresh } from './useWorkspaceRefresh'
import type { FileNode } from '@/contracts'

vi.mock('@/router', () => ({ default: { push: vi.fn().mockResolvedValue(undefined) } }))
vi.mock('@/services/platform/desktop', async original => ({ ...await original<object>(), isDesktop: () => false }))

let wrapper: ReturnType<typeof mount> | undefined
const path = '/课程/笔记.md'
const tree = (): FileNode[] => [{ id: 'folder', name: '课程', path: '/课程', type: 'folder', children: [
  { id: 'note', name: '笔记.md', path, type: 'file', content_hash: 'unchanged' },
] }]

beforeEach(() => {
  vi.useFakeTimers(); setActivePinia(createPinia())
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.spyOn(service, 'getNoteId').mockResolvedValue('note')
  vi.spyOn(service, 'readFileContent').mockResolvedValue('# 结论\n\n正文')
  vi.spyOn(service, 'refreshTree').mockImplementation(async () => tree())
  const workspace = useWorkspaceStore()
  workspace.hasVault = true; workspace.vaultId = 'vault'; workspace.fileTree = tree()
})
afterEach(() => {
  wrapper?.unmount(); wrapper = undefined
  vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear()
})

it.each(['课程/笔记.md', '课程\\笔记.md', '/课程/笔记.md'])('keeps citation %s and in-note links attached to the tree through refresh', async filePath => {
  const editor = useEditorStore(), workspace = useWorkspaceStore()
  await navigateToCitation({ file_path: filePath, block_id: 'block-1' }, {
    loadFile: editor.loadFile, openFile: workspace.openFile,
    highlightBlock: editor.highlightBlock, navigate: vi.fn(),
  })
  await navigateMarkdownHref('#结论')
  wrapper = mount(defineComponent({ setup() { useWorkspaceRefresh(); return () => h(EditorHeader) } }))
  await flushPromises()

  expect(wrapper.text()).not.toContain('原文件已删除或移动')
  expect(editor.saveStatus).toBe('saved')
  expect(editor.currentFilePath).toBe(path)
  expect(workspace.activeFilePath).toBe(path)
  expect(workspace.activeFile?.id).toBe('note')
  expect(workspace.openFiles).toEqual([path])
  expect(editor.headingRequest).toMatchObject({ path, index: 0 })
  expect(service.readFileContent).toHaveBeenCalledWith(path)

  // Opening the same file from the tree must not reload or duplicate its tab.
  await editor.loadFile(path); workspace.openFile(path)
  expect(service.readFileContent).toHaveBeenCalledTimes(1)
  expect(workspace.openFiles).toEqual([path])

  // Real removal must still preserve edits and offer recovery.
  editor.updateContent('保留未保存内容')
  vi.mocked(service.refreshTree).mockResolvedValue([])
  window.dispatchEvent(new Event('focus')); await flushPromises()
  expect(wrapper.text()).toContain('原文件已删除或移动')
  expect(editor.saveStatus).toBe('conflict')
  expect(editor.content).toBe('保留未保存内容')
})

it('opens an encoded relative Markdown link under the same tree identity', async () => {
  const editor = useEditorStore(), workspace = useWorkspaceStore()
  await navigateMarkdownHref('./%E7%AC%94%E8%AE%B0.md#结论', '/课程/入口.md')
  wrapper = mount(defineComponent({ setup() { useWorkspaceRefresh(); return () => h(EditorHeader) } }))
  await flushPromises()
  expect(editor.currentFilePath).toBe(path)
  expect(workspace.activeFile?.id).toBe('note')
  expect(editor.saveStatus).toBe('saved')
  expect(wrapper.text()).not.toContain('原文件已删除或移动')
})
