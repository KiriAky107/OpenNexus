import { describe, expect, it, vi } from 'vitest'
import { CanvasModel } from '../../src/features/editor/canvasModel'
import { CanvasGestures } from './canvasGestures'

const source = '{\r\n"nodes":[{"id":"g","type":"group","x":0,"y":0,"width":600,"height":400,"custom":{"keep":1}},{"id":"a","type":"text","x":40,"y":80,"width":300,"height":180,"text":"中文 😀"}],"edges":[],"extension":{"keep":true}}\r\n'
const setup = () => { const model = new CanvasModel(source), changed = vi.fn(); return { model, changed, gestures: new CanvasGestures(model, changed) } }
describe('owned mobile Canvas gestures', () => {
  it('opening, panning and focal pinch do not rewrite original document bytes or save', () => {
    const { model, changed, gestures: g } = setup()
    g.down(1, { x: 0, y: 0 }, { kind: 'pan' }); g.move(1, { x: 30, y: 40 }); g.up(1)
    expect(g.viewport).toEqual({ x: 30, y: 40, zoom: 1 })
    g.down(2, { x: 30, y: 40 }, { kind: 'pan' }); g.down(3, { x: 130, y: 40 }, { kind: 'pan' })
    g.move(3, { x: 230, y: 40 })
    expect(g.viewport).toEqual({ x: 30, y: 40, zoom: 2 })
    g.up(3); g.move(2, { x: -300, y: -400 }); g.up(2)
    expect(model.source).toBe(source); expect(changed).not.toHaveBeenCalled(); expect(model.canUndo).toBe(false)
  })
  it('previews a group and its members, then commits a single undoable document transaction', () => {
    const { model, changed, gestures: g } = setup(); g.viewport.zoom = 2
    g.down(1, { x: 10, y: 10 }, { kind: 'move', ids: ['g'] }); g.move(1, { x: 50, y: 70 })
    expect(g.preview.get('g')?.x).toBe(20); expect(g.preview.get('a')?.y).toBe(110)
    expect(model.source).toBe(source); expect(changed).not.toHaveBeenCalled()
    expect(g.up(1)).toBe(true); expect(changed).toHaveBeenCalledTimes(1)
    expect(model.document.nodes[0]?.custom).toEqual({ keep: 1 }); expect(model.document.extension).toEqual({ keep: true })
    expect(model.undo()).toBe(true); expect(model.source).toBe(source)
  })
  it('pointer cancellation or backgrounding discards every transient node delta', () => {
    const { model, changed, gestures: g } = setup()
    g.down(1, { x: 0, y: 0 }, { kind: 'move', ids: ['a'] }); g.move(1, { x: 90, y: 20 }); g.cancel()
    expect(g.up(1)).toBe(false); expect(g.preview.size).toBe(0); expect(model.source).toBe(source); expect(changed).not.toHaveBeenCalled()
  })
  it('second touch cancels a pending node drag and prevents a remaining finger from committing it', () => {
    const { model, changed, gestures: g } = setup()
    g.down(1, { x: 0, y: 0 }, { kind: 'move', ids: ['a'] }); g.move(1, { x: 90, y: 20 })
    g.down(2, { x: 100, y: 0 }, { kind: 'pan' }); expect(g.preview.size).toBe(0)
    g.up(2); g.move(1, { x: 800, y: 800 }); g.up(1)
    expect(model.source).toBe(source); expect(changed).not.toHaveBeenCalled()
  })
  it('resizing commits bounded dimensions once and undo restores exact source', () => {
    const { model, changed, gestures: g } = setup()
    g.down(1, { x: 300, y: 180 }, { kind: 'resize', id: 'a' }); g.move(1, { x: -300, y: -180 })
    expect(g.preview.get('a')?.width).toBe(80); g.up(1)
    expect(model.document.nodes[1]?.height).toBe(60); expect(changed).toHaveBeenCalledTimes(1)
    model.undo(); expect(model.source).toBe(source)
  })
  it('IME and read-only ownership cannot start a document gesture, and extra touches cancel previews', () => {
    const { model, changed, gestures: g } = setup()
    expect(g.down(1, { x: 0, y: 0 }, { kind: 'move', ids: ['a'] }, true)).toBe(false)
    expect(g.pointers.size).toBe(0)
    g.down(1, { x: 0, y: 0 }, { kind: 'pan' }); g.down(2, { x: 100, y: 0 }, { kind: 'pan' }); g.down(3, { x: 200, y: 0 }, { kind: 'pan' })
    g.move(3, { x: 1000, y: 0 }); [1, 2, 3].forEach(id => g.up(id))
    expect(g.viewport.zoom).toBe(1); expect(model.source).toBe(source); expect(changed).not.toHaveBeenCalled()
  })
})
