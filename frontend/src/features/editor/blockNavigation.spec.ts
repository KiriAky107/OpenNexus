// @vitest-environment happy-dom
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { EditorView } from '@codemirror/view'
import { editorViewCtx, type Editor } from '@milkdown/kit/core'
import { EditorView as ProseMirrorView } from '@milkdown/kit/prose/view'
import { headingFoldTransaction, headingFoldKey } from './headingFolding'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import * as workspace from '@/services/workspaceService'
import * as notes from '@/services/noteService'
import { navigateToCitation } from '@/composables/useCitationNavigation'
import SourceMarkdownEditor from './SourceMarkdownEditor.vue'
import VisualMarkdownEditor from './VisualMarkdownEditor.vue'
import EditorHeader from './EditorHeader.vue'
import SearchView from '@/features/search/SearchView.vue'
import { useSearchStore } from '@/stores/search'
import fixture from '../../../tests/fixtures/citation-note.json'

// Generated from backend app.knowledge.parser.parse_blocks, including UTF-16
// offsets, duplicate IDs, CRLF, Markdown formatting and frontmatter.
vi.hoisted(() => { Object.defineProperty(document, 'compatMode', { value: 'CSS1Compat', configurable: true }) })
vi.mock('@/router', () => ({ default: { push: vi.fn().mockResolvedValue(undefined) } }))
vi.mock('vue-router', async original => ({ ...await original<object>(), useRouter: () => ({ push: vi.fn().mockResolvedValue(undefined) }) }))
let wrapper: VueWrapper | undefined
beforeEach(async () => {
  localStorage.clear(); setActivePinia(createPinia())
  vi.spyOn(workspace, 'readFileContent').mockResolvedValue(fixture.markdown)
  vi.spyOn(workspace, 'getNoteId').mockResolvedValue(fixture.note_id)
  vi.spyOn(notes, 'getNote').mockResolvedValue(structuredClone(fixture))
  useWorkspaceStore().vaultId = 'citation-test'
  const store = useEditorStore()
  vi.spyOn(store, 'scheduleAutoSave').mockImplementation(() => {})
  await store.loadFile('/note.md')
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; useEditorStore().closeFile(); vi.restoreAllMocks() })
afterAll(async () => { await new Promise(resolve => setTimeout(resolve, 3100)) })

const targets = [
  ['The **cited statement**', 'The cited statement includes 中文🙂 and a link.'],
  ['> Nested quoted target.', 'Nested quoted target.\nSecond line.'],
  ['- List target one', 'List target one\nList target two'],
  ['```ts', 'const target = "quoted code"'],
  ['Duplicate paragraph.', 'Duplicate paragraph.'],
] as const

async function cite(prefix: string) {
  const block = fixture.blocks.filter(block => block.content.startsWith(prefix)).at(-1)!
  const store = useEditorStore(), navigate = vi.fn()
  await navigateToCitation({ file_path: fixture.file_path, block_id: block.block_id }, { loadFile: store.loadFile, highlightBlock: store.highlightBlock, navigate })
  await flushPromises()
  expect(navigate).toHaveBeenCalledWith('/workspace')
  expect(store.blockNavigationNotice).toBe('')
  return block
}

it.each(targets)('selects the exact source block for %s', async prefix => {
  wrapper = mount(SourceMarkdownEditor, { props: { initialContent: fixture.markdown }, attachTo: document.body })
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  const dispatch = vi.spyOn(view, 'dispatch')
  const block = await cite(prefix)
  expect(view.state.selection.main.from).toBe(fixture.markdown.slice(0, block.start_offset).replace(/\r\n/g, '\n').length)
  const highlighted: string[] = []
  for (const source of view.state.facet(EditorView.decorations)) {
    const decorations = typeof source === 'function' ? source(view) : source
    decorations.between(0, view.state.doc.length, (from, to, value) => {
      if (value.spec.class === 'cm-citation-highlight') highlighted.push(view.state.sliceDoc(from, to))
    })
  }
  expect(highlighted).toEqual([block.content])
  expect(wrapper.find('.cm-citation-highlight').exists()).toBe(true)
  expect(dispatch.mock.calls.some(([transaction]) => !!transaction.effects)).toBe(true)
})

it.each(targets)('selects and reveals the rendered passage for %s', async (prefix, visible) => {
  wrapper = mount(VisualMarkdownEditor, { props: { initialContent: fixture.markdown }, attachTo: document.body })
  await vi.waitFor(() => expect(wrapper!.find('.ProseMirror').exists()).toBe(true))
  await flushPromises()
  const editor = (wrapper.vm as unknown as { getEditor: () => Editor }).getEditor()
  const view = editor.action(ctx => ctx.get(editorViewCtx))
  view.dispatch(headingFoldTransaction(view.state, 'all')!)
  expect(headingFoldKey.getState(view.state)?.size).toBeGreaterThan(0)
  const dispatch = vi.spyOn(view, 'dispatch')
  await cite(prefix)
  const selection = view.state.selection
  expect(selection.from).toBeGreaterThan(20)
  expect(view.state.doc.textBetween(selection.from, selection.to, '\n', '\n').trim()).toBe(visible)
  if (prefix === 'Duplicate paragraph.') {
    const positions: number[] = []
    view.state.doc.descendants((node, pos) => { if (node.isText && node.text === visible) positions.push(pos) })
    expect(selection.from).toBe(positions.at(-1))
  }
  expect(headingFoldKey.getState(view.state)?.size).toBe(0)
  expect(dispatch.mock.calls.some(([tr]) => tr.scrolledIntoView)).toBe(true)
})

