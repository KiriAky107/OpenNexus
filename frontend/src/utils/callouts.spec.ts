// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { calloutTypes, parseCallout } from './callouts'
import { renderMarkdown } from './markdown'

describe('callouts', () => {
  for (const [type, aliases] of Object.entries(calloutTypes)) {
    for (const alias of aliases) it(`renders ${alias}`, async () => {
      const root = document.createElement('div')
      root.innerHTML = await renderMarkdown(`> [!${alias.toUpperCase()}] 标题\n> **正文** 与 \`code\`\n>\n> - 条目`)
      expect(root.querySelector('.markdown-callout')?.getAttribute('data-callout')).toBe(type)
      expect(root.querySelector('.callout-title')?.textContent).toBe('标题')
      expect(root.querySelector('strong')?.textContent).toBe('正文')
      expect(root.querySelector('li')?.textContent).toBe('条目')
    })
  }
  it('supports folding, nesting, unknown types, empty bodies and safe titles', async () => {
    const root = document.createElement('div')
    root.innerHTML = await renderMarkdown('> [!WARNING]- 外层\n>\n> > [!tip]+ 内层\n> > 内容\n\n> [!custom] <img src=x onerror=alert(1)>\n\n> [!NOTE]')
    expect(root.querySelector('details')?.hasAttribute('open')).toBe(false)
    expect(root.querySelector('details details')?.hasAttribute('open')).toBe(true)
    expect(root.querySelectorAll('.markdown-callout')).toHaveLength(4)
    expect(root.querySelector('img')).toBeNull()
    expect(root.textContent).toContain('<img src=x onerror=alert(1)>')
  })
  it('renders the editor serialization of nested folded callouts', async () => {
    const root = document.createElement('div')
    root.innerHTML = await renderMarkdown('> [!WARNING]- 注意\n>\n> **正文**\n>\n> > [!TIP] 内层\n> > 内容\n')
    expect(root.querySelectorAll('.markdown-callout')).toHaveLength(2)
    expect(root.querySelector('strong')?.textContent).toBe('正文')
  })
  it('does not convert ordinary quotes, inline code, escaped markers or fenced examples', async () => {
    const root = document.createElement('div')
    root.innerHTML = await renderMarkdown('> ordinary\n\n> \\[!NOTE]\n\n`[!TIP]`\n\n```text\n> [!WARNING]\n```')
    expect(root.querySelector('.markdown-callout')).toBeNull()
    expect(root.querySelectorAll('blockquote')).toHaveLength(2)
    expect(parseCallout('prefix [!NOTE]')).toBeNull()
  })
})
