// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
// The application has a doctype; happy-dom otherwise reports quirks mode to KaTeX.
vi.hoisted(() => { Object.defineProperty(document, 'compatMode', {value:'CSS1Compat',configurable:true}) })
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
import { useEditorStore } from '@/stores/editor'
import { executeEditorCommand } from '@/services/editorCommandService'
import { headingFoldKey } from './headingFolding'
import { useMarkdownPreferencesStore } from '@/stores/markdownPreferences'

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
  it('applies syntax and renderer preferences when opening the visual editor', async () => {
    const preferences = useMarkdownPreferencesStore()
    preferences.preferences.heading = 'setext'
    preferences.preferences.bullet = '+'
    preferences.preferences.fence = '~'
    preferences.preferences.callouts = false
    preferences.preferences.math = false
    preferences.preferences.autoLinks = false
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: '# Heading\n\n- first\n- second\n\n> [!NOTE]\n> text\n\nhttps://example.com\n\n```text\ncode\n```' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    const result = editor.action(getMarkdown())
    expect(result).toContain('Heading\n===')
    expect(result).toContain('+ first')
    expect(result).toContain('~~~text')
    expect(wrapper.find('.markdown-callout').exists()).toBe(false)
    expect(wrapper.find('.ProseMirror a').exists()).toBe(false)
  })
  it('folds heading sections, retains nested state and opens hidden outline targets', async () => {
    const source = '# A\n\nbody\n\n## B\n\nchild\n\n# C\n\nvisible'
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: source }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    await wrapper.get('.heading-fold-toggle[aria-label="折叠 H2 B"]').trigger('click')
    await wrapper.get('.heading-fold-toggle[aria-label="折叠 H1 A"]').trigger('click')
    expect(wrapper.findAll('.heading-fold-hidden').length).toBeGreaterThan(1)
    await wrapper.get('.heading-fold-toggle[aria-label="展开 H1 A"]').trigger('click')
    expect(wrapper.get('.heading-fold-toggle[aria-label="展开 H2 B"]').attributes('aria-expanded')).toBe('false')
    editor.action(ctx => {
      const view = ctx.get(editorViewCtx)
      let position = 0
      view.state.doc.descendants((node, pos) => { if (node.isText && node.text === 'child') position = pos })
      view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, position)))
      expect(headingFoldKey.getState(view.state)?.size).toBe(0)
    })
    expect(wrapper.find('.heading-fold-hidden').exists()).toBe(false)
    expect(editor.action(getMarkdown()).trim()).toBe(source)
    await wrapper.get('button[aria-label="折叠所有章节"]').trigger('click')
    expect(wrapper.findAll('.section-actions button')).toHaveLength(1)
    expect(wrapper.get('.section-actions button').text()).toBe('全部展开')
    await wrapper.get('.heading-fold-toggle[aria-label="展开 H1 C"]').trigger('click')
    expect(wrapper.get('.section-actions button').text()).toBe('全部折叠')
    await wrapper.get('button[aria-label="折叠所有章节"]').trigger('click')
    await wrapper.get('button[aria-label="展开所有章节"]').trigger('click')
    expect(wrapper.get('.section-actions button').text()).toBe('全部折叠')
    expect(wrapper.find('.heading-fold-hidden').exists()).toBe(false)
  })
  it('offers expand all when individually collapsed parents hide expanded children', async () => {
    const source = '# A\n\nbody\n\n## B\n\nchild\n\n# C\n\nbody'
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: source }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    await wrapper.get('.heading-fold-toggle[aria-label="折叠 H1 A"]').trigger('click')
    expect(wrapper.get('.section-actions button').text()).toBe('全部折叠')
    await wrapper.get('.heading-fold-toggle[aria-label="折叠 H1 C"]').trigger('click')
    expect(wrapper.get('.section-actions button').text()).toBe('全部展开')
    expect(wrapper.get('.heading-fold-toggle[aria-label="折叠 H2 B"]').attributes('aria-expanded')).toBe('true')
    await wrapper.get('.heading-fold-toggle[aria-label="展开 H1 A"]').trigger('click')
    expect(wrapper.get('.section-actions button').text()).toBe('全部折叠')
    expect(wrapper.get('.heading-fold-toggle[aria-label="折叠 H2 B"]').attributes('aria-expanded')).toBe('true')
    await wrapper.get('.heading-fold-toggle[aria-label="折叠 H1 A"]').trigger('click')
    await wrapper.get('.section-actions button').trigger('click')
    expect(wrapper.find('.heading-fold-hidden').exists()).toBe(false)
    expect(wrapper.get('.section-actions button').text()).toBe('全部折叠')
    expect(editor.action(getMarkdown()).trim()).toBe(source)
  })
  it('renders and folds callouts without losing portable Markdown on serialization', async () => {
    const source = '> [!WARNING]- 注意\n>\n> **正文**\n>\n> > [!TIP] 内层\n> > 内容'
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: source }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    expect(wrapper.findAll('.markdown-callout')).toHaveLength(2)
    expect(wrapper.get('.markdown-callout').attributes('data-collapsed')).toBe('true')
    await wrapper.get('.callout-title').trigger('click')
    expect(wrapper.get('.markdown-callout').attributes('data-collapsed')).toBe('false')
    const markdown = editor.action(getMarkdown())
    expect(markdown.trim()).toBe(source)
  })
  it('dispatches native-ready commands through editor transactions and rejects invalid parameters', async () => {
    useEditorStore().currentFilePath = 'test.md'
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: 'text' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    expect(await executeEditorCommand('editor.heading', 8)).toEqual({ ok: false, reason: 'invalid-params' })
    expect(await executeEditorCommand('editor.heading', 2)).toEqual({ ok: true })
    expect(editor.action(getMarkdown())).toContain('## text')
    expect(await executeEditorCommand('editor.callout', { type: 'tip', body: '**test**' })).toEqual({ ok: true })
    expect(wrapper.find('.markdown-callout').exists()).toBe(true)
    useEditorStore().saveStatus = 'conflict'
    expect(await executeEditorCommand('editor.bold')).toEqual({ ok: false, reason: 'unavailable' })
  })
  it('keeps code examples as ordinary quotes and renders newly typed markers', async () => {
    const wrapper = mount(VisualMarkdownEditor, { props: { initialContent: '> `[!NOTE]`\n\n> text' }, attachTo: document.body })
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    expect(wrapper.find('.markdown-callout').exists()).toBe(false)
    editor.action(ctx => {
      const view = ctx.get(editorViewCtx)
      let position = 0
      view.state.doc.descendants((node, pos) => { if (node.isText && node.text === 'text') position = pos })
      view.dispatch(view.state.tr.insertText('[!TIP]', position, position + 4))
    })
    expect(wrapper.find('.markdown-callout').exists()).toBe(true)
    expect(editor.action(getMarkdown())).toContain('[!TIP]')
  })
  it('renders the supported format matrix and preserves inline code', async () => {
    const source = ['# H1','## H2','### H3','#### H4','##### H5','###### H6',
      '正文 **粗体** *斜体* ~~删除~~ `s` 与 ``a`b``', '> 引用', '- 项目\n  - 子项', '1. 第一\n2. 第二',
      '- [x] 完成\n- [ ] 未完成', '[链接](https://example.com)',
      '| A | B |\n| --- | --- |\n| x | y |', '---', '$x^2$', '$$\nx^2\n$$', '```js\nconst n = 1\n```'].join('\n\n')
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:source},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    expect(wrapper.get('.ProseMirror code').text()).toBe('s')
    for (const selector of ['h1','h2','h3','h4','h5','h6','strong','em','del','blockquote','ol','ul','table','hr','a']) expect(wrapper.find(`.ProseMirror ${selector}`).exists(), selector).toBe(true)
    expect(editor.action(getMarkdown())).toContain('`s`')
    expect(editor.action(getMarkdown())).toContain('``a`b``')
  })
  it('converts a typed closing backtick to inline code', async () => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    editor.action(ctx => {
      const view = ctx.get(editorViewCtx)
      for (const text of '`s`') {
        const {from,to} = view.state.selection
        let handled = false
        view.someProp('handleTextInput', handler => { if (handler(view,from,to,text, () => view.state.tr.insertText(text,from,to))) { handled = true; return true } })
        if (!handled) view.dispatch(view.state.tr.insertText(text,from,to))
      }
    })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.ProseMirror code').text()).toBe('s')
  })
  it('reconciles IME composition text without handleTextInput', async () => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    editor.action(ctx => ctx.get(editorViewCtx).dispatch(ctx.get(editorViewCtx).state.tr.insertText('`s`')))
    await wrapper.get('.ProseMirror').trigger('compositionend', {data:'`s`'})
    await new Promise(resolve => setTimeout(resolve, 30))
    expect(wrapper.get('.ProseMirror code').text()).toBe('s')
    expect(editor.action(getMarkdown()).trim()).toBe('`s`')
  })
  it.each(['insertText', 'insertCompositionText', 'insertReplacementText'])('reconciles %s without event.data after the DOM update', async (inputType) => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    // Chromium/IME can omit data and commit its DOM change after the input event.
    await wrapper.get('.ProseMirror').trigger('input', {inputType, data:null})
    await new Promise(resolve => setTimeout(resolve, 10))
    editor.action(ctx => ctx.get(editorViewCtx).dispatch(ctx.get(editorViewCtx).state.tr.insertText('`s`')))
    await vi.waitFor(() => expect(wrapper.get('.ProseMirror code').text()).toBe('s'))
  })
  it('waits for composition cleanup before converting committed text', async () => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    const view = editor.action(ctx => ctx.get(editorViewCtx))
    const composing = vi.spyOn(view, 'composing', 'get').mockReturnValue(true)
    await wrapper.get('.ProseMirror').trigger('compositionstart')
    view.dispatch(view.state.tr.insertText('`s`'))
    await wrapper.get('.ProseMirror').trigger('compositionend', {data:'`s`'})
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(wrapper.find('.ProseMirror code').exists()).toBe(false)
    composing.mockReturnValue(false)
    await vi.waitFor(() => expect(wrapper.get('.ProseMirror code').text()).toBe('s'))
    composing.mockRestore()
  })
  it('converts content typed between an existing pair of backticks', async () => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    const view = editor.action(ctx => ctx.get(editorViewCtx))
    view.dispatch(view.state.tr.insertText('``'))
    await wrapper.get('.ProseMirror').trigger('input', {inputType:'insertText', data:'`'})
    await new Promise(resolve => setTimeout(resolve, 60))
    // Empty pairs are serialized as escaped literal text, but that must not
    // prevent recognition after the user moves back and fills in the content.
    expect(editor.action(getMarkdown())).toContain('\\`')
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 2)).insertText('s'))
    await wrapper.get('.ProseMirror').trigger('input', {inputType:'insertText', data:'s'})
    await vi.waitFor(() => expect(wrapper.get('.ProseMirror code').text()).toBe('s'))
    expect(editor.action(getMarkdown()).trim()).toBe('`s`')
    expect(view.state.selection.from).toBe(2)
    view.dispatch(view.state.tr.insertText('tring'))
    expect(editor.action(getMarkdown()).trim()).toBe('`string`')
  })
  it.each(['insertFromPaste', 'historyUndo', 'deleteContentBackward'])('does not reinterpret literals on %s', async (inputType) => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    editor.action(ctx => ctx.get(editorViewCtx).dispatch(ctx.get(editorViewCtx).state.tr.insertText('`s`')))
    await wrapper.get('.ProseMirror').trigger('input', {inputType, data:null})
    await new Promise(resolve => setTimeout(resolve, 60))
    expect(wrapper.find('.ProseMirror code').exists()).toBe(false)
  })
  it('enables inline code from the toolbar at an empty selection', async () => {
    const wrapper = mount(VisualMarkdownEditor, {props:{initialContent:''},attachTo:document.body})
    mounted.push(wrapper)
    const editor = await waitForEditor(wrapper)
    await wrapper.get('[aria-label="行内代码"]').trigger('pointerdown')
    editor.action(ctx => ctx.get(editorViewCtx).dispatch(ctx.get(editorViewCtx).state.tr.insertText('value')))
    expect(editor.action(getMarkdown()).trim()).toBe('`value`')
  })
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
