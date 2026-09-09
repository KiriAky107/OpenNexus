import { isMap, isScalar, isSeq, parseDocument } from 'yaml'

export interface NoteMetadata {
  prefix: string
  yaml: string
  body: string
  title: string
  tags: string[]
  hasTags: boolean
}

function parseProperties(yaml: string) {
  const document = parseDocument(yaml)
  // 不支持的 YAML 在源模式下保持可用，无需部分重写。
  if (document.errors.length || document.warnings.length || !isMap(document.contents)) return null
  return document
}

export function splitNoteMetadata(source: string): NoteMetadata | null {
  const match = source.match(/^\uFEFF?(---|\*\*\*)[ \t]*\r?\n([\s\S]*?)\r?\n(?:-{3,}|\.\.\.)[ \t]*(?:\r?\n|$)/)
  if (!match) return null
  const yaml = match[2]!
  const document = parseProperties(yaml)
  if (!document || (!document.has('title') && !document.has('tags'))) return null
  const title = document.get('title') ?? ''
  if (typeof title !== 'string') return null
  const tagNode = document.get('tags', true)
  let tags: string[] = []
  if (isSeq(tagNode)) {
    // 不要删除其他属性可能引用的锚定列表项。
    if (!tagNode.items.every(item => isScalar(item) && typeof item.value === 'string' && !item.anchor)) return null
    tags = tagNode.items.map(item => (item as { value: string }).value)
  } else if (isScalar(tagNode)) {
    if (typeof tagNode.value === 'string') tags = tagNode.value.split(',').map(tag => tag.trim()).filter(Boolean)
    else if (tagNode.value !== null) return null
  } else if (tagNode !== undefined) return null
  return { prefix: match[0], yaml, body: source.slice(match[0].length), title, tags, hasTags: document.has('tags') }
}

export function updateMetadataTags(metadata: NoteMetadata, tags: string[]): string {
  const document = parseProperties(metadata.yaml)
  if (!document) throw new Error('Invalid note metadata')
  const previous = document.get('tags', true)
  const replacement = document.createNode([...new Set(tags)])
  if (isScalar(previous) || isSeq(previous)) {
    replacement.anchor = previous.anchor
    replacement.comment = previous.comment
    replacement.commentBefore = previous.commentBefore
  }
  document.set('tags', replacement)
  const newline = metadata.prefix.includes('\r\n') ? '\r\n' : '\n'
  const prefix = `---\n${document.toString()}---\n`.replace(/\n/g, newline)
  return (metadata.prefix.startsWith('\uFEFF') ? '\uFEFF' : '') + prefix
}
