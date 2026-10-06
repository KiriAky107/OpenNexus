// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import { MarkdownPreview } from './markdownPreview'
import { previewHighlighter } from '@/services/previewHighlighter'
import { plainCode } from '@/services/previewHighlightProtocol'
import { defaultMarkdownPreferences } from '@/stores/markdownPreferences'

vi.mock('@/services/previewHighlighter', () => ({ previewHighlighter: { highlight: vi.fn() } }))
vi.mock('@/services/mermaidService', () => ({ renderMermaid: vi.fn(async () => ({
  warnings: [], svg: '<svg viewBox="0 0 100 100"><text>Diagram</text></svg>',
})) }))
beforeEach(() => { vi.mocked(previewHighlighter.highlight).mockReset().mockImplementation(async source => plainCode(source)) })

it('retains completed code DOM and interactive block state during text-only stream updates', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  const source = '> [!NOTE]- Fold\n> body\n\n```typescript\nconst value = 1;\n```\n\nTail'
  await renderer.render(source)
  const pre = root.querySelector('.shiki')!, code = pre.querySelector('code')!
  const fold = root.querySelector('details')!; fold.open = true
  pre.scrollLeft = 73
  for (let index = 0; index < 12; index++) {
    await renderer.render(source + ' ' + index, { previewOnly: true })
    await renderer.render(source + ' ' + index)
  }
  expect(root.querySelector('.shiki')).toBe(pre)
  expect(root.querySelector('.shiki code')).toBe(code)
  expect(pre.scrollLeft).toBe(73)
  expect(root.querySelector('details')).toBe(fold)
  expect(fold.open).toBe(true)
  expect(previewHighlighter.highlight).toHaveBeenCalledTimes(1)
  expect(root.querySelector('.markdown-code-source')!.textContent).toBe('const value = 1;\n')
})

it('resolves references across blocks and invalidates a link when its later definition changes', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  await renderer.render('[Doc][ref]\n\n[ref]: ./old.md\n\n[1] [cit_a]', { citationNumbers: [1], citationAliases: { cit_a: 1 } })
  expect(root.querySelector('a')?.getAttribute('href')).toBe('./old.md')
  expect(root.querySelectorAll('.inline-citation')).toHaveLength(2)
  await renderer.render('[Doc][ref]\n\n[ref]: ./new.md\n\n[1] [cit_a]', { citationNumbers: [1], citationAliases: { cit_a: 1 } })
  expect(root.querySelector('a')?.getAttribute('href')).toBe('./new.md')
})

it('retains literal code when citations, reference definitions and CSS-only preferences change', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  const source = '```typescript\nconst value = 1;\n```\n\n[Doc][ref] [1]\n\n[ref]: ./old.md'
  await renderer.render(source, { theme: 'light', preferences: defaultMarkdownPreferences })
  const code = root.querySelector('.shiki')!
  await renderer.render(source.replace('./old.md', './new.md'), { theme: 'dark', citationNumbers: [1],
    preferences: { ...defaultMarkdownPreferences, wrapCode: !defaultMarkdownPreferences.wrapCode } })
  expect(root.querySelector('.shiki')).toBe(code)
  expect(previewHighlighter.highlight).toHaveBeenCalledTimes(1)
  expect(root.querySelector('a')?.getAttribute('href')).toBe('./new.md')
  expect(root.querySelector('.inline-citation')).not.toBeNull()
})

it('handles duplicate blocks independently and preserves the correct nodes when reordered', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  await renderer.render('Same\n\nOther\n\nSame')
  const paragraphs = [...root.querySelectorAll('p')]
  await renderer.render('Other\n\nSame\n\nSame')
  expect([...root.querySelectorAll('p')]).toEqual([paragraphs[1], paragraphs[0], paragraphs[2]])
})

it('sanitizes HTML and nested code, without interpreting literal code or user-made placeholders', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  await renderer.render('<div data-preview-code="0"><img src=x onerror="alert(1)"></div>\n\n> ```text\n> <script>x</script> & <img src=x onerror="bad">\n> ```\n\n$x^2$')
  expect(root.querySelector('script,[onerror]')).toBeNull()
  expect(root.querySelector('blockquote .shiki code')?.textContent).toBe('<script>x</script> & <img src=x onerror="bad">')
  expect(root.querySelector('.katex')).not.toBeNull()
  expect(root.querySelector('[data-preview-code]')?.querySelector('.shiki')).toBeNull()
})

