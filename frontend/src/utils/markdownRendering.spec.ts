// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

it('renders CommonMark and GFM formats, escaped literals and safe HTML', async () => {
  const source = ['# H1','## H2','### H3','#### H4','##### H5','###### H6',
    '**bold** *italic* ~~deleted~~ `inline` ``a`b``', '> quote', '- item\n  - nested',
    '1. first\n2. second', '- [x] done\n- [ ] todo', '[link][ref]\n\n[ref]: https://example.com',
    '![alt](https://example.com/image.png)', '| A | B |\n| --- | --- |\n| x | y |', '---',
    'line  \nbreak', '\\`literal\\`', '<script>alert(1)</script><img src="x" onerror="alert(1)">',
    '```unknown-language\n<script>literal</script>\n```'].join('\n\n')
  const root = document.createElement('div')
  root.innerHTML = await renderMarkdown(source)
  for (const selector of ['h1','h2','h3','h4','h5','h6','strong','em','del','blockquote','ul','ol','table','hr','br','a','img','input[type=checkbox]','pre code']) expect(root.querySelector(selector), selector).not.toBeNull()
  expect(root.querySelector('code')?.textContent).toBe('inline')
  expect(root.textContent).toContain('`literal`')
  expect(root.querySelector('pre code')?.textContent).toContain('<script>literal</script>')
  expect(root.querySelector('script,[onerror]')).toBeNull()
})

it('renders inline, display and editor LaTeX fences while leaving code literals alone', async () => {
  const root = document.createElement('div')
  root.innerHTML = await renderMarkdown('$x^2$\n\n$$\nx^2\n$$\n\n```LaTeX\nx^2\n```\n\n`$literal$`\n\n```text\n$literal$\n```')
  expect(root.querySelectorAll('.katex')).toHaveLength(3)
  expect(root.querySelector('code')?.textContent).toBe('$literal$')
  expect(root.querySelector('pre code')?.textContent).toContain('$literal$')
})

it('renders numeric and legacy citations as numbered buttons without altering code', async () => {
  const root = document.createElement('div')
  root.innerHTML = await renderMarkdown('正文 [1][2] [cit_blk_a] `[1]` [3] [1](https://example.com)', { citationNumbers: [1, 2], citationAliases: { cit_blk_a: 2 } })
  expect([...root.querySelectorAll('.inline-citation')].map(c => c.textContent)).toEqual(['[1]', '[2]', '[2]'])
  expect(root.querySelector('code')?.textContent).toBe('[1]')
  expect(root.querySelector('a')?.getAttribute('href')).toBe('https://example.com')
})

it('shows code language and preserves exact source for copying', async () => {
  const root = document.createElement('div')
  root.innerHTML = await renderMarkdown('```python\nprint("hello")\n```')
  expect(root.querySelector('.markdown-code-toolbar')?.textContent).toContain('python')
  expect(root.querySelector('[data-code-action="copy"]')).not.toBeNull()
  expect(root.querySelector('.markdown-code-source')?.textContent).toBe('print("hello")\n')
})

it('keeps code toolbar inside the themed frame and avoids extra rendered newline rows', async () => {
  const root = document.createElement('div')
  root.innerHTML = await renderMarkdown('```markdown\n# First\n\n## Second\n```')
  const frame = root.querySelector('.markdown-code-block')!
  expect(frame.getAttribute('data-language-label')).toBe('markdown')
  expect(frame.querySelector(':scope > .markdown-code-toolbar')).not.toBeNull()
  const code = frame.querySelector('.shiki code')!
  expect([...code.childNodes].filter(n => n.nodeType === Node.TEXT_NODE && n.textContent?.includes('\n'))).toHaveLength(0)
  expect(code.querySelectorAll('.line')).toHaveLength(4)
  expect(frame.querySelector('.markdown-code-source')?.textContent).toBe('# First\n\n## Second\n')
})
