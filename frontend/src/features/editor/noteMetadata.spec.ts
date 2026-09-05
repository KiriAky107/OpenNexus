import { expect, it } from 'vitest'
import { splitNoteMetadata, updateMetadataTags } from './noteMetadata'

it('renders legacy properties and saves real frontmatter without losing other fields', () => {
  const note = '***\n\ntitle: Python\ntags: python, 编程\nembedding_local_only: true\n----------------\n\n# 正文\n'
  const metadata = splitNoteMetadata(note)!
  expect(metadata.tags).toEqual(['python', '编程'])
  expect(metadata.body).toBe('\n# 正文\n')
  const prefix = updateMetadataTags(metadata, ['编程', '学习', '学习'])
  expect(prefix).toContain('embedding_local_only: true')
  expect(prefix).toContain('tags: ["编程","学习"]')
  expect(prefix.startsWith('---\n')).toBe(true)
  expect(splitNoteMetadata(prefix + metadata.body)!.tags).toEqual(['编程', '学习'])
})

it('does not mistake ordinary Markdown for metadata', () => {
  expect(splitNoteMetadata('---\nA paragraph\n---\n')).toBeNull()
})
