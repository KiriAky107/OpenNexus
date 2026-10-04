import type { ApiNoteBlock } from '@/contracts'

export interface BlockLocation { offset: number; length: number }
const lines = (value: string) => value.replace(/\r\n?/g, '\n')

/** Offsets supplied by Core are UTF-16 and include frontmatter. */
export function resolveBlockLocation(current: string, snapshot: string, block: ApiNoteBlock): BlockLocation | null {
  const { start_offset: from, end_offset: to } = block
  if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || to <= from || to > snapshot.length) return null
  const raw = snapshot.slice(from, to)
  if (lines(raw) !== lines(block.content)) return null
  if (current === snapshot) return { offset: from, length: to - from }
  // Unsaved edits can move an unchanged block. Only a unique exact occurrence
  // establishes its new position; duplicate paragraphs must not be guessed.
  const offset = current.indexOf(raw)
  if (offset < 0 || current.indexOf(raw, offset + 1) !== -1) return null
  return { offset, length: raw.length }
}
