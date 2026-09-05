import { expect, it } from 'vitest'
import { parseDocument } from 'yaml'
import { splitNoteMetadata, updateMetadataTags } from './noteMetadata'

it('renders legacy properties and saves real frontmatter without losing other fields', () => {
  const note = '***\n\ntitle: Python\ntags: python, 编程\nembedding_local_only: true\n----------------\n\n# 正文\n'
  const metadata = splitNoteMetadata(note)!
  expect(metadata.tags).toEqual(['python', '编程'])
  expect(metadata.body).toBe('\n# 正文\n')
  const prefix = updateMetadataTags(metadata, ['编程', '学习', '学习'])
  expect(prefix).toContain('embedding_local_only: true')
  expect(prefix.startsWith('---\n')).toBe(true)
  expect(splitNoteMetadata(prefix + metadata.body)!.tags).toEqual(['编程', '学习'])
})

it('does not mistake ordinary Markdown for metadata', () => {
  expect(splitNoteMetadata('---\nA paragraph\n---\n')).toBeNull()
})

it.each(['- python\n- rust', '  - python\n  - rust', '[python, rust]'])('replaces the complete YAML tag list: %s', (list) => {
  const metadata = splitNoteMetadata(`---\ntitle: Demo\ntags:\n${list.startsWith('[') ? '  ' : ''}${list}\nextra:\n  enabled: true # keep this\n---\n# Body\n`)!
  expect(metadata.tags).toEqual(['python', 'rust'])
  const prefix = updateMetadataTags(metadata, [...metadata.tags, 'new'])
  const updated = splitNoteMetadata(prefix + metadata.body)!
  expect(updated.tags).toEqual(['python', 'rust', 'new'])
  expect(updated.body).toBe('# Body\n')
  const document = parseDocument(updated.yaml)
  expect(document.errors).toEqual([])
  expect(document.toJS().extra).toEqual({ enabled: true })
  expect(prefix).toContain('# keep this')
  expect(splitNoteMetadata(updateMetadataTags(updated, []))!.tags).toEqual([])
})

it('preserves quoted commas, escapes, multiline titles and nested properties', () => {
  const tags = ['a,b', 'quote"tag', 'path\\tag', 'true']
  const metadata = splitNoteMetadata(`---\ntitle: |\n  A multiline\n  title\ntags: ${JSON.stringify(tags)}\nextra: {count: 2, enabled: false}\n---\n正文`)!
  expect(metadata.tags).toEqual(tags)
  const updated = splitNoteMetadata(updateMetadataTags(metadata, tags) + metadata.body)!
  expect(updated.tags).toEqual(tags)
  expect(updated.title).toBe(metadata.title)
  expect(parseDocument(updated.yaml).toJS().extra).toEqual({ count: 2, enabled: false })
})

it('preserves document encoding markers and tag anchors', () => {
  const metadata = splitNoteMetadata('\uFEFF---\r\ntitle: Demo\r\ntags: &labels [python]\r\nrelated: *labels\r\n---\r\nBody')!
  const prefix = updateMetadataTags(metadata, ['rust'])
  expect(prefix.startsWith('\uFEFF---\r\n')).toBe(true)
  expect(prefix.replace(/\r\n/g, '')).not.toContain('\n')
  expect(parseDocument(splitNoteMetadata(prefix)!.yaml).toJS().related).toEqual(['rust'])
})

it.each(['tags: [broken', 'tags: [one]\ntags: [two]', 'tags: {nested: value}', 'tags: [1, true]', 'tags: [&label python]\nother: *label'])('leaves invalid or unsupported tag data in source mode: %s', (yaml) => {
  expect(splitNoteMetadata(`---\ntitle: Demo\n${yaml}\n---\nBody`)).toBeNull()
})
