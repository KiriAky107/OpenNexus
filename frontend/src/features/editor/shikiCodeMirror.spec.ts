// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { Compartment } from '@codemirror/state'
import { bundledLanguagesInfo } from 'shiki/langs'
import { EditorView } from '@codemirror/view'
import { shikiLanguage, shikiLanguages } from './shikiCodeMirror'
import { getCodeTokenizer } from '@/utils/markdown'
import * as markdown from '@/utils/markdown'

const editors: EditorView[] = []
afterEach(() => { editors.splice(0).forEach(view => view.destroy()); vi.restoreAllMocks() })

it('reuses highlighting across recreated views and bounds retained entries', async () => {
  const tokenize = vi.fn(await getCodeTokenizer('github-light', 'javascript'))
  vi.spyOn(markdown, 'getCodeTokenizer').mockResolvedValue(tokenize)
  const support = await shikiLanguage('javascript', 'github-light')
  const create = (doc: string) => {
    const view = new EditorView({ doc, extensions: [support] })
    editors.push(view)
    return view
  }
  const source = 'const answer = 42'
  create(source)
  const recreated = create(source)
  expect(tokenize).toHaveBeenCalledTimes(1)
  expect(recreated.dom.textContent).toContain(source)
  recreated.dispatch({ changes: { from: 0, to: source.length, insert: 'let changed = 1' } })
  expect(tokenize).toHaveBeenCalledTimes(2)
  expect(recreated.dom.textContent).toContain('let changed = 1')
  for (let i = 0; i < 33; i++) create(`const value = ${i}`)
  const before = tokenize.mock.calls.length
  create(source)
  expect(tokenize).toHaveBeenCalledTimes(before + 1)
})

it.each(['github-light', 'github-dark'] as const)('uses Shiki %s tokens and updates editable content', async theme => {
  const support = await shikiLanguage('python', theme)
  const view = new EditorView({ doc: 'print("Hello")', extensions: [support] })
  editors.push(view)
  const tokenize = await getCodeTokenizer(theme)
  const expected = tokenize('print("Hello")', 'python')[0]!.find(token => token.content.includes('Hello'))!
  const colored = [...view.dom.querySelectorAll<HTMLElement>('.shiki-token')].find(node => node.textContent?.includes('Hello'))!
  expect(colored).toBeDefined()
  const sample = document.createElement('span'); sample.style.color = expected.color!
  expect(colored.style.color).toBe(sample.style.color)
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'def hello():\n    return 42' } })
  expect(view.state.doc.toString()).toContain('return 42')
  expect(view.dom.querySelectorAll('.shiki-token').length).toBeGreaterThan(2)
  expect(view.dom.textContent).toContain('return 42')
})

it('reconfigures language and theme without modifying the document', async () => {
  const config = new Compartment()
  const view = new EditorView({ doc: 'const answer = 42', extensions: [config.of(await shikiLanguage('javascript', 'github-light'))] })
  editors.push(view)
  const before = view.dom.querySelector<HTMLElement>('.shiki-token')!.style.color
  view.dispatch({ effects: config.reconfigure(await shikiLanguage('javascript', 'github-dark')) })
  expect(view.dom.querySelector<HTMLElement>('.shiki-token')!.style.color).not.toBe(before)
  expect(view.state.doc.toString()).toBe('const answer = 42')
  view.dispatch({ effects: config.reconfigure(await shikiLanguage('text', 'github-dark')) })
  expect(view.state.doc.toString()).toBe('const answer = 42')
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
