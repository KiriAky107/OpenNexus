/** 保留 YAML 节点、未知字段及类型；有歧义时只返回预览，不改正文。 */
import { isMap, isScalar, isSeq, parseDocument, type Document } from 'yaml'

export interface PropertyConflict { key: string; current: string; incoming: string }
export interface ImportPreview { content: string | null; conflicts: PropertyConflict[] }
export type PropertyChoices = Record<string, 'current' | 'incoming'>

function block(source: string) {
  const match = source.match(/^\uFEFF?(---|\*\*\*)[ \t]*\r?\n([\s\S]*?)\r?\n(?:---+|\.\.\.)[ \t]*(?:\r?\n|$)/)
  if (!match) return null
  const document = parseDocument(match[2]!, { uniqueKeys: true })
  if (document.errors.length || document.warnings.length || !isMap(document.contents)) throw new Error('属性 YAML 无法无损处理，请保留原文在源码中修改。')
  if (match[1] === '***' && !document.has('title') && !document.has('tags')) return null
  return { prefix: match[0], document }
}

function normalizeTags(document: Document) {
  if (!document.has('tags')) return
  const previous = document.get('tags', true)
  let values: string[]
  if (isScalar(previous) && typeof previous.value === 'string') values = previous.value.split(/[,，]/).map(value => value.trim()).filter(Boolean)
  else if (isScalar(previous) && previous.value === null) values = []
  else if (isSeq(previous) && previous.items.every(item => isScalar(item) && typeof item.value === 'string' && !item.anchor)) values = previous.items.map(item => String((item as { value: string }).value))
  else throw new Error('标签结构不支持无损转换，已保留原文。')
  const replacement = document.createNode([...new Set(values)])
  if (isScalar(previous) || isSeq(previous)) {
    replacement.anchor = previous.anchor; replacement.comment = previous.comment; replacement.commentBefore = previous.commentBefore
  }
  document.set('tags', replacement)
}

export function previewPropertyImport(source: string, selection?: { from: number; to: number }, choices: PropertyChoices = {}): ImportPreview {
  if (source.length > 5 * 1024 * 1024) throw new Error('文档过大，请缩小属性选区。')
  const selected = selection && selection.from !== selection.to ? selection : undefined
  const start = selected?.from ?? 0, end = selected?.to ?? source.length
  if (start < 0 || end > source.length || start >= end) throw new Error('选区无效')
  // 选区必须从完整行开始，且不能位于代码围栏内。
  if (start && source[start - 1] !== '\n') throw new Error('请选择完整属性块')
  let fence: string | null = null
  for (const line of source.slice(0, start).split(/\r?\n/)) {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/)
    if (marker) {
      if (!fence) fence = marker[1]!
      else if (marker[1]![0] === fence[0] && marker[1]!.length >= fence.length) fence = null
    }
  }
  if (fence) throw new Error('代码块中的文本不会作为笔记属性导入')
  const incoming = block(source.slice(start, end))
  if (!incoming) throw new Error('未识别到完整的标准或历史属性块')
  if (selected && source.slice(start + incoming.prefix.length, end).trim()) throw new Error('选区包含属性块以外的正文')
  const existing = start > 0 ? block(source) : null
  if (existing && start < existing.prefix.length) throw new Error('选区与已有属性块重叠')
  const document = existing ? existing.document.clone() : incoming.document.clone()
  const conflicts: PropertyConflict[] = []
  if (existing) {
    // 跨文档别名的归属不明确，不能在合并时悄悄改变指向。
    if (/[&*][\w-]+/.test(incoming.document.toString()) || /[&*][\w-]+/.test(existing.document.toString())) throw new Error('含 YAML 锚点的多个属性块请先在源码中合并')
    for (const pair of (incoming.document.contents as NonNullable<typeof incoming.document.contents> & { items: { key: unknown }[] }).items) {
      if (!isScalar(pair.key) || typeof pair.key.value !== 'string') throw new Error('属性键必须是字符串')
      const key = pair.key.value
      const node = incoming.document.get(key, true)
      if (document.has(key) && JSON.stringify(document.get(key)) !== JSON.stringify(incoming.document.get(key))) {
        conflicts.push({ key, current: String(document.get(key, true)), incoming: String(node) })
        if (!choices[key]) continue
        if (choices[key] === 'current') continue
      }
      document.set(key, node)
    }
  }
  if (conflicts.some(conflict => !choices[conflict.key])) return { content: null, conflicts }
  normalizeTags(document)
  // 校验别名引用数量和最终文档，无法解析时不返回候选正文。
  document.toJS({ maxAliasCount: 50 })
  const body = existing
    ? source.slice(existing.prefix.length, start) + source.slice(start + incoming.prefix.length)
    : source.slice(0, start) + source.slice(start + incoming.prefix.length)
  const newline = source.includes('\r\n') ? '\r\n' : '\n'
  const prefix = `---\n${document.toString()}---\n`.replace(/\n/g, newline)
  return { content: (source.startsWith('\uFEFF') ? '\uFEFF' : '') + prefix + body.replace(/^\uFEFF/, ''), conflicts }
}
