// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { Compartment } from '@codemirror/state'
import { LanguageDescription } from '@codemirror/language'
import { EditorView } from '@codemirror/view'
import { shikiLanguage, shikiLanguages } from './shikiCodeMirror'
import { getCodeTokenizer } from '@/utils/markdown'

const editors: EditorView[] = []
afterEach(() => { editors.splice(0).forEach(view => view.destroy()) })

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
  expect(languages.find(item => item.name === 'Python')?.alias).toContain('py')
  expect(languages.find(item => item.name === 'LaTeX')?.alias).toContain('latex')
})

it('preserves original loaders and metadata while replacing supported languages', async () => {
  const originalPython = LanguageDescription.of({ name: 'Python', alias: ['py', 'custom-python'], extensions: ['py'], filename: /^SConstruct$/, load: () => shikiLanguage('text', 'github-light') })
  const originalRust = LanguageDescription.of({ name: 'Rust', alias: ['rs'], extensions: ['rs'], load: () => shikiLanguage('text', 'github-light') })
  const languages = shikiLanguages('github-dark', [originalPython, originalRust])
  expect(languages.find(item => item.name === 'Rust')).toBe(originalRust)
  const python = languages.find(item => item.name === 'Python')!
  expect(languages.filter(item => item.alias.includes('python'))).toHaveLength(1)
  expect(python.alias).toContain('custom-python')
  expect(python.extensions).toEqual(['py'])
  expect(python.filename).toBe(originalPython.filename)
  expect(await python.load()).not.toBe(await originalPython.load())
})
