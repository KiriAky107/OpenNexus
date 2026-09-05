// TODO(desktop): 第三阶段顶部「段落 → 导入为笔记属性」复用属性解析边界，
// 补齐无损 YAML、冲突合并与可撤销事务；见 docs/contracts/Tauri-Rust桌面客户端需求说明-第三阶段.md。
export interface NoteMetadata { prefix: string; yaml: string; body: string; title: string; tags: string[] }

export function splitNoteMetadata(source: string): NoteMetadata | null {
  const match = source.match(/^\uFEFF?(---|\*\*\*)[ \t]*\r?\n([\s\S]*?)\r?\n(?:-{3,}|\.\.\.)[ \t]*(?:\r?\n|$)/)
  if (!match) return null
  const yaml = match[2]!
  // Only recognize metadata with explicit fields, not ordinary thematic breaks.
  const title = yaml.match(/^title:[ \t]*(.*)$/m)?.[1]?.trim() ?? ''
  const rawTags = yaml.match(/^tags:[ \t]*(.*)$/m)?.[1]?.trim()
  if (!title && rawTags === undefined) return null
  // Complex YAML values remain editable in source mode, never partially rewritten.
  if (/^(?:[|>]|\{)/.test(title) || (rawTags === '' && /^\s+-\s/m.test(yaml))) return null
  const tags = rawTags?.replace(/^\[|\]$/g, '').split(',').map(tag => tag.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean) ?? []
  return { prefix: match[0], yaml, body: source.slice(match[0].length), title: title.replace(/^['"]|['"]$/g, ''), tags }
}

export function updateMetadataTags(metadata: NoteMetadata, tags: string[]): string {
  const line = `tags: ${JSON.stringify([...new Set(tags)])}`
  const yaml = /^tags:/m.test(metadata.yaml) ? metadata.yaml.replace(/^tags:.*$/m, () => line) : `${metadata.yaml}\n${line}`
  return `---\n${yaml.trim()}\n---\n`
}
