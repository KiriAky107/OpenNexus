import { fromMarkdown, type Extension } from 'mdast-util-from-markdown'
import { decodeHTMLAttribute, escapeAttribute } from 'entities'
import { splitNoteMetadata } from '@/utils/noteMetadata'
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
  field?: 'file' | 'background'
  /** A real reference with an unproven source range must never be rewritten. */
  editable?: boolean
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

function reference(source: string, raw: string, kind: VaultReference['kind'], paths: Set<string>, start?: number, end?: number, href = raw): VaultReference {
  if (/^https?:\/\//i.test(href)) return { source, raw, target: null, kind, status: 'external', start, end }
  if (/^(mailto|tel):/i.test(href)) return { source, raw, target: null, kind, status: 'unsupported', start, end }
  const [path, suffix] = splitSuffix(href)
  const target = !path && suffix.startsWith('#') ? source : resolveVaultReference(source, path, kind === 'canvas-file')
  return { source, raw, target, kind, status: !target ? 'unsafe' : paths.has(target) ? 'resolved' : workspaceDocumentType(target) === 'unsupported' ? 'unsupported' : 'missing', start, end, suffix }
}

interface MarkdownNode {
  type: string
  url?: string
  value?: string
  identifier?: string
  children?: MarkdownNode[]
  position?: { start: { offset?: number }; end: { offset?: number } }
}

function escaped(text: string, offset: number): boolean {
  let count = 0
  while (offset > 0 && text[--offset] === '\\') count++
  return count % 2 === 1
}

function markdownReferences(source: string, content: string, paths: Set<string>): VaultReference[] {
  const metadataEnd = splitNoteMetadata(content)?.prefix.length ?? 0
  const ranges = new WeakMap<object, { start: number; end: number }>()
  // Keep the default exit handlers (which decode escapes/entities). Capture the
  // destination token before the parser starts buffering its decoded value.
  const destination: NonNullable<Extension['enter']>[string] = function(token) {
    const owner = this.stack.at(-1)
    if (owner) ranges.set(owner, { start: token.start.offset, end: token.end.offset })
    this.buffer()
  }
  const tree = fromMarkdown(content, { mdastExtensions: [{ enter: {
    resourceDestinationString: destination, definitionDestinationString: destination,
  } }] })
  const results: VaultReference[] = []
  const definitions = new Set<string>()
  let rawTextTag: string | null = null
  const visit = (node: MarkdownNode, insideLink = false) => {
    if (node.type === 'code' || node.type === 'inlineCode') return
    const start = node.position?.start.offset, end = node.position?.end.offset
    if (end !== undefined && end <= metadataEnd) return
    if (rawTextTag && node.type !== 'html') { node.children?.forEach(child => visit(child, insideLink)); return }
    if (node.type === 'link' || node.type === 'image' || node.type === 'definition') {
      if (node.type === 'definition') {
        if (definitions.has(node.identifier!)) return
        definitions.add(node.identifier!)
      }
      const range = ranges.get(node)
      const raw = range ? content.slice(range.start, range.end) : node.url ?? ''
      const ref = reference(source, raw, node.type === 'image' ? 'image' : 'markdown', paths, range?.start, range?.end, node.url)
      ref.editable = !!range && range.end > range.start
      results.push(ref)
      if (node.type === 'link') node.children?.forEach(child => visit(child, true))
      return
    }
    if (!insideLink && start !== undefined && end !== undefined && node.type === 'text') {
      const text = content.slice(start, end)
      for (const match of text.matchAll(/\[\[([^\]|\r\n]+)(?:\|[^\]\r\n]*)?\]\]/g)) {
        if (escaped(text, match.index!)) continue
        const raw = match[1]!, [name, suffix] = splitSuffix(raw)
        const matches = [...paths].filter(path => workspaceDocumentType(path) === 'markdown' && (path === `/${name.replace(/^\//, '')}` || path === `/${name.replace(/^\//, '')}.md` || path.split('/').at(-1) === (name.endsWith('.md') ? name : `${name}.md`)))
        results.push({ source, raw, kind: 'wiki', target: matches.length === 1 ? matches[0]! : null,
          status: matches.length === 1 ? 'resolved' : matches.length ? 'ambiguous' : 'missing',
          suffix, start: start + match.index! + 2, end: start + match.index! + 2 + raw.length })
      }
    }
    if (start !== undefined && end !== undefined && node.type === 'html') {
      let htmlStart = start
      if (rawTextTag) {
        const close = content.slice(start, end).match(new RegExp(`</${rawTextTag}\\s*>`, 'i'))
        if (!close) return
        htmlStart += close.index! + close[0].length
        rawTextTag = null
      }
      const html = content.slice(htmlStart, end)
      // Ignore comments and raw-text elements, including apparent tags in their content.
      const tags = /<!--[\s\S]*?(?:-->|$)|<(script|style|textarea|title|xmp|iframe|noembed|noframes|plaintext)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)|<([a-z][\w:-]*)\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi
      for (const tag of html.matchAll(tags)) {
        if (tag[1] && !new RegExp(`</${tag[1]}\\s*>`, 'i').test(tag[0])) rawTextTag = tag[1].toLowerCase()
        const name = tag[2]?.toLowerCase()
        if (name !== 'a' && name !== 'img') continue
        const attribute = name === 'a' ? 'href' : 'src'
        const attributes = /([^\s=<>"'`]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s<>`]+))?/g
        attributes.lastIndex = tag[0].indexOf(name) + name.length
        for (const match of tag[0].matchAll(attributes)) {
          if (match[1]?.toLowerCase() !== attribute || !match[2]) continue
          const quoted = /^["']/.test(match[2]), raw = quoted ? match[2].slice(1, -1) : match[2]
          const offset = htmlStart + tag.index! + match.index! + match[0].lastIndexOf(match[2]) + (quoted ? 1 : 0)
          results.push(reference(source, raw, 'html', paths, offset, offset + raw.length, decodeHTMLAttribute(raw)))
          break // HTML uses the first attribute when an attribute is duplicated.
        }
      }
    }
    node.children?.forEach(child => visit(child, insideLink))
  }
  visit(tree)
  return results.sort((a, b) => (a.start ?? 0) - (b.start ?? 0))
}

export interface ReferenceEdit { start: number; end: number; before: string; after: string }

export function scanVaultReferences(source: string, content: string, paths: Set<string>): VaultReference[] {
  if (workspaceDocumentType(source) === 'canvas') {
    const document = JSON.parse(content) as { nodes?: Array<Record<string, unknown>> }
    return (document.nodes ?? []).flatMap(node => {
      const field = node.type === 'file' ? 'file' : node.type === 'group' ? 'background' : null
      return field && typeof node[field] === 'string' ? [{ ...reference(source, String(node[field]), 'canvas-file', paths), nodeId: String(node.id), field }] : []
    })
  }
  if (workspaceDocumentType(source) !== 'markdown') return []
  return markdownReferences(source, content, paths)
}

/** Rebase both incoming targets and outgoing links when a document/folder moves. */
export function rewritePathReferences(source: string, content: string, oldPath: string, newPath: string, paths: Set<string>) {
  const moved = (path: string) => path === oldPath || path.startsWith(`${oldPath}/`)
  const destination = (path: string) => moved(path) ? `${newPath}${path.slice(oldPath.length)}` : path
  const newSource = destination(source)
  const references = scanVaultReferences(source, content, paths)
  const affected = (ref: VaultReference) => ref.target && (moved(ref.target) || (moved(source) && ref.kind !== 'wiki' && ref.kind !== 'canvas-file'))
  const changed = references.filter(ref => ref.status === 'resolved' && ref.editable !== false && affected(ref))
  const pending = references.filter(ref => ref.status === 'ambiguous' || (ref.editable === false && affected(ref)) || (ref.status !== 'resolved' && moved(source) && !['external', 'unsupported'].includes(ref.status)))
  const replacement = (ref: VaultReference) => {
    const target = destination(ref.target!)
    if (ref.raw.startsWith('#') && target === newSource) return ref.raw
    if (ref.kind === 'wiki' || ref.kind === 'canvas-file') return target.slice(1) + (ref.suffix ?? '')
    const href = encodeLinkPath(relativeFrom(newSource, target)) + (ref.suffix ?? '')
    return ref.kind === 'html' ? escapeAttribute(href).replace(/'/g, '&#39;') : href
  }
  if (workspaceDocumentType(source) === 'canvas') {
    if (!changed.length) return { content, changed, pending }
    const document = JSON.parse(content) as { nodes: Array<Record<string, unknown>> }
    for (const ref of changed) {
      const node = document.nodes.find(node => node.id === ref.nodeId)
      if (node) node[ref.field ?? 'file'] = replacement(ref)
    }
    return { content: JSON.stringify(document, null, 2) + '\n', changed, pending }
  }
  let updated = content
  const edits: ReferenceEdit[] = changed.map(ref => ({ start: ref.start!, end: ref.end!, before: content.slice(ref.start!, ref.end!), after: replacement(ref) }))
    .filter(edit => edit.before !== edit.after).sort((a, b) => a.start - b.start)
  for (const edit of [...edits].reverse()) updated = updated.slice(0, edit.start) + edit.after + updated.slice(edit.end)
  return { content: updated, changed, pending, edits }
}

function relativeFrom(source: string, target: string): string {
  const from = source.replace(/^\//, '').split('/').slice(0, -1)
  const to = target.replace(/^\//, '').split('/')
  while (from.length && to.length && from[0] === to[0]) { from.shift(); to.shift() }
  return `${'../'.repeat(from.length)}${to.join('/')}`
}

function encodeLinkPath(path: string): string {
  return encodeURI(path).replace(/[#?()'&]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`)
}

/** Only exact resolved links are rewritten; ambiguous/wiki references stay unchanged. */
export function rewriteReferences(source: string, content: string, oldPath: string, newPath: string, paths: Set<string>): { content: string; changed: VaultReference[]; pending: VaultReference[] } {
  const references = scanVaultReferences(source, content, paths).filter(item => item.target === oldPath || (item.kind === 'wiki' && item.status === 'ambiguous'))
  const changed = references.filter(item => item.target === oldPath && item.status === 'resolved' && item.kind !== 'wiki' && item.editable !== false)
  const pending = references.filter(item => !changed.includes(item))
  if (workspaceDocumentType(source) === 'canvas') {
    if (!changed.length) return { content, changed, pending }
    const document = JSON.parse(content) as { nodes: Array<Record<string, unknown>> }
    for (const item of changed) {
      const node = document.nodes.find(node => String(node.id) === item.nodeId)
      if (node) node[item.field ?? 'file'] = newPath.replace(/^\//, '')
    }
    return { content: JSON.stringify(document, null, 2) + '\n', changed, pending }
  }
  let updated = content
  for (const item of [...changed].sort((left, right) => (right.start ?? 0) - (left.start ?? 0))) {
    const path = item.raw.startsWith('/') ? newPath : relativeFrom(source, newPath)
    const href = encodeLinkPath(path) + (item.suffix ?? '')
    updated = updated.slice(0, item.start) + (item.kind === 'html' ? escapeAttribute(href).replace(/'/g, '&#39;') : href) + updated.slice(item.end)
  }
  return { content: updated, changed, pending }
}
