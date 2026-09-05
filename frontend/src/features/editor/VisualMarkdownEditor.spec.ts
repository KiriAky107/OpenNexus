// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { editorViewCtx, type Editor } from '@milkdown/kit/core'
import { TextSelection } from '@milkdown/kit/prose/state'
import { getMarkdown } from '@milkdown/kit/utils'
import VisualMarkdownEditor from './VisualMarkdownEditor.vue'
import { useSettingsStore } from '@/stores/settings'

type EditorComponent = { getEditor: () => Editor | undefined }

const mounted: VueWrapper[] = []

async function waitForEditor(wrapper: VueWrapper): Promise<Editor> {
  for (let attempt = 0; attempt < 100; attempt++) {
    const editor = (wrapper.vm as unknown as EditorComponent).getEditor()
    if (editor) {
      try {
        editor.action(getMarkdown())
        return editor
      } catch { /* editor is still creating */ }
    }
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error('Milkdown editor did not become ready')
}

function selectText(editor: Editor, from: number, to: number) {
  editor.action((ctx) => {
    const view = ctx.get(editorViewCtx)
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, from, to)))
    view.focus()
  })
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
})

describe('VisualMarkdownEditor formatting toolbars', () => {
  it('applies bold from the top toolbar to the selected text', async () => {
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: 'alpha beta' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    selectText(editor, 1, 6)

    await wrapper.get('[aria-label="加粗"]').trigger('pointerdown')

    expect(editor.action(getMarkdown())).toContain('**alpha** beta')
  })

  it('applies italic from the floating toolbar to the selected text', async () => {
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: 'alpha beta' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    selectText(editor, 1, 6)
    await new Promise((resolve) => setTimeout(resolve, 80))

    const floatingItalic = document.querySelector<HTMLButtonElement>('.milkdown-toolbar [data-toolbar-item="italic"]')
    expect(floatingItalic).not.toBeNull()
    floatingItalic?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))

    expect(editor.action(getMarkdown())).toContain('*alpha* beta')
  })

  it('writes a custom input font size into markdown for the selected text', async () => {
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: 'alpha beta' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    selectText(editor, 1, 6)

    await wrapper.get('[aria-label="自定义字号"]').setValue(22)
    await wrapper.get('[aria-label="应用自定义字号"]').trigger('pointerdown')

    expect(editor.action(getMarkdown())).toContain('<span style="font-size: 22px">alpha</span> beta')
  })

  it('turns a heading back into a normal paragraph', async () => {
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: '# alpha' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)

    await wrapper.get('[aria-label="标题级别"]').setValue('paragraph')

    expect(editor.action(getMarkdown()).trim()).toBe('alpha')
  })

  it('updates native spell checking on the ProseMirror editor', async () => {
    const settings = useSettingsStore()
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: 'mispelled word' }, attachTo: document.body })
    mounted.push(wrapper)
    await waitForEditor(wrapper)

    settings.spellCheck = true
    settings.language = 'en'
    await wrapper.vm.$nextTick()

    const editable = wrapper.get('.ProseMirror')
    expect(editable.attributes('spellcheck')).toBe('true')
    expect(editable.attributes('lang')).toBe('en')
  })
})
