import { expect, it, vi } from 'vitest'
import { VaultLinkIndex } from './vaultLinkIndex'
import { rewritePathReferences, scanVaultReferences } from './vaultReferences'

const file = (path: string, hash = path) => ({ id: path, name: path.slice(1), path, content_hash: hash, type: 'file' as const })
it('incrementally reads changed files, re-resolves paths after moves and isolates resets', async () => {
  const index = new VaultLinkIndex(), read = vi.fn(async (path: string) => path === '/a.md' ? '[b](b.md) [[missing]]' : '')
  await index.update([file('/a.md'),file('/b.md')], read)
  expect(index.backlinks('/b.md')).toHaveLength(1)
  expect(index.broken[0]?.status).toBe('missing')
  await index.update([file('/a.md'),file('/b.md')], read)
  expect(read).toHaveBeenCalledTimes(2)
  await index.update([file('/a.md'),file('/c.md')], read)
  expect(index.backlinks('/b.md')).toHaveLength(0)
  expect(index.broken).toHaveLength(2)
  await index.update([file('/a.md','changed'),file('/c.md')], read)
  expect(read).toHaveBeenCalledTimes(4)
  index.reset()
  expect(index.references).toEqual([])
})
it('retains diagnostic errors and excludes stale reads after changing vault', async () => {
  const index = new VaultLinkIndex()
  await index.update([file('/bad.canvas')], async () => '{broken')
  expect(index.errors[0]?.path).toBe('/bad.canvas')
  let finish!: (value:string) => void
  const pending = index.update([file('/a.md')], () => new Promise(resolve => { finish = resolve }))
  index.reset(); finish('[b](b.md)'); await pending
  expect(index.documents.size).toBe(0)
})
it('rebases incoming and outgoing links across folder moves without changing ambiguous links or unknown canvas fields', () => {
  const paths = new Set(['/course/a.md','/course/b.md','/other/b.md','/outside.md','/map.canvas'])
  const markdown = '[same](b.md#part "title") [out](../outside.md) [[b]] [[course/b.md#h|label]]'
  const result = rewritePathReferences('/course/a.md', markdown, '/course', '/nested/new', paths)
  expect(result.content).toBe('[same](b.md#part "title") [out](../../outside.md) [[b]] [[nested/new/b.md#h|label]]')
  expect(result.pending).toHaveLength(1)
  const canvas = JSON.stringify({ nodes: [{id:'n',type:'file',file:'course/b.md',subpath:'#h',unknown:123}],edges:[],unknown:{a:1} })
  const changed = JSON.parse(rewritePathReferences('/map.canvas',canvas,'/course','/nested/new',paths).content)
  expect(changed.nodes[0]).toMatchObject({file:'nested/new/b.md',subpath:'#h',unknown:123})
  expect(changed.unknown).toEqual({a:1})
  expect(scanVaultReferences('/a.md','[url](https://example.com) [mail](mailto:a@b) [bad](../../a.md)',paths).map(ref=>ref.status)).toEqual(['external','unsupported','unsafe'])
})
