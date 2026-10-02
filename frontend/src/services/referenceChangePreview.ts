import type { ReferenceEdit } from './vaultReferences'

export interface ReferenceHunk {
  before: string
  after: string
  beforeLine: number
  afterLine: number
}

/** Every replacement is reviewable, even in a single very long line or document. */
export function referenceChangeHunks(before: string, after: string, edits?: ReferenceEdit[]): ReferenceHunk[] {
  const lineAt = (text: string) => {
    const starts = [0, ...text.matchAll(/\r\n|\n|\r/g)].map(value => typeof value === 'number' ? value : value.index! + value[0].length)
    return (offset: number) => {
      let low = 0, high = starts.length
      while (low < high) { const middle = (low + high) >>> 1; if (starts[middle]! <= offset) low = middle + 1; else high = middle }
      return low
    }
  }
  const beforeLine = lineAt(before), afterLine = lineAt(after)
  if (edits?.length) {
    let delta = 0
    return edits.map(edit => {
      const afterStart = edit.start + delta
      const beforeStart = Math.max(0, edit.start - 160), previewStart = Math.max(0, afterStart - 160)
      const hunk = {
        before: before.slice(beforeStart, edit.end + 160),
        after: after.slice(previewStart, afterStart + edit.after.length + 160),
        beforeLine: beforeLine(beforeStart), afterLine: afterLine(previewStart),
      }
      delta += edit.after.length - (edit.end - edit.start)
      return hunk
    })
  }
  if (before === after) return []
  // Canvas JSON and legacy plans have no Markdown destination tokens. Show
  // the complete changed range in bounded pages, including all later changes.
  let start = 0, endBefore = before.length, endAfter = after.length
  while (start < endBefore && start < endAfter && before[start] === after[start]) start++
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) { endBefore--; endAfter-- }
  start = Math.max(0, start - 160)
  endBefore = Math.min(before.length, endBefore + 160)
  endAfter = Math.min(after.length, endAfter + 160)
  const hunks: ReferenceHunk[] = []
  for (let offset = 0; offset < Math.max(endBefore - start, endAfter - start); offset += 1200) {
    const beforeStart = Math.min(endBefore, start + offset), afterStart = Math.min(endAfter, start + offset)
    hunks.push({ before: before.slice(beforeStart, Math.min(endBefore, beforeStart + 1200)),
      after: after.slice(afterStart, Math.min(endAfter, afterStart + 1200)),
      beforeLine: beforeLine(beforeStart), afterLine: afterLine(afterStart) })
  }
  return hunks
}
