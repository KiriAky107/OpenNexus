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
