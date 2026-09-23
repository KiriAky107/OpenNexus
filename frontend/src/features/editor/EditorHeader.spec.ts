// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import EditorHeader from './EditorHeader.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import * as service from '@/services/workspaceService'
afterEach(() => vi.restoreAllMocks())
it('restores the hidden toolbar without switching mode or changing note content', async () => {
 const pinia = createPinia(); setActivePinia(pinia)
 const layout = useLayoutPreferencesStore(); layout.editorToolbarVisible = false
 const editor = useEditorStore(); editor.mode = 'wysiwyg'; editor.content = '# Keep this'
 const wrapper = mount(EditorHeader, { global: { plugins: [pinia] } })
 try {
  await wrapper.get('[aria-label="显示编辑器工具栏"]').trigger('click')
  expect(layout.editorToolbarVisible).toBe(true)
  expect(editor.content).toBe('# Keep this')
  expect(editor.mode).toBe('wysiwyg')
 } finally { wrapper.unmount(); localStorage.clear() }
})
it('offers recovery for a missing file, supports cancel, and releases navigation after confirmation', async () => {
 const pinia = createPinia(); setActivePinia(pinia)
 vi.spyOn(service, 'getNoteId').mockResolvedValue('id')
 const read = vi.spyOn(service, 'readFileContent').mockResolvedValue('original')
 const editor = useEditorStore(), workspace = useWorkspaceStore()
 await editor.loadFile('/removed.md'); workspace.openFile('/removed.md')
 editor.updateContent('unsaved'); editor.setExternalChanged()
 const wrapper = mount(EditorHeader, { global: { plugins: [pinia], stubs: { ActionDialog: { name: 'ActionDialog', template: '<div />', props: ['message'], emits: ['resolve'] } } } })
 const close = () => wrapper.findAll('button').find(button => button.text() === '关闭当前笔记')!
 try {
  expect(wrapper.text()).toContain('原文件已删除或移动')
  expect(wrapper.text()).not.toContain('重新加载外部版本')
  expect(wrapper.text()).toContain('下载 Markdown 副本')
  await close().trigger('click')
  wrapper.findComponent({ name: 'ActionDialog' }).vm.$emit('resolve', null); await flushPromises()
  expect(editor.content).toBe('unsaved')
  await close().trigger('click')
  wrapper.findComponent({ name: 'ActionDialog' }).vm.$emit('resolve', ''); await flushPromises()
  expect(editor.currentFilePath).toBeNull(); expect(workspace.activeFilePath).toBeNull()
  read.mockResolvedValue('another file')
  await editor.loadFile('/other.md')
  expect(editor.content).toBe('another file')
 } finally { wrapper.unmount() }
})
it('does not discard text changed after confirmation was opened', async () => {
 setActivePinia(createPinia())
 const editor = useEditorStore()
 editor.currentFilePath = '/removed.md'; editor.updateContent('before'); editor.setExternalChanged()
 editor.updateContent('after')
 expect(await editor.discardExternalChanges('/removed.md', 'before')).toBe(false)
 expect(editor.content).toBe('after')
})
