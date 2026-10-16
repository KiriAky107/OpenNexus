// @vitest-environment happy-dom
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
vi.hoisted(() => Object.defineProperty(document, 'compatMode', { value: 'CSS1Compat', configurable: true }))
import { mount, type VueWrapper } from '@vue/test-utils'
import { editorViewCtx, type Editor } from '@milkdown/kit/core'
import { getMarkdown } from '@milkdown/kit/utils'
import { TextSelection, NodeSelection } from '@milkdown/kit/prose/state'
import { EditorView as CodeMirror } from '@codemirror/view'
import { appLocale } from '../../src/i18n'
import MobileRichEditor from './MobileRichEditor.vue'

const native = vi.hoisted(() => ({ mobileRequest: vi.fn() }))
vi.mock('./native', () => native)
vi.mock('../../src/features/editor/codeHighlightClient', async () => {
  const { computeHighlight } = await import('../../src/features/editor/codeHighlightProcessor')
  return { codeHighlights: { request: (_owner: object, input: Parameters<typeof computeHighlight>[0], done: (value: unknown) => void) => { void computeHighlight(input).then(done) }, cancel: () => {} } }
})
vi.mock('../../src/services/previewHighlighter', async () => {
  const { computePreviewHighlight } = await import('../../src/services/previewHighlightProcessor')
  return { previewHighlighter: { highlight: computePreviewHighlight, cached: () => undefined } }
})
let wrapper: VueWrapper<InstanceType<typeof MobileRichEditor>> | undefined
beforeEach(() => { appLocale.value = 'en'; native.mobileRequest.mockReset() })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks() })
afterAll(async () => { await new Promise(resolve => setTimeout(resolve, 3100)) })
async function open(source: string) {
  wrapper = mount(MobileRichEditor, { props: { modelValue: source, path: 'notes/original.md', label: 'Note body', language: 'en', theme: 'light' }, attachTo: document.body })
  await vi.waitFor(() => expect(wrapper!.findComponent({ name: 'RichMarkdownEditor' }).emitted('ready')).toHaveLength(1))
  return wrapper.vm.getEditor() as Editor
}
it('collapses phone formatting by default and formats the original selection without replacing the editor or undo history', async () => {
  const source = 'original 😀\r\n', editor = await open(source)
  const toggle = wrapper!.get('.formatting-toggle'), toolbar = wrapper!.get('.markdown-toolbar')
  expect(toggle.attributes('aria-expanded')).toBe('false')
  expect(toolbar.isVisible()).toBe(false)
  expect(toggle.attributes('aria-controls')).toBe(toolbar.attributes('id'))
  editor.action(ctx => { const view = ctx.get(editorViewCtx); view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 1, 9))) })
  const selected = editor.action(ctx => ctx.get(editorViewCtx).state.selection.toJSON())
  await toggle.trigger('pointerdown'); await toggle.trigger('click')
  expect(toolbar.isVisible()).toBe(true)
  expect(editor.action(ctx => ctx.get(editorViewCtx).state.selection.toJSON())).toEqual(selected)
  expect(wrapper!.emitted('update:modelValue')).toBeUndefined()
  await toolbar.get('[aria-label="Bold"]').trigger('click', { detail: 0 })
  await vi.waitFor(() => expect(wrapper!.vm.source()).toContain('**original**'))
  expect(wrapper!.vm.handleBack()).toBe(true)
  await wrapper!.vm.$nextTick()
  expect(toolbar.isVisible()).toBe(false)
  expect(wrapper!.vm.handleBack()).toBe(false)
  await toggle.trigger('click')
  expect(wrapper!.vm.getEditor()).toBe(editor)
  expect(wrapper!.vm.undo()).toBe(true)
  await vi.waitFor(() => expect(wrapper!.vm.source()).toBe(source))
  expect(native.mobileRequest).not.toHaveBeenCalled()
})
it('dismisses floating tools on outside touch, Escape or read-only review without changing original bytes', async () => {
  const source = '# 原文 😀\r\n', editor = await open(source), toggle = wrapper!.get('.formatting-toggle')
  await toggle.trigger('click')
  await wrapper!.get('.ProseMirror').trigger('pointerdown')
  expect(wrapper!.get('.markdown-toolbar').isVisible()).toBe(false)
  await toggle.trigger('click')
  await wrapper!.get('.markdown-toolbar').trigger('keydown', { key: 'Escape', isComposing: true })
  expect(wrapper!.get('.markdown-toolbar').isVisible()).toBe(true)
  await wrapper!.get('.markdown-toolbar').trigger('keydown', { key: 'Escape' })
  expect(wrapper!.get('.markdown-toolbar').isVisible()).toBe(false)
  expect(document.activeElement).toBe(toggle.element)
  await toggle.trigger('click')
  await wrapper!.setProps({ readOnly: true })
  expect(wrapper!.get('.markdown-toolbar').isVisible()).toBe(false)
  expect((toggle.element as HTMLButtonElement).disabled).toBe(true)
  expect(wrapper!.vm.getEditor()).toBe(editor)
  expect(wrapper!.vm.source()).toBe(source)
  expect(wrapper!.emitted('update:modelValue')).toBeUndefined()
  expect(native.mobileRequest).not.toHaveBeenCalled()
})
it('keeps original metadata and mixed source on mount and restores those exact bytes when a real rich-text edit is undone', async () => {
  const source = '---\r\ntitle: 中文\r\ntags: [keep]\r\n---\r\n**原文 😀**\n'
  const editor = await open(source)
  expect(wrapper!.emitted('update:modelValue')).toBeUndefined()
  editor.action(ctx => { const view = ctx.get(editorViewCtx); view.dispatch(view.state.tr.insertText(' more', view.state.doc.content.size - 1)) })
  await vi.waitFor(() => expect(wrapper!.emitted('update:modelValue')).toHaveLength(1))
  expect(wrapper!.vm.source()).toContain('---\r\ntitle: 中文\r\ntags: [keep]\r\n---\r\n')
  expect(wrapper!.vm.undo()).toBe(true)
  await vi.waitFor(() => expect(wrapper!.vm.source()).toBe(source))
  expect(native.mobileRequest).not.toHaveBeenCalled()
})
it('keeps the same document, selection and undo history through language, theme and read-only changes', async () => {
  const source = 'original'
  const editor = await open(source)
  editor.action(ctx => { const view = ctx.get(editorViewCtx); const tr=view.state.tr.insertText(' edit',9);view.dispatch(tr.setSelection(TextSelection.create(tr.doc,3))) })
  await vi.waitFor(() => expect(wrapper!.vm.source()).toContain('edit'))
  const before = editor.action(ctx => ctx.get(editorViewCtx).state.selection.toJSON())
  await wrapper!.setProps({ theme: 'dark', language: 'zh-CN', readOnly: true }); appLocale.value = 'zh-CN'
  expect(wrapper!.vm.getEditor()).toBe(editor)
  expect(editor.action(ctx => ctx.get(editorViewCtx).state.selection.toJSON())).toEqual(before)
  expect(wrapper!.get('.ProseMirror').attributes('contenteditable')).toBe('false')
  expect(wrapper!.vm.selection()).toBeNull()
  await wrapper!.setProps({ readOnly: false })
  expect(wrapper!.vm.undo()).toBe(true)
  await vi.waitFor(() => expect(wrapper!.vm.source()).toBe(source))
})
it('freezes an actual parsed image candidate without editing, accepts only its original native result and undoes to original bytes', async () => {
  const source = '**原文 😀**\r\n'
  const editor = await open(source)
  editor.action(ctx => { const view = ctx.get(editorViewCtx); view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 3))) })
  const selected = wrapper!.vm.selection()!
  expect(selected.template).toContain(`![](opennexus-rich-image-${selected.marker})`)
  expect(wrapper!.vm.source()).toBe(source)
  expect(wrapper!.emitted('update:modelValue')).toBeUndefined()
  const after = selected.template.replace(`![](opennexus-rich-image-${selected.marker})`, '![](../attachments/original.png)')
  native.mobileRequest.mockResolvedValue({ content_base64: 'iVBORw0KGgo=' })
  expect(wrapper!.vm.acceptImage('foreign', after)).toBe(false)
  expect(wrapper!.vm.acceptImage(source, after)).toBe(true)
  expect(wrapper!.vm.source()).toBe(after)
  expect(wrapper!.vm.undo()).toBe(true)
  await vi.waitFor(() => expect(wrapper!.vm.source()).toBe(source))
})
it('never loads remote Markdown or HTML images through the desktop broker and refuses a foreign model update', async () => {
  const source = '![](https://example.invalid/private.png)\n\n<div><img src="https://example.invalid/html.png"></div>\n'
  const editor = await open(source), original = editor.action(getMarkdown())
  expect(wrapper!.findAll('img[src^="https:"]')).toHaveLength(0)
  expect(native.mobileRequest).not.toHaveBeenCalled()
  await wrapper!.setProps({ modelValue: 'late other file' })
  expect(wrapper!.emitted('error')).toEqual([['MOBILE_EDITOR_CONTENT_CHANGED']])
  expect(editor.action(getMarkdown())).toBe(original)
  expect(wrapper!.vm.source()).toBe(source)
})

