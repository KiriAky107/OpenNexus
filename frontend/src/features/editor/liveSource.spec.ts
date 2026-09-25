// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getMarkdown } from '@milkdown/kit/utils'
import { editorViewCtx, type Editor } from '@milkdown/kit/core'
import { TextSelection } from '@milkdown/kit/prose/state'
import VisualMarkdownEditor from './VisualMarkdownEditor.vue'
import { beginSourceInsertion, sourceRanges } from './liveSource'

let wrapper: VueWrapper | undefined
beforeEach(() => {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList
  Range.prototype.getBoundingClientRect = () => new DOMRect()
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })
async function setup(source: string) {
  localStorage.clear(); setActivePinia(createPinia())
  wrapper = mount(VisualMarkdownEditor, { props: { initialContent: source }, attachTo: document.body })
  await vi.waitFor(() => expect(wrapper!.find('.ProseMirror p').exists()).toBe(true), { timeout: 5000 })
  return (wrapper.vm as unknown as { getEditor(): Editor }).getEditor()
}

function replaceSource(editor: Editor, value: string) {
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx), point = view.state.selection.$from
    view.dispatch(view.state.tr.insertText(value, point.start(), point.end()))
  })
}

it('edits a link as raw Markdown, saves active input without escaping and parses it on blur', async () => {
  const editor = await setup('[Original](https://example.com/old)\n')
  editor.action(ctx => { const view = ctx.get(editorViewCtx); view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 2))) })
  beginSourceInsertion(editor, 'link')
  await vi.waitFor(() => expect(wrapper!.find('.live-source-editor').exists()).toBe(true))
  replaceSource(editor, '[Updated](https://example.com/new)')
  expect(editor.action(getMarkdown())).toContain('[Updated](https://example.com/new)')
  await wrapper!.get('.ProseMirror').trigger('blur')
  await vi.waitFor(() => expect(wrapper!.find('a[href="https://example.com/new"]').exists()).toBe(true))
  expect(wrapper!.get('a[href="https://example.com/new"]').text()).toBe('Updated')
  expect(wrapper!.find('[role="dialog"]').exists()).toBe(false)
})

it('keeps HTML edits unescaped and restores the original value on Escape', async () => {
  const editor = await setup('Before <strong>bold</strong> after.\n')
  const range = editor.action(ctx => sourceRanges(ctx.get(editorViewCtx).state.doc).sort((a,b) => (b.to-b.from)-(a.to-a.from))[0]!)
  editor.action(ctx => { const view=ctx.get(editorViewCtx); view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, range.from))) })
  beginSourceInsertion(editor, 'html')
  await vi.waitFor(() => expect(wrapper!.find('.live-source-editor').exists()).toBe(true))
  replaceSource(editor, '<em>changed</em>')
  expect(editor.action(getMarkdown())).toContain('<em>changed</em>')
  await wrapper!.get('.ProseMirror').trigger('keydown', { key: 'z', ctrlKey: true })
  expect(wrapper!.get('.live-source-editor').text()).toContain('<strong>bold</strong>')
  await wrapper!.get('.ProseMirror').trigger('keydown', { key: 'z', ctrlKey: true, shiftKey: true })
  expect(wrapper!.get('.live-source-editor').text()).toBe('<em>changed</em>')
  await wrapper!.get('.ProseMirror').trigger('keydown', { key: 'Escape' })
  expect(editor.action(getMarkdown())).toContain('<strong>bold</strong>')
  expect(editor.action(getMarkdown())).not.toContain('changed')
})

it('does not offer source activation inside code blocks or inline code', async () => {
  const editor = await setup('`<strong>literal</strong>` and `[link](https://example.com)`\n')
  expect(editor.action(ctx => sourceRanges(ctx.get(editorViewCtx).state.doc))).toEqual([])
})

it('keeps source inside its paragraph and renders when the caret moves into surrounding text', async () => {
  const editor = await setup('Before [label](https://example.com) after.\n')
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx), range = sourceRanges(view.state.doc)[0]!
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, range.from + 1)))
  })
  beginSourceInsertion(editor, 'link')
  expect(wrapper!.find('textarea').exists()).toBe(false)
  replaceSource(editor, '[updated](https://example.com/new)')
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx)
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 2)))
  })
  expect(editor.action(getMarkdown())).toContain('Before [updated](https://example.com/new) after.')
  expect(wrapper!.find('.live-source-editor').exists()).toBe(false)
  expect(wrapper!.findAll('.ProseMirror > p')).toHaveLength(1)
})
