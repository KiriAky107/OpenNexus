import { edgeGeometry, type CanvasDocument, type CanvasNode, type CanvasEdge } from './canvasModel'

export type Geometry = NonNullable<ReturnType<typeof edgeGeometry>>
export interface GeometryItem { edge: CanvasEdge; geometry: Geometry | null }
export interface ViewBounds { left: number; right: number; top: number; bottom: number }
export function intersects(bounds: ViewBounds, view: ViewBounds) {
  return bounds.right >= view.left && bounds.left <= view.right && bounds.bottom >= view.top && bounds.top <= view.bottom
}
function same(a: CanvasNode | undefined, b: CanvasNode | undefined) {
  return a === b || Boolean(a && b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height)
}

/** One immutable document index, with temporary geometry only for gesture edges. */
export class CanvasGeometry {
  readonly nodes = new Map<string, CanvasNode>()
  readonly order = new Map<string, number>()
  readonly incident = new Map<string, Set<string>>()
  readonly edges = new Map<string, CanvasEdge>()
  readonly stats = { indexedNodes: 0, endpointLookups: 0, calculations: 0 }
  private base = new Map<string, GeometryItem>()
  private projected = new Map<string, GeometryItem>()
  private previous: ReadonlyMap<string, CanvasNode> = new Map()
  private result: GeometryItem[]
  constructor(readonly document: CanvasDocument) {
    document.nodes.forEach((node, index) => {
      const id = node.id
      this.nodes.set(id, node); this.order.set(id, index); this.stats.indexedNodes++
    })
    for (const edge of document.edges) {
      this.edges.set(edge.id, edge)
      for (const id of [edge.fromNode, edge.toNode]) {
        if (!this.incident.has(id)) this.incident.set(id, new Set())
        this.incident.get(id)!.add(edge.id)
      }
      this.base.set(edge.id, { edge, geometry: this.calculate(edge, this.nodes) })
    }
    this.projected = new Map(this.base)
    this.result = [...this.base.values()]
  }
  private calculate(edge: CanvasEdge, nodes: ReadonlyMap<string, CanvasNode>) {
    this.stats.endpointLookups += 2; this.stats.calculations++
    return edgeGeometry(edge, nodes)
  }
  project(overrides: ReadonlyMap<string, CanvasNode>) {
    const changed = new Set<string>()
    for (const [id, node] of overrides) if (!same(node, this.previous.get(id))) changed.add(id)
    for (const [id, node] of this.previous) if (!same(node, overrides.get(id))) changed.add(id)
    const affected = new Set<string>()
    for (const id of changed) for (const edgeId of this.incident.get(id) || []) affected.add(edgeId)
    for (const id of affected) {
      const item = this.base.get(id)!, edge = item.edge
      const from = overrides.get(edge.fromNode), to = overrides.get(edge.toNode)
      if (!from && !to) this.projected.set(id, item)
      else {
        const endpoints = new Map<string, CanvasNode>()
        const a = from || this.nodes.get(edge.fromNode), b = to || this.nodes.get(edge.toNode)
        if (a) endpoints.set(edge.fromNode, a)
        if (b) endpoints.set(edge.toNode, b)
        this.projected.set(id, { edge, geometry: this.calculate(edge, endpoints) })
      }
    }
    this.previous = overrides
    if (affected.size) this.result = this.document.edges.map(edge => this.projected.get(edge.id)!)
    return this.result
  }
}
