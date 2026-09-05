// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { editorViewCtx, type Editor } from '@milkdown/kit/core'
import { NodeSelection, TextSelection } from '@milkdown/kit/prose/state'
import { getMarkdown } from '@milkdown/kit/utils'
import VisualMarkdownEditor from './VisualMarkdownEditor.vue'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import { codeBlockConfig } from '@milkdown/kit/component/code-block'
import { EditorView as CodeMirror } from '@codemirror/view'
import { renderMarkdown } from '@/utils/markdown'

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
  it.each([['jsonc', 'JSON with Comments', '// comment\n{"answer": 42}'], ['ahk', 'AutoHotkey', 'MsgBox "Hello"']])('persists %s from the language menu and renders it with Shiki', async (id, label, source) => {
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: `\`\`\`text\n${source}\n\`\`\`` }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    editor.action(ctx => {
      const view = ctx.get(editorViewCtx)
      view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, 0)))
    })
    for (let attempt = 0; attempt < 100 && !wrapper.find('.language-button').exists(); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 10))
    }
    await wrapper.get('.language-button').trigger('click')
    const item = wrapper.get(`.language-list-item[data-language="${id}"]`)
    expect(item.text()).toBe(label)
    await item.trigger('click')
    const markdown = editor.action(getMarkdown())
    expect(markdown).toContain(`\`\`\`${id}\n`)
    const html = await renderMarkdown(markdown)
    expect(new Set([...html.matchAll(/--shiki-light:([^;" ]+)/g)].map(match => match[1])).size).toBeGreaterThan(1)
    const reopened = mount(VisualMarkdownEditor, { props: { initialContent: markdown }, attachTo: document.body })
    mounted.push(reopened)
    const restored = await waitForEditor(reopened)
    expect(restored.action(ctx => ctx.get(editorViewCtx).state.doc.firstChild?.attrs.language)).toBe(id)
  })
  it.each(['github-light', 'github-dark'] as const)('keeps Shiki %s mappings after Crepe merges its defaults', async theme => {
    useThemeStore().codeBlockTheme = theme
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: '```python\nprint("Hello")\n```' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    const config = editor.action(ctx => ctx.get(codeBlockConfig.key))
    const matching = config.languages.filter(item => item.alias.includes('python'))
    expect(matching).toHaveLength(1)
    for (const name of ['Java', 'Go', 'Rust']) {
      const language = config.languages.find(item => item.name === name.toLowerCase())
      expect(language, `${name} remains available`).toBeDefined()
      const view = new CodeMirror({ doc: 'class Example {}', extensions: [...config.extensions, await language!.load()] })
      try { expect(view.dom.querySelector('.shiki-token')).not.toBeNull() }
      finally { view.destroy() }
    }
    const cm = new CodeMirror({ doc: 'print("Hello")', extensions: [...config.extensions, await matching[0]!.load()] })
    try {
      const string = [...cm.dom.querySelectorAll<HTMLElement>('.shiki-token')].find(el => el.textContent?.includes('Hello'))
      expect(string?.style.color.toUpperCase()).toBe(theme === 'github-dark' ? '#9ECBFF' : '#032F62')
    } finally { cm.destroy() }
  })
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
