import { workspaceDocumentType } from './workspaceDocuments'

export interface VaultReference {
  source: string
  raw: string
  target: string | null
  kind: 'markdown' | 'image' | 'wiki' | 'canvas-file' | 'html'
  status: 'resolved' | 'missing' | 'ambiguous' | 'external' | 'unsafe' | 'unsupported'
  start?: number
  end?: number
  suffix?: string
  nodeId?: string
}

function safeParts(parts: string[]): boolean {
  return parts.every(part => part !== '' && part !== '.' && part !== '..' && !part.startsWith('.') && !/[\\:*?"<>|\0]/.test(part) && part.toLowerCase() !== 'opennexus-records')
}

/** Resolve a link into a vault-root path, never a host filesystem path. */
export function resolveVaultReference(source: string, raw: string, rootRelative = false): string | null {
  const href = raw.trim()
  if (!href || href.startsWith('#') || /^\/\//.test(href) || /^[a-z][a-z\d+.-]*:/i.test(href)) return null
  const path = href.split(/[?#]/, 1)[0]!
  let decoded: string
  try { decoded = decodeURIComponent(path) } catch { return null }
  if (!decoded || decoded.includes('\\') || decoded.includes('\0')) return null
  const parts = rootRelative || decoded.startsWith('/') ? [] : source.replace(/^\//, '').split('/').slice(0, -1)
  for (const part of decoded.replace(/^\//, '').split('/')) {
    if (!part || part === '.') continue
    if (part === '..') { if (!parts.length) return null; parts.pop() }
    else parts.push(part)
  }
  return parts.length && safeParts(parts) ? `/${parts.join('/')}` : null
}

function splitSuffix(raw: string): [string, string] {
  const index = raw.search(/[?#]/)
  return index < 0 ? [raw, ''] : [raw.slice(0, index), raw.slice(index)]
}

function reference(source: string, raw: string, kind: VaultReference['kind'], paths: Set<string>, start?: number, end?: number): VaultReference {
  if (/^https?:\/\//i.test(raw)) return { source, raw, target: null, kind, status: 'external', start, end }
  if (/^(mailto|tel):/i.test(raw)) return { source, raw, target: null, kind, status: 'unsupported', start, end }
  const [path, suffix] = splitSuffix(raw)
  const target = !path && suffix.startsWith('#') ? source : resolveVaultReference(source, path, kind === 'canvas-file')
  return { source, raw, target, kind, status: !target ? 'unsafe' : paths.has(target) ? 'resolved' : workspaceDocumentType(target) === 'unsupported' ? 'unsupported' : 'missing', start, end, suffix }
}

export function scanVaultReferences(source: string, content: string, paths: Set<string>): VaultReference[] {
  if (workspaceDocumentType(source) === 'canvas') {
    const document = JSON.parse(content) as { nodes?: Array<Record<string, unknown>> }
    return (document.nodes ?? []).filter(node => node.type === 'file' && typeof node.file === 'string')
      .map(node => ({ ...reference(source, String(node.file), 'canvas-file', paths), nodeId: String(node.id) }))
  }
  if (workspaceDocumentType(source) !== 'markdown') return []
  const results: VaultReference[] = []
  let offset = 0
  let fence = false
  for (const line of content.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence
    else if (!fence) {
      const inlineCode = (index: number) => (line.slice(0, index).match(/`/g)?.length ?? 0) % 2 === 1
      for (const match of line.matchAll(/(!?\[[^\]\n]*\])\((?:<([^>\n]+)>|([^\s)\n]+))(?:\s+["'][^\n]*?["'])?\)/g)) {
        const raw = (match[2] ?? match[3])!
        const start = offset + match.index! + match[1]!.length + 1 + (match[2] === undefined ? 0 : 1)
        if (!inlineCode(match.index!)) results.push(reference(source, raw, match[1]!.startsWith('!') ? 'image' : 'markdown', paths, start, start + raw.length))
      }
      for (const match of line.matchAll(/\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]/g)) {
        if (inlineCode(match.index!)) continue
        const raw = match[1]!
        const [name, suffix] = splitSuffix(raw)
        const matches = [...paths].filter(path => workspaceDocumentType(path) === 'markdown' && (path === `/${name.replace(/^\//, '')}` || path === `/${name.replace(/^\//, '')}.md` || path.split('/').at(-1) === (name.endsWith('.md') ? name : `${name}.md`)))
        results.push({ source, raw, kind: 'wiki', target: matches.length === 1 ? matches[0]! : null,
          status: matches.length === 1 ? 'resolved' : matches.length ? 'ambiguous' : 'missing',
          suffix, start: offset + match.index! + 2, end: offset + match.index! + 2 + raw.length })
      }
      for (const match of line.matchAll(/<(?:img|a)\b[^>]*?\b(?:src|href)=["']([^"']+)["'][^>]*>/gi)) {
        if (inlineCode(match.index!)) continue
        const raw = match[1]!
        const start = offset + match.index! + match[0].indexOf(raw)
        results.push(reference(source, raw, 'html', paths, start, start + raw.length))
      }
      const definition = line.match(/^\s{0,3}\[[^\]\n]+\]:\s*<?([^\s>]+)>?(?:\s+["'(].*)?$/)
      if (definition) {
        const raw = definition[1]!
        const start = offset + line.indexOf(raw)
        results.push(reference(source, raw, 'markdown', paths, start, start + raw.length))
      }
    }
    offset += line.length + 1
  }
  return results
}

/** Rebase both incoming targets and outgoing links when a document/folder moves. */
export function rewritePathReferences(source: string, content: string, oldPath: string, newPath: string, paths: Set<string>) {
  const moved = (path: string) => path === oldPath || path.startsWith(`${oldPath}/`)
  const destination = (path: string) => moved(path) ? `${newPath}${path.slice(oldPath.length)}` : path
  const newSource = destination(source)
  const references = scanVaultReferences(source, content, paths)
  const changed = references.filter(ref => ref.status === 'resolved' && ref.target && (moved(ref.target) || (moved(source) && ref.kind !== 'wiki' && ref.kind !== 'canvas-file')))
  const pending = references.filter(ref => ref.status === 'ambiguous' || (ref.status !== 'resolved' && moved(source) && !['external', 'unsupported'].includes(ref.status)))
  const replacement = (ref: VaultReference) => {
    const target = destination(ref.target!)
    if (ref.raw.startsWith('#') && target === newSource) return ref.raw
    if (ref.kind === 'wiki' || ref.kind === 'canvas-file') return target.slice(1) + (ref.suffix ?? '')
    return encodeURI(relativeFrom(newSource, target)).replace(/#/g, '%23').replace(/\?/g, '%3F') + (ref.suffix ?? '')
  }
  if (workspaceDocumentType(source) === 'canvas') {
    if (!changed.length) return { content, changed, pending }
    const document = JSON.parse(content) as { nodes: Array<Record<string, unknown>> }
    for (const ref of changed) {
      const node = document.nodes.find(node => node.id === ref.nodeId)
      if (node) node.file = replacement(ref)
    }
    return { content: JSON.stringify(document, null, 2) + '\n', changed, pending }
  }
  let updated = content
  for (const ref of [...changed].sort((a,b) => b.start! - a.start!)) updated = updated.slice(0, ref.start!) + replacement(ref) + updated.slice(ref.end!)
  return { content: updated, changed, pending }
}

function relativeFrom(source: string, target: string): string {
  const from = source.replace(/^\//, '').split('/').slice(0, -1)
  const to = target.replace(/^\//, '').split('/')
  while (from.length && to.length && from[0] === to[0]) { from.shift(); to.shift() }
  return `${'../'.repeat(from.length)}${to.join('/')}`
}

/** Only exact resolved links are rewritten; ambiguous/wiki references stay unchanged. */
export function rewriteReferences(source: string, content: string, oldPath: string, newPath: string, paths: Set<string>): { content: string; changed: VaultReference[]; pending: VaultReference[] } {
  const references = scanVaultReferences(source, content, paths).filter(item => item.target === oldPath || (item.kind === 'wiki' && item.status === 'ambiguous'))
  const changed = references.filter(item => item.target === oldPath && item.status === 'resolved' && item.kind !== 'wiki')
  const pending = references.filter(item => !changed.includes(item))
  if (workspaceDocumentType(source) === 'canvas') {
    if (!changed.length) return { content, changed, pending }
    const document = JSON.parse(content) as { nodes: Array<Record<string, unknown>> }
    const ids = new Set(changed.map(item => item.nodeId))
    for (const node of document.nodes) if (ids.has(String(node.id))) node.file = newPath.replace(/^\//, '')
    return { content: JSON.stringify(document, null, 2) + '\n', changed, pending }
  }
  let updated = content
  for (const item of [...changed].sort((left, right) => (right.start ?? 0) - (left.start ?? 0))) {
    const path = item.raw.startsWith('/') ? newPath : relativeFrom(source, newPath)
    updated = updated.slice(0, item.start) + encodeURI(path) + (item.suffix ?? '') + updated.slice(item.end)
  }
  return { content: updated, changed, pending }
}
