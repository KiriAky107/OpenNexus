import { expect, it } from 'vitest'
import { noteOutline } from './outline'

it('hides legacy metadata while preserving editor heading positions', () => {
  const source = '***\n\ntitle: Python\ntags: python\n---\n\n# Variables\n'
  expect(noteOutline(source)).toEqual([{ index: 0, level: 1, title: 'Variables', offset: source.indexOf('# Variables') }])
})

it('keeps duplicate headings distinct and skips code fences', () => {
  const source = '# Same\n\n```md\n# Not a heading\n```\n\n## Same\n\nSetext\n---\n'
  expect(noteOutline(source).map(h => [h.index, h.level, h.title, source.slice(h.offset, h.offset + 2)])).toEqual([
    [0, 1, 'Same', '# '], [1, 2, 'Same', '##'], [2, 2, 'Setext', 'Se'],
  ])
})
