/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const globalStyles = [
  new URL('./tokens.css', import.meta.url),
  new URL('./features.css', import.meta.url),
].map((path) => readFileSync(path, 'utf8')).join('\n')

const markdownStyles = [
  new URL('../features/editor/VisualMarkdownEditor.vue', import.meta.url),
  new URL('../features/themes/ThemesView.vue', import.meta.url),
  new URL('../components/common/MarkdownContent.vue', import.meta.url),
].map((path) => readFileSync(path, 'utf8')).join('\n')

describe('轻量动效基线', () => {
  it('为减少动态效果偏好提供全局回退', () => {
    expect(globalStyles).toContain('@media (prefers-reduced-motion: reduce)')
  })

  it('不使用全属性过渡或高成本模糊滤镜', () => {
    expect(globalStyles).not.toMatch(/transition:\s*all\b/)
    expect(globalStyles).not.toMatch(/(?:backdrop-)?filter\s*:/)
  })

  it('页面入场只改变透明度和变换', () => {
    const pageAnimation = globalStyles.match(/@keyframes page-in\s*{[\s\S]*?\n}/)?.[0] ?? ''
    expect(pageAnimation).toContain('opacity')
    expect(pageAnimation).toContain('transform')
    expect(pageAnimation).not.toMatch(/(?:width|height|margin|padding|top|left)\s*:/)
  })

  it('Markdown 序号和表格使用独立的高对比度主题变量', () => {
    expect(globalStyles).toContain('--color-markdown-grid:')
    expect(globalStyles).toContain('--color-markdown-marker:')
    expect(markdownStyles).toContain('var(--color-markdown-grid)')
    expect(markdownStyles).toContain('var(--color-markdown-marker)')
  })

  it('不混用可能丢失后代选择器的 scoped global 写法', () => {
    expect(markdownStyles).not.toMatch(/:global\([^\n]+\)\s+\./)
  })
})
