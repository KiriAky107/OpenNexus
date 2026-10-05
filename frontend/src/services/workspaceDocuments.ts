export type WorkspaceDocumentType = 'markdown' | 'canvas' | 'image' | 'experiment' | 'unsupported'

export function isExperimentWorkspacePath(path: string): boolean {
  return path.replace(/^\/+/, '').split('/')[0]?.toLowerCase() === 'experiments'
}

export function workspaceDocumentType(path: string): WorkspaceDocumentType {
  if (/\.md$/i.test(path)) return 'markdown'
  if (/\.canvas$/i.test(path)) return 'canvas'
  if (/\.(png|jpe?g|gif|webp)$/i.test(path)) return 'image'
  if (isExperimentWorkspacePath(path) && /\.(py|json|csv)$/i.test(path)) return 'experiment'
  return 'unsupported'
}

export const MAX_CANVAS_BYTES = 4 * 1024 * 1024
export const MAX_EXPERIMENT_EDITOR_BYTES = 2 * 1024 * 1024
export const EMPTY_CANVAS = '{\n  "nodes": [],\n  "edges": []\n}\n'

/** JSON Canvas file/background fields are raw vault-relative paths, never URLs. */
export function resolveCanvasFile(value: unknown): string | null {
  if (typeof value !== 'string' || !value || /[\\:*?"<>|\x00-\x1f\x7f-\x9f]/.test(value)) return null
  const valid = value.split('/').every(part => part.length > 0 && !part.startsWith('.') && !/[. ]$/.test(part)
    && part.toLowerCase() !== 'opennexus-records' && !/^(?:CON|PRN|AUX|NUL|COM\d|LPT\d)(?:\.|$)/i.test(part))
  return valid ? `/${value}` : null
}

export function validateCanvasContent(content: string): void {
  if (new TextEncoder().encode(content).length > MAX_CANVAS_BYTES) throw new Error('CANVAS_TOO_LARGE')
  let value: unknown
  try { value = JSON.parse(content) } catch { throw new Error('CANVAS_INVALID') }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('CANVAS_INVALID')
  const root = value as Record<string, unknown>
  const nodes = root.nodes === undefined ? [] : root.nodes
  const edges = root.edges === undefined ? [] : root.edges
  if (!Array.isArray(nodes) || !Array.isArray(edges)) throw new Error('CANVAS_INVALID')
  if (nodes.length > 2000 || edges.length > 4000) throw new Error('CANVAS_TOO_COMPLEX')
  const ids = new Set<string>()
  const bounded = (value: unknown, min: number, max: number) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
  const optional = (item: Record<string, unknown>, key: string, valid: (value: unknown) => boolean) => item[key] === undefined || valid(item[key])
  const color = (value: unknown) => typeof value === 'string' && /^(?:[1-6]|#[\da-f]{6})$/i.test(value)
  const vaultPath = (value: unknown) => resolveCanvasFile(value) !== null
  for (const item of nodes) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('CANVAS_INVALID')
    const node = item as Record<string, unknown>
    if (typeof node.id !== 'string' || !node.id || node.id.length > 128 || ids.has(node.id)
      || !bounded(node.x, -1_000_000, 1_000_000) || !bounded(node.y, -1_000_000, 1_000_000)
      || !bounded(node.width, 1, 100_000) || !bounded(node.height, 1, 100_000)) throw new Error('CANVAS_INVALID')
    ids.add(node.id)
    if (!optional(node, 'color', color)
      || !optional(node, 'subpath', value => typeof value === 'string' && value.startsWith('#'))
      || !optional(node, 'label', value => typeof value === 'string')
      || !optional(node, 'background', vaultPath)
      || !optional(node, 'backgroundStyle', value => typeof value === 'string' && ['cover', 'ratio', 'repeat'].includes(value))) throw new Error('CANVAS_INVALID')
    if (node.type === 'text' && typeof node.text === 'string') continue
    if (node.type === 'file' && vaultPath(node.file)) continue
    if (node.type === 'link' && typeof node.url === 'string' && /^https?:\/\//i.test(node.url) && !/\s/.test(node.url)) {
      try { const url = new URL(node.url); if (url.hostname && !url.username && !url.password && url.port !== '0') continue } catch { /* invalid URL */ }
    }
    if (node.type === 'group') continue
    throw new Error('CANVAS_INVALID')
  }
  const edgeIds = new Set<string>()
  for (const item of edges) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('CANVAS_INVALID')
    const edge = item as Record<string, unknown>
    if (typeof edge.id !== 'string' || !edge.id || edge.id.length > 128 || edgeIds.has(edge.id)
      || typeof edge.fromNode !== 'string' || !ids.has(edge.fromNode)
      || typeof edge.toNode !== 'string' || !ids.has(edge.toNode)) throw new Error('CANVAS_INVALID')
    edgeIds.add(edge.id)
    if (!optional(edge, 'color', color) || !optional(edge, 'label', value => typeof value === 'string')
      || !['fromSide', 'toSide'].every(key => optional(edge, key, value => typeof value === 'string' && ['left', 'right', 'top', 'bottom'].includes(value)))
      || !['fromEnd', 'toEnd'].every(key => optional(edge, key, value => typeof value === 'string' && ['none', 'arrow'].includes(value)))) throw new Error('CANVAS_INVALID')
  }
}
