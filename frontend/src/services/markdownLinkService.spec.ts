// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resolveNoteLink } from './markdownLinkService'
import * as desktop from './platform/desktop'

afterEach(() => vi.restoreAllMocks())

describe('resolveNoteLink', () => {
  it('resolves relative notes and decoded headings inside the vault', () => {
    expect(resolveNoteLink('../算法/双指针.md#复杂度%20分析', '/课程/数组/三数和.md')).toEqual({
      path: '/课程/算法/双指针.md',
      fragment: '复杂度 分析',
    })
  })

  it('keeps a fragment in the current note and blocks traversal outside the vault', () => {
    expect(resolveNoteLink('#结论', '/课程/笔记.md')).toEqual({ path: '/课程/笔记.md', fragment: '结论' })
    expect(resolveNoteLink('../../../outside.md', '/课程/笔记.md')).toBeNull()
  })

  it('does not treat web and non-markdown assets as notes', () => {
    expect(resolveNoteLink('https://example.com/a.md', '/note.md')).toBeNull()
    expect(resolveNoteLink('./audio.mp3', '/note.md')).toBeNull()
  })

  it('opens scoped experiment files from Markdown only in the desktop app', () => {
    expect(resolveNoteLink('../experiments/main.py#section', '/notes/readme.md')).toBeNull()
    vi.spyOn(desktop, 'isDesktop').mockReturnValue(true)
    expect(resolveNoteLink('../experiments/main.py#section%202', '/notes/readme.md')).toEqual({
      path: '/experiments/main.py', fragment: 'section 2',
    })
    expect(resolveNoteLink('../experiments/input.json', '/notes/readme.md')).toEqual({
      path: '/experiments/input.json', fragment: '',
    })
    expect(resolveNoteLink('../settings.json', '/notes/readme.md')).toBeNull()
    expect(resolveNoteLink('../experiments/runner.exe', '/notes/readme.md')).toBeNull()
  })
})
