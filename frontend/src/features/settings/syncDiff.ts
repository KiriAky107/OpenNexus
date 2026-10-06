export interface DiffRow { kind: 'same' | 'added' | 'removed'; left: number | null; right: number | null; text: string }
/** Bound both the comparison matrix and rendered rows, preserving raw line endings. */
export function syncDiff(before: string, after: string) {
  const limit = 400, outputLimit = 240
  const a = before.split('\n', limit + 1), b = after.split('\n', limit + 1)
  let truncated = a.length > limit || b.length > limit
  a.length = Math.min(a.length, limit); b.length = Math.min(b.length, limit)
  const width = b.length + 1, matrix = new Uint16Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--)
    matrix[i * width + j] = a[i] === b[j] ? 1 + matrix[(i + 1) * width + j + 1] : Math.max(matrix[(i + 1) * width + j], matrix[i * width + j + 1])
  const rows: DiffRow[] = []
  let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { rows.push({ kind: 'same', left: ++i, right: ++j, text: a[i - 1] }); continue }
    if (i < a.length && (j === b.length || matrix[(i + 1) * width + j] >= matrix[i * width + j + 1])) rows.push({ kind: 'removed', left: ++i, right: null, text: a[i - 1] })
    else rows.push({ kind: 'added', left: null, right: ++j, text: b[j - 1] })
  }
  const visible = rows.filter((row, index) => row.kind !== 'same' || rows.slice(Math.max(0, index - 2), index + 3).some(item => item.kind !== 'same'))
  truncated ||= visible.length > outputLimit
  return { rows: visible.slice(0, outputLimit), truncated }
}
