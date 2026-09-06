import { expect, it } from 'vitest'
import { usedCitations } from './usedCitations'
const candidates = Array.from({ length: 6 }, (_, i) => ({ note_id: 'note', block_id: `${i}`, file_path: 'note.md', heading_path: '', content: 'source' }))

it('reveals completed references in first-use order without renumbering or duplicates', () => {
  expect(usedCitations('', candidates)).toEqual([])
  expect(usedCitations('结论 [3', candidates)).toEqual([])
  expect(usedCitations('结论 [3] 然后 [2] [3] [99]', candidates).map(item => item.number)).toEqual([3, 2])
  expect(usedCitations('结论 [3]', [])).toEqual([])
})

it('ignores code examples, escaped markers and links', () => {
  const content = '`[1]`\n\n```txt\n[2]\n```\n\n\\[3] [4](https://example.com) ![5](image.png)\n\n正文 **[6]**'
  expect(usedCitations(content, candidates).map(item => item.number)).toEqual([6])
})

it('restores legacy ID citations with their original numeric card labels', () => {
  const sources = candidates.map((c, i) => ({ ...c, citation_id: `cit_blk_${i}` }))
  expect(usedCitations('正文 [cit_blk_2][1][cit_blk_2] `[cit_blk_4]` [cit_blk_unknown]', sources).map(c => c.number)).toEqual([3, 1])
})
