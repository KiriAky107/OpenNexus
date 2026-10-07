export const LOG_WINDOW_SIZE = 24_576
export const LOG_CHUNK_SIZE = 2_048

// Host snapshots retain a bounded prefix. Offsets refer to that decoded text,
// never to bytes that the Host discarded after its retention limit.
export function logBoundary(text: string, offset: number): number {
  const at = Math.max(0, Math.min(text.length, offset))
  return at > 0 && at < text.length && /[\uD800-\uDBFF]/.test(text[at - 1]!) && /[\uDC00-\uDFFF]/.test(text[at]!) ? at - 1 : at
}

export function logChunks(text: string, start: number, end: number) {
  const chunks: { index: number; text: string }[] = []
  for (let index = Math.floor(start / LOG_CHUNK_SIZE); index * LOG_CHUNK_SIZE < end; index++) {
    const from = Math.max(start, logBoundary(text, index * LOG_CHUNK_SIZE))
    const to = Math.min(end, logBoundary(text, (index + 1) * LOG_CHUNK_SIZE))
    if (to > from) chunks.push({ index, text: text.slice(from, to) })
  }
  return chunks
}
