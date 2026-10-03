// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Compartment } from '@codemirror/state'
import { history, undo, redo } from '@codemirror/commands'
import { bundledLanguagesInfo } from 'shiki/langs'
import { EditorView } from '@codemirror/view'
import { shikiLanguage, shikiLanguages } from './shikiCodeMirror'
import { getCodeTokenizer } from '@/services/codeHighlighter'
import { computeHighlight } from './codeHighlightProcessor'
import { codeHighlights } from './codeHighlightClient'
import type { HighlightResult } from './codeHighlightProtocol'

vi.mock('./codeHighlightClient', () => ({ codeHighlights: { request: vi.fn(), cancel: vi.fn() } }))

const editors: EditorView[] = []
beforeEach(() => {
  vi.mocked(codeHighlights.request).mockReset().mockImplementation((_owner, input, done) => { void computeHighlight(input).then(done) })
  vi.mocked(codeHighlights.cancel).mockReset()
})
afterEach(() => { editors.splice(0).forEach(view => view.destroy()); vi.restoreAllMocks(); vi.useRealTimers() })

it('coalesces continuous edits outside input transactions and preserves selection and undo', async () => {
  vi.useFakeTimers()
  const support = await shikiLanguage('javascript', 'github-light')
  vi.mocked(codeHighlights.request).mockImplementation(() => {})
  const view = new EditorView({ doc: 'const answer = 42', extensions: [support, history()] })
  editors.push(view)
  for (let index = 0; index < 50; index++) view.dispatch({ changes: { from: view.state.doc.length, insert: ' ' } })
  view.dispatch({ selection: { anchor: 4, head: 8 } })
  expect(codeHighlights.request).not.toHaveBeenCalled()
  const edited = view.state.doc.toString()
  await vi.advanceTimersByTimeAsync(30)
  expect(codeHighlights.request).toHaveBeenCalledTimes(1)
  expect(vi.mocked(codeHighlights.request).mock.calls[0]![1].source).toBe(edited)
  vi.mocked(codeHighlights.request).mock.calls[0]![2]({ spans: new Uint32Array([0, 5, 0]), styles: ['color:red'] })
  expect([view.state.selection.main.from, view.state.selection.main.to]).toEqual([4, 8])
  expect(undo(view)).toBe(true)
  expect(view.state.doc.toString()).toBe('const answer = 42')
  expect(redo(view)).toBe(true)
  expect(view.state.doc.toString()).toBe(edited)
})

it.each(['github-light', 'github-dark'] as const)('uses Shiki %s tokens and updates editable content', async theme => {
  const support = await shikiLanguage('python', theme)
  const view = new EditorView({ doc: 'print("Hello")', extensions: [support] })
  editors.push(view)
  const tokenize = await getCodeTokenizer(theme, 'python')
  const expected = tokenize('print("Hello")', 'python')[0]!.find(token => token.content.includes('Hello'))!
  await vi.waitFor(() => expect(view.dom.querySelector('.shiki-token')).not.toBeNull())
  const colored = [...view.dom.querySelectorAll<HTMLElement>('.shiki-token')].find(node => node.textContent?.includes('Hello'))!
  expect(colored).toBeDefined()
  const sample = document.createElement('span'); sample.style.color = expected.color!
  expect(colored.style.color).toBe(sample.style.color)
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'def hello():\n    return 42' } })
  await vi.waitFor(() => expect(view.dom.querySelectorAll('.shiki-token').length).toBeGreaterThan(2))
  expect(view.state.doc.toString()).toContain('return 42')
  expect(view.dom.querySelectorAll('.shiki-token').length).toBeGreaterThan(2)
  expect(view.dom.textContent).toContain('return 42')
})

