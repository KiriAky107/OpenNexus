import { marked } from 'marked'
import { splitNoteMetadata } from '../editor/noteMetadata'

export interface OutlineHeading { index: number; level: number; title: string; offset: number }

export function noteOutline(source: string): OutlineHeading[] {
  const headings: OutlineHeading[] = []
  const metadata = splitNoteMetadata(source)
  let offset = metadata?.prefix.length ?? 0
  let headingIndex = 0
  for (const token of marked.lexer(metadata?.body ?? source)) {
    const start = source.indexOf(token.raw, offset)
    if (token.type === 'heading') {
      const index = headingIndex++
      headings.push({ index, level: token.depth, title: token.text.replace(/[*_`]/g, ''), offset: Math.max(0, start) })
    }
    if (start >= 0) offset = start + token.raw.length
  }
  return headings
}
