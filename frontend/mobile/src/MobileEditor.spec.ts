// @vitest-environment happy-dom
import { mount, type VueWrapper } from '@vue/test-utils'
import { EditorView } from '@codemirror/view'
import { afterEach, expect, it } from 'vitest'
import MobileEditor from './MobileEditor.vue'

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined })

it('uses a real CodeMirror view and preserves CRLF/mixed source through typing and undo', async () => {
  const original = '# 中文\r\n😀\nlast'
  wrapper = mount(MobileEditor, { props: { modelValue: original, label: 'Note body', language: 'en' }, attachTo: document.body })
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ changes: { from: view.state.doc.length, insert: '\nmore' } })
  const changed = wrapper.emitted('update:modelValue')!.at(-1)![0] as string
  expect(changed).toBe(original + '\r\nmore')
  await wrapper.setProps({ modelValue: changed })
  ;(wrapper.vm as unknown as { undo: () => boolean }).undo()
  expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(original)
  expect(wrapper.emitted('error')).toBeUndefined()
})

it('changes ARIA/language without replacing source, selection or history', async () => {
  wrapper = mount(MobileEditor, { props: { modelValue: 'original\r\nbody', label: 'Body', language: 'en' }, attachTo: document.body })
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ selection: { anchor: 3 } })
  const before = view.state.doc.toString()
  await wrapper.setProps({ language: 'zh-CN', label: '笔记正文' })
  expect(view.state.selection.main.head).toBe(3)
  expect(view.contentDOM.getAttribute('aria-label')).toBe('笔记正文')
  expect(view.contentDOM.getAttribute('lang')).toBe('zh-CN')
  expect(view.state.doc.toString()).toBe(before)
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
})

it('blocks toolbar insertion in a readonly view and refuses foreign model replacement', async () => {
  wrapper = mount(MobileEditor, { props: { modelValue: 'keep my input', label: 'Body', language: 'en', readOnly: true }, attachTo: document.body })
  expect((wrapper.vm as unknown as { insert: (text: string) => boolean }).insert('replace')).toBe(false)
  await wrapper.setProps({ modelValue: 'late other document' })
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  expect(view.state.doc.toString()).toBe('keep my input')
  expect(wrapper.emitted('error')).toEqual([['MOBILE_EDITOR_CONTENT_CHANGED']])
})

it('maps a CRLF/emoji cursor into original UTF-16 bytes and undoes an acknowledged image insertion', async () => {
  const original = '# 中文\r\n😀\nend'
  wrapper = mount(MobileEditor, { props: { modelValue: original, label: 'Body', language: 'en', readOnly: true }, attachTo: document.body })
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ selection: { anchor: 7 } })
  const exposed = wrapper.vm as unknown as { selection: () => { from: number; to: number }; acceptImage: (before: string, after: string) => boolean; undo: () => boolean }
  expect(exposed.selection()).toEqual({ from: 8, to: 8 })
  const after = original.slice(0, 8) + '![](../attachments/ab/image.png)' + original.slice(8)
  expect(exposed.acceptImage(original, after)).toBe(true)
  expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(after)
  await wrapper.setProps({ modelValue: after, readOnly: false }); expect(exposed.undo()).toBe(true)
  expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(original)
})
