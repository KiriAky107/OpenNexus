import { expect, it } from 'vitest'
import { CanvasModel } from './canvasModel'

const document = { nodes: [{ id: 'a', type: 'text', x: 0, y: 0, width: 100, height: 100, text: '原正文\r\nsecond 😀', custom: [true, null] }], edges: [], extension: { preserved: '原始值' } }
const source = (eol: string) => JSON.stringify(document, null, 2).replace(/\n/g, eol) + eol

it.each(['\r\n', '\n', '\r'])('keeps the original JSON line ending %j across edit, undo and redo', eol => {
  const original = source(eol), model = new CanvasModel(original)
  expect(model.source).toBe(original)
  model.updateNode('a', { color: '4' })
  const edited = model.source
  expect(edited).toContain(eol)
  expect(edited.endsWith(eol)).toBe(true)
  expect(edited.split(eol).join('')).not.toMatch(/[\r\n]/)
  expect(JSON.parse(edited)).toEqual({ ...document, nodes: [{ ...document.nodes[0], color: '4' }] })
  expect(model.undo()).toBe(true); expect(model.source).toBe(original)
  expect(model.redo()).toBe(true); expect(model.source).toBe(edited)
})

it('uses the accepted source line ending after replacement and keeps a no-op byte exact', () => {
  const model = new CanvasModel(source('\n')), accepted = source('\r\n')
  model.acceptSource(accepted)
  expect(model.updateNode('a', { text: document.nodes[0]!.text })).toBe(false)
  expect(model.source).toBe(accepted)
  model.updateNode('a', { label: 'new label' })
  expect(model.source.replace(/\r\n/g, '')).not.toContain('\n')
  expect(JSON.parse(model.source).nodes[0].text).toBe(document.nodes[0]!.text)
  model.undo(); expect(model.source).toBe(accepted)
})

it('keeps the existing LF default for compact JSON without a physical newline', () => {
  const model = new CanvasModel(JSON.stringify(document))
  model.updateNode('a', { color: '6' })
  expect(model.source).toContain('\n'); expect(model.source).not.toContain('\r')
  expect(JSON.parse(model.source).nodes[0].text).toBe(document.nodes[0]!.text)
})