it('reveals a citation received before the writing editor mounts only after its visible DOM owns focus', async () => {
  await cite('The **cited statement**')
  const scrolls: Array<{ loading: boolean; focused: boolean; selected: string }> = []
  const dispatch = ProseMirrorView.prototype.dispatch
  vi.spyOn(ProseMirrorView.prototype, 'dispatch').mockImplementation(function (this: ProseMirrorView, tr) {
    if (tr.scrolledIntoView) scrolls.push({
      loading: this.dom.closest('.milkdown-host')!.classList.contains('loading'),
      focused: this.hasFocus(),
      selected: tr.doc.textBetween(tr.selection.from, tr.selection.to, '\n', '\n').trim(),
    })
    // Keep the real state, DOM selection and ProseMirror scroll implementation.
    return dispatch.call(this, tr)
  })
  wrapper = mount(VisualMarkdownEditor, { props: { initialContent: fixture.markdown }, attachTo: document.body })
  await vi.waitFor(() => expect(scrolls).not.toHaveLength(0))
  expect(scrolls).toEqual([{
    loading: false,
    focused: true,
    selected: 'The cited statement includes 中文🙂 and a link.',
  }])
  const editor = (wrapper.vm as unknown as { getEditor: () => Editor }).getEditor()
  const view = editor.action(ctx => ctx.get(editorViewCtx))
  // List node views restore their mount-time selection on the next animation
  // frame. The citation must still own the range after those callbacks settle.
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  expect(view.state.doc.textBetween(view.state.selection.from, view.state.selection.to)).toBe(scrolls[0]!.selected)
})

it('shows an explicit stale-block fallback and lets a later citation recover', async () => {
  const store = useEditorStore()
  await store.highlightBlock('deleted-block')
  expect(store.blockRequest).toMatchObject({ offset: 0, length: 0 })
  wrapper = mount(EditorHeader)
  expect(wrapper.text()).toContain('引用段落已更新或删除')
  await cite('The **cited statement**')
  expect(store.blockRequest!.offset).toBeGreaterThan(0)
  expect(wrapper.text()).not.toContain('引用段落已更新或删除')
})

it('discards a late block response after another same-file navigation', async () => {
  let resolve!: (note: typeof fixture) => void
  vi.mocked(notes.getNote).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  const store = useEditorStore(), pending = store.highlightBlock(fixture.blocks.at(-1)!.block_id)
  await store.loadFile('/note.md')
  resolve(fixture); await pending
  expect(store.blockRequest).toBeNull()
  expect(store.blockNavigationNotice).toBe('')
})

it('does not guess between duplicate paragraphs after unsaved edits move the source', async () => {
  const store = useEditorStore()
  store.updateContent('Added paragraph.\r\n\r\n' + fixture.markdown)
  await store.highlightBlock(fixture.blocks.at(-1)!.block_id)
  expect(store.blockRequest).toMatchObject({ offset: 0, length: 0 })
  expect(store.blockNavigationNotice).toContain('引用段落已更新或删除')
  expect(store.content.startsWith('Added paragraph.')).toBe(true)
})

it('relocates a unique unchanged paragraph after unsaved edits move it', async () => {
  const store = useEditorStore(), prefix = 'Added paragraph.\r\n\r\n'
  store.updateContent(prefix + fixture.markdown)
  const block = await cite('The **cited statement**')
  expect(store.blockRequest!.offset).toBe(prefix.length + block.start_offset)
  expect(store.saveStatus).toBe('dirty')
})

it('opens a clicked search result at the same real source range', async () => {
  const block = fixture.blocks.find(block => block.content.startsWith('The **cited'))!
  const search = useSearchStore()
  vi.spyOn(search, 'loadHistory').mockResolvedValue()
  search.results = [{ note_id: fixture.note_id, note_title: fixture.title, file_path: fixture.file_path,
    block_id: block.block_id, heading_path: 'Target', snippet: block.content, score: 1, match_type: 'fts' }]
  wrapper = mount(SearchView)
  await wrapper.get('.result-card').trigger('click'); await flushPromises()
  expect(useEditorStore().blockRequest).toMatchObject({ path: '/note.md', offset: block.start_offset, length: block.end_offset - block.start_offset })
  wrapper.unmount()
  wrapper = mount(SourceMarkdownEditor, { props: { initialContent: fixture.markdown }, attachTo: document.body })
  await flushPromises()
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  expect(view.state.selection.main.from).toBe(fixture.markdown.slice(0, block.start_offset).replace(/\r\n/g, '\n').length)
  expect(wrapper.find('.cm-citation-highlight').exists()).toBe(true)
})

it('keeps a failed block lookup retryable and clears it after a successful retry', async () => {
  const store = useEditorStore(), block = fixture.blocks.at(-1)!
  vi.mocked(notes.getNote).mockRejectedValueOnce(new Error('temporarily unavailable'))
  await store.highlightBlock(block.block_id)
  expect(store.blockNavigationNotice).toContain('可重试引用')
  expect(store.blockRequest).toMatchObject({ offset: 0, length: 0 })
  await store.highlightBlock(block.block_id)
  expect(store.blockNavigationNotice).toBe('')
  expect(store.blockRequest!.offset).toBe(block.start_offset)
})
