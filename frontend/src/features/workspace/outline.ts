import { marked } from 'marked'
import { splitNoteMetadata } from '../editor/noteMetadata'

export interface OutlineHeading { index: number; level: number; title: string; offset: number }

export function noteOutline(source: string): OutlineHeading[] {
  const headings: OutlineHeading[] = []
  const metadata = splitNoteMetadata(source)
  const prefixLength = metadata?.prefix.length ?? 0
  const body = metadata?.body ?? source
  // Marked normalizes line endings in token.raw; keep offsets in the original
  // source so CRLF notes use the same positions as LF notes and the editor.
  const normalized = body.replace(/\r\n?/g, '\n'), crlf = /\r\n/g
  let lineBreak = crlf.exec(body), removed = 0, offset = 0
  let headingIndex = 0
  for (const token of marked.lexer(normalized)) {
    const start = normalized.indexOf(token.raw, offset)
    if (token.type === 'heading') {
      while (lineBreak && lineBreak.index < start + removed) {
        removed++; lineBreak = crlf.exec(body)
      }
      const index = headingIndex++
      headings.push({ index, level: token.depth, title: token.text.replace(/[*_`]/g, ''), offset: prefixLength + Math.max(0, start) + removed })
    }
    if (start >= 0) offset = start + token.raw.length
  }
  return headings
}