it('cancels old highlight work before it can replace final text', async () => {
  let complete!: (html: string) => void
  vi.mocked(previewHighlighter.highlight).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  const root = document.createElement('div'), renderer = new MarkdownPreview(root), controller = new AbortController()
  const old = renderer.render('```text\nobsolete\n```', { signal: controller.signal })
  await vi.waitFor(() => expect(complete).toBeTypeOf('function'))
  controller.abort()
  await renderer.render('Final text')
  complete(plainCode('obsolete\n'))
  await expect(old).rejects.toMatchObject({ name: 'AbortError' })
  expect(root.textContent?.trim()).toBe('Final text')
})

it('yields while coloring a large block and resumes cancelled coloring without rebuilding its frame', async () => {
  const source = Array.from({ length: 240 }, (_, i) => `line ${i}`).join('\n')
  const root = document.createElement('div'), renderer = new MarkdownPreview(root), controller = new AbortController()
  await renderer.render('```text\n' + source + '\n```\n\nTail', { previewOnly: true })
  const pre = root.querySelector('.shiki')!, first = pre.querySelector('.line')!
  const work = renderer.render('```text\n' + source + '\n```\n\nTail', { signal: controller.signal })
  await Promise.resolve(); await Promise.resolve()
  expect(root.querySelector('[data-highlight-state="pending"]')).not.toBeNull()
  expect(pre.querySelector('.line')).toBe(first)
  controller.abort()
  await expect(work).rejects.toMatchObject({ name: 'AbortError' })
  await renderer.render('```text\n' + source + '\n```\n\nTail updated')
  expect(root.querySelector('.shiki')).toBe(pre)
  expect(pre.querySelector('.line')).not.toBe(first)
  expect([...pre.querySelectorAll('.line')].map(line => line.textContent).join('\n')).toBe(source + '\n')
  expect(root.querySelector('[data-highlight-state="complete"]')).not.toBeNull()
  expect(previewHighlighter.highlight).toHaveBeenCalledTimes(1)
})

it('retains diagram zoom/source state until a theme change rerenders the diagram', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  const source = '```mermaid\ngraph TD; A-->B\n```\n\nTail'
  await renderer.render(source, { theme: 'light' })
  const diagram = root.querySelector<HTMLElement>('.markdown-mermaid')!
  diagram.dataset.diagramScale = '2'; diagram.querySelector<HTMLElement>('.markdown-code-source')!.hidden = false
  await renderer.render(source + ' updated', { theme: 'light' })
  expect(root.querySelector('.markdown-mermaid')).toBe(diagram)
  expect(diagram.dataset.diagramScale).toBe('2')
  expect(diagram.querySelector<HTMLElement>('.markdown-code-source')!.hidden).toBe(false)
  await renderer.render(source, { theme: 'dark' })
  expect(root.querySelector('.markdown-mermaid')).not.toBe(diagram)
})

it('renders growing/open and closed fences, lists and math without losing literal source', async () => {
  const root = document.createElement('div'), renderer = new MarkdownPreview(root)
  await renderer.render('```text\n[1] <unsafe>')
  expect(root.querySelector('.markdown-code-source')?.textContent).toBe('[1] <unsafe>\n')
  await renderer.render('```text\n[1] <unsafe>\n```\n\n- first\n- **second**\n\n$x^2$', { citationNumbers: [1] })
  expect(root.querySelector('.markdown-code-source')?.textContent).toBe('[1] <unsafe>\n')
  expect(root.querySelector('.inline-citation')).toBeNull()
  expect(root.querySelectorAll('li')).toHaveLength(2)
  expect(root.querySelector('li strong')?.textContent).toBe('second')
  expect(root.querySelector('.katex')).not.toBeNull()
  await renderer.render('Final **body** with $x^3$')
  expect(root.querySelector('.markdown-code-block,li')).toBeNull()
  expect(root.querySelector('strong')?.textContent).toBe('body')
  expect(root.querySelector('.katex')).not.toBeNull()
})