it('reconfigures language and theme without modifying the document', async () => {
  const config = new Compartment()
  const view = new EditorView({ doc: 'const answer = 42', extensions: [config.of(await shikiLanguage('javascript', 'github-light'))] })
  editors.push(view)
  await vi.waitFor(() => expect(view.dom.querySelector('.shiki-token')).not.toBeNull())
  const before = view.dom.querySelector<HTMLElement>('.shiki-token')!.style.color
  view.dispatch({ effects: config.reconfigure(await shikiLanguage('javascript', 'github-dark')) })
  await vi.waitFor(() => expect(view.dom.querySelector('.shiki-token')).not.toBeNull())
  expect(view.dom.querySelector<HTMLElement>('.shiki-token')!.style.color).not.toBe(before)
  expect(view.state.doc.toString()).toBe('const answer = 42')
  view.dispatch({ effects: config.reconfigure(await shikiLanguage('text', 'github-dark')) })
  expect(view.state.doc.toString()).toBe('const answer = 42')
})

it('discards obsolete document, language, theme and disposed-view replies', async () => {
  vi.useFakeTimers()
  vi.mocked(codeHighlights.request).mockImplementation(() => {})
  const config = new Compartment()
  const view = new EditorView({ doc: 'before', extensions: [config.of(await shikiLanguage('javascript', 'github-light'))] })
  editors.push(view)
  const result: HighlightResult = { spans: new Uint32Array([0, 1, 0]), styles: ['color:red'] }
  await vi.advanceTimersByTimeAsync(0)
  const first = vi.mocked(codeHighlights.request).mock.calls[0]![2]
  view.dispatch({ changes: { from: 0, to: 6, insert: 'after' } })
  first(result)
  expect(view.dom.querySelector('.shiki-token')).toBeNull()
  await vi.advanceTimersByTimeAsync(30)
  const second = vi.mocked(codeHighlights.request).mock.calls[1]![2]
  view.dispatch({ effects: config.reconfigure(await shikiLanguage('python', 'github-dark')) })
  second(result)
  expect(view.dom.querySelector('.shiki-token')).toBeNull()
  await vi.advanceTimersByTimeAsync(0)
  const third = vi.mocked(codeHighlights.request).mock.calls[2]![2]
  third(result)
  expect(view.dom.querySelector('.shiki-token')).not.toBeNull()
  view.destroy(); editors.splice(editors.indexOf(view), 1)
  expect(() => third(result)).not.toThrow()
})

it('keeps all text editable when highlighting fails or exceeds the source budget', async () => {
  vi.useFakeTimers()
  vi.mocked(codeHighlights.request).mockImplementation((_owner, _input, done) => done())
  const view = new EditorView({ doc: 'before', extensions: [await shikiLanguage('javascript', 'github-light')] })
  editors.push(view)
  await vi.advanceTimersByTimeAsync(0)
  view.dispatch({ changes: { from: 0, to: 6, insert: 'x'.repeat(1_000_001) } })
  await vi.advanceTimersByTimeAsync(30)
  expect(view.state.doc.length).toBe(1_000_001)
  expect(codeHighlights.request).toHaveBeenCalledTimes(1)
  expect(view.dom.querySelector('.shiki-token')).toBeNull()
})

it('offers fenced-code aliases and retains the LaTeX selector', () => {
  const languages = shikiLanguages('github-light')
  expect(languages.find(item => item.name === 'python')?.alias).toContain('py')
  expect(languages.find(item => item.name === 'latex')?.alias).toContain('latex')
})

it('offers every bundled Shiki language and alias', () => {
  const languages = shikiLanguages('github-light')
  expect(languages).toHaveLength(bundledLanguagesInfo.length + 2)
  for (const info of bundledLanguagesInfo) {
    const language = languages.find(item => item.alias.includes(info.id))!
    expect(language, info.id).toBeDefined()
    expect(language.name).toBe(info.id)
    expect(language.alias).toContain(info.name.toLowerCase())
    for (const alias of info.aliases ?? []) expect(language.alias).toContain(alias.toLowerCase())
  }
})

it('loads every bundled grammar and produces tokens with both GitHub themes', async () => {
  for (const info of bundledLanguagesInfo) {
    for (const theme of ['github-light', 'github-dark'] as const) {
      const tokenize = await getCodeTokenizer(theme, info.id)
      const tokens = tokenize('example = 42', info.id).flat()
      expect(tokens.map(token => token.content).join(''), info.id).toBe('example = 42')
      expect(tokens.every(token => token.color), info.id).toBe(true)
    }
  }
}, 120000)
