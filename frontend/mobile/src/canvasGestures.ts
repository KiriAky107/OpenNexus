import { CanvasModel, movementIds, type CanvasNode } from '../../src/features/editor/canvasModel'

export interface CanvasPoint { x: number; y: number }
export interface CanvasViewport { x: number; y: number; zoom: number }
export type CanvasGestureTarget = { kind: 'pan' } | { kind: 'move'; ids: string[] } | { kind: 'resize'; id: string }
type Drag = { kind: 'drag'; pointer: number; start: CanvasPoint; target: CanvasGestureTarget; view: CanvasViewport }
type Pinch = { kind: 'pinch'; ids: [number, number]; distance: number; anchor: CanvasPoint; zoom: number }

/** Previews are transient. A completed one-finger gesture makes one model transaction. */
export class CanvasGestures {
  readonly pointers = new Map<number, CanvasPoint>()
  viewport: CanvasViewport = { x: 0, y: 0, zoom: 1 }
  preview = new Map<string, CanvasNode>()
  private gesture: Drag | Pinch | null = null
  private suspended = false
  private delta: CanvasPoint = { x: 0, y: 0 }
  constructor(readonly model: CanvasModel, private readonly changed: (source: string) => void) {}
  world(point: CanvasPoint): CanvasPoint {
    return { x: (point.x - this.viewport.x) / this.viewport.zoom, y: (point.y - this.viewport.y) / this.viewport.zoom }
  }
  down(id: number, point: CanvasPoint, target: CanvasGestureTarget, locked = false): boolean {
    if (locked || !Number.isFinite(point.x) || !Number.isFinite(point.y) || this.pointers.has(id)) return false
    this.pointers.set(id, point)
    if (this.pointers.size === 1 && !this.suspended) {
      this.gesture = { kind: 'drag', pointer: id, start: point, target, view: { ...this.viewport } }
      this.delta = { x: 0, y: 0 }
    } else if (this.pointers.size === 2 && !this.suspended) {
      const entries = [...this.pointers.entries()], a = entries[0]!, b = entries[1]!
      const center = { x: (a[1].x + b[1].x) / 2, y: (a[1].y + b[1].y) / 2 }
      // A second finger cancels any node preview before owning zoom. No partial drag is saved.
      this.preview = new Map()
      this.gesture = { kind: 'pinch', ids: [a[0], b[0]], distance: Math.max(1, Math.hypot(a[1].x - b[1].x, a[1].y - b[1].y)), anchor: this.world(center), zoom: this.viewport.zoom }
    } else {
      this.gesture = null; this.preview = new Map(); this.suspended = true
    }
    return true
  }
  move(id: number, point: CanvasPoint) {
    if (!this.pointers.has(id) || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return
    this.pointers.set(id, point)
    const gesture = this.gesture
    if (!gesture || this.suspended) return
    if (gesture.kind === 'pinch') {
      const a = this.pointers.get(gesture.ids[0]), b = this.pointers.get(gesture.ids[1]); if (!a || !b) return
      const zoom = Math.min(4, Math.max(0.15, gesture.zoom * Math.hypot(a.x - b.x, a.y - b.y) / gesture.distance))
      this.viewport = { zoom, x: (a.x + b.x) / 2 - gesture.anchor.x * zoom, y: (a.y + b.y) / 2 - gesture.anchor.y * zoom }
      return
    }
    if (id !== gesture.pointer) return
    const dx = point.x - gesture.start.x, dy = point.y - gesture.start.y
    if (gesture.target.kind === 'pan') {
      this.viewport = { ...gesture.view, x: gesture.view.x + dx, y: gesture.view.y + dy }; return
    }
    this.delta = { x: Math.round(dx / gesture.view.zoom), y: Math.round(dy / gesture.view.zoom) }
    this.preview = new Map()
    if (gesture.target.kind === 'move') {
      const ids = new Set(movementIds(this.model.document.nodes, gesture.target.ids))
      for (const node of this.model.document.nodes) if (ids.has(node.id)) this.preview.set(node.id, { ...node, x: node.x + this.delta.x, y: node.y + this.delta.y })
    } else {
      const id = gesture.target.id
      const node = this.model.document.nodes.find(node => node.id === id)
      if (node) this.preview.set(node.id, { ...node, width: Math.max(80, node.width + this.delta.x), height: Math.max(60, node.height + this.delta.y) })
    }
  }
  up(id: number) {
    if (!this.pointers.has(id)) return false
    const gesture = this.gesture, preview = this.preview
    this.pointers.delete(id); this.preview = new Map(); this.gesture = null
    let changed = false
    try {
      if (!this.suspended && gesture?.kind === 'drag' && gesture.pointer === id && gesture.target.kind !== 'pan' && (this.delta.x || this.delta.y)) {
        if (gesture.target.kind === 'move') changed = this.model.move(gesture.target.ids, this.delta.x, this.delta.y)
        else {
          const node = preview.get(gesture.target.id)
          if (node) changed = this.model.updateNode(node.id, { width: node.width, height: node.height })
        }
        if (changed) this.changed(this.model.source)
      }
      return changed
    } finally {
      // After pinch, lifting one finger never turns the remaining finger into an accidental drag.
      this.suspended = this.pointers.size > 0
    }
  }
  cancel() {
    this.gesture = null; this.preview = new Map(); this.pointers.clear(); this.suspended = false; this.delta = { x: 0, y: 0 }
  }
}