it('keeps the actual code-block editor and undo while theme and language change', async () => {
  const source='```javascript\nconst value = 1\n```\n',editor=await open(source)
  editor.action(ctx=>{const view=ctx.get(editorViewCtx);view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc,0)))})
  await vi.waitFor(()=>expect(wrapper!.find('.cm-editor').exists()).toBe(true))
  const block=wrapper!.get('.cm-editor').element as HTMLElement,view=CodeMirror.findFromDOM(block)!
  view.dispatch({changes:{from:view.state.doc.length,insert:'\n// edited'}})
  await vi.waitFor(()=>expect(wrapper!.vm.source()).toContain('// edited'))
  const edited=wrapper!.vm.source();view.dispatch({selection:{anchor:3}})
  await wrapper!.setProps({theme:'dark',language:'zh-CN'})
  expect(wrapper!.vm.getEditor()).toBe(editor)
  expect(CodeMirror.findFromDOM(wrapper!.get('.cm-editor').element as HTMLElement)).toBe(view)
  expect(view.state.selection.main.head).toBe(3);expect(wrapper!.vm.source()).toBe(edited)
  expect(wrapper!.get('.ProseMirror').attributes('lang')).toBe('zh-CN')
})
it('holds composition ownership in the actual shared editor and blocks image capture until it ends', async () => {
  await open('原文')
  await wrapper!.get('.ProseMirror').trigger('compositionstart')
  expect(wrapper!.emitted('compositionStart')).toHaveLength(1)
  expect(wrapper!.vm.selection()).toBeNull()
  expect(wrapper!.vm.undo()).toBe(false)
  await wrapper!.get('.ProseMirror').trigger('compositionend')
  await vi.waitFor(()=>expect(wrapper!.emitted('compositionEnd')).toHaveLength(1))
  expect(wrapper!.emitted('update:modelValue')).toBeUndefined()
})
