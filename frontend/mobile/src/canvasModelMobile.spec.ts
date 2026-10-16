import { expect, it } from 'vitest'
import { CanvasModel } from '../../src/features/editor/canvasModel'
const raw = '{\r\n"nodes":[{"id":"a","type":"text","x":0,"y":0,"width":300,"height":180,"text":"原文","extension":true}],"edges":[],"original":true}\r\n'
it('coalesces an explicitly owned property input into one undo while keeping the original bytes', () => {
  const model = new CanvasModel(raw)
  model.updateNode('a', { text: '原文1' }, 'field-session')
  model.updateNode('a', { text: '原文12' }, 'field-session')
  model.updateNode('a', { text: '原文123' }, 'field-session')
  expect(model.undo()).toBe(true); expect(model.source).toBe(raw); expect(model.canUndo).toBe(false)
  model.redo(); expect(model.document.nodes[0]?.text).toBe('原文123')
  expect(model.document.nodes[0]?.extension).toBe(true); expect(model.document.original).toBe(true)
})
it('separates a gesture and a new input owner, and invalid property values cannot destroy the previous undo', () => {
  const model = new CanvasModel(raw)
  model.updateNode('a', { text: 'first' }, 'first-field')
  expect(() => model.updateNode('a', { width: 0 }, 'first-field')).toThrow('CANVAS_INVALID')
  model.move(['a'], 20, 30)
  model.updateNode('a', { text: 'second' }, 'second-field')
  model.undo(); expect(model.document.nodes[0]).toMatchObject({ x: 20, text: 'first' })
  model.undo(); expect(model.document.nodes[0]).toMatchObject({ x: 0, text: 'first' })
  model.undo(); expect(model.source).toBe(raw)
})
