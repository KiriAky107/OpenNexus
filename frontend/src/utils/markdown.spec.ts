import { describe, expect, it, vi } from 'vitest'
import { highlightCode } from './markdown'
vi.mock('@/services/previewHighlighter', async () => {
  const { computePreviewHighlight } = await import('@/services/previewHighlightProcessor')
  return { previewHighlighter: { highlight: computePreviewHighlight } }
})

describe('Shiki GitHub 双主题', () => {
  it('一次渲染同时生成 GitHub Light 和 GitHub Dark 颜色变量', async () => {
    const html = await highlightCode('const answer = 42', 'typescript')

    expect(html).toContain('github-light')
    expect(html).toContain('github-dark')
    expect(html).toContain('--shiki-light')
    expect(html).toContain('--shiki-dark')
  })

  it('按需加载原先未支持的语言，并解析 Shiki 别名', async () => {
    const source = 'fn main() { let answer = 42; }'
    const [rust, alias] = await Promise.all([highlightCode(source, 'rust'), highlightCode(source, 'rs')])
    expect(alias).toBe(rust)
    expect(rust).toContain('github-dark')
    expect(new Set([...rust.matchAll(/--shiki-light:([^;" ]+)/g)].map(match => match[1])).size).toBeGreaterThan(1)
  })
})
