import { describe, expect, it } from 'vitest'
import { highlightCode } from './markdown'

describe('Shiki GitHub 双主题', () => {
  it('一次渲染同时生成 GitHub Light 和 GitHub Dark 颜色变量', async () => {
    const html = await highlightCode('const answer = 42', 'typescript')

    expect(html).toContain('github-light')
    expect(html).toContain('github-dark')
    expect(html).toContain('--shiki-light')
    expect(html).toContain('--shiki-dark')
  })
})
