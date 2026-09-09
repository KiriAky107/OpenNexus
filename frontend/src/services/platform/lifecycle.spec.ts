import { describe, expect, it } from 'vitest'
import { resolveEditorShortcut } from './lifecycle'

const key = (code: string, options: Partial<Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>> = {}) =>
  resolveEditorShortcut({ code, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...options })

describe('桌面编辑快捷键', () => {
  it('映射正文与六级标题', () => {
    expect(key('Digit0', { ctrlKey: true })).toEqual({ id: 'editor.paragraph' })
    expect(key('Digit6', { ctrlKey: true })).toEqual({ id: 'editor.heading', params: 6 })
  })

  it('映射格式、列表与警告框', () => {
    expect(key('Digit5', { altKey: true, shiftKey: true })?.id).toBe('editor.strikethrough')
    expect(key('BracketRight', { ctrlKey: true, shiftKey: true })?.id).toBe('editor.bullet-list')
    expect(key('KeyC', { ctrlKey: true, altKey: true })).toMatchObject({ id: 'editor.callout', params: { type: 'note' } })
    expect(key('KeyK', { ctrlKey: true })?.id).toBe('editor.link')
    expect(key('KeyT', { ctrlKey: true, altKey: true })?.id).toBe('editor.table')
  })
})
