import { expect, it } from 'vitest'
import { referenceChangeHunks } from './referenceChangePreview'
import { rewritePathReferences } from './vaultReferences'

it('shows each reviewed replacement beyond the former 4000-character boundary', () => {
  const before = `${'中文🙂'.repeat(1300)}\r\n[one](b.md)\r\n${'unchanged\r\n'.repeat(1000)}[two](b.md#end)`
  const result = rewritePathReferences('/a.md', before, '/b.md', '/new-long-name.md', new Set(['/a.md', '/b.md']))
  const hunks = referenceChangeHunks(before, result.content, result.edits)
  expect(hunks).toHaveLength(2)
  expect(hunks[0]?.after).toContain('[one](new-long-name.md)')
  expect(hunks[1]?.after).toContain('[two](new-long-name.md#end)')
  expect(hunks[1]!.afterLine).toBeGreaterThan(980)
  expect(hunks.every(hunk => hunk.before.length < 400 && hunk.after.length < 400)).toBe(true)
})

it('includes the entire changed range of tokenless Canvas/legacy plans across bounded pages', () => {
  const prefix = 'unchanged'.repeat(1000), middle = 'context'.repeat(2000), suffix = 'tail'.repeat(1000)
  const before = `${prefix}old-one${middle}old-two${suffix}`, after = `${prefix}new-one${middle}new-two${suffix}`
  const hunks = referenceChangeHunks(before, after)
  const oldText = hunks.map(hunk => hunk.before).join(''), newText = hunks.map(hunk => hunk.after).join('')
  expect(oldText).toContain(`old-one${middle}old-two`)
  expect(newText).toContain(`new-one${middle}new-two`)
  expect(hunks.every(hunk => hunk.before.length <= 1200 && hunk.after.length <= 1200)).toBe(true)
})
