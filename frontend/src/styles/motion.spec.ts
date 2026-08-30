/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const globalStyles = [
  new URL('./tokens.css', import.meta.url),
  new URL('./features.css', import.meta.url),
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
})
