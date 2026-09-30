import { describe, expect, it } from 'vitest'
import { resolveVaultReference, rewriteReferences, rewritePathReferences, scanVaultReferences } from './vaultReferences'

describe('vault references', () => {
  const paths = new Set(['/notes/a.md', '/notes/b.md', '/assets/logo.png'])

  it('resolves only paths in the vault', () => {
    expect(resolveVaultReference('/notes/a.md', '../assets/logo.png')).toBe('/assets/logo.png')
    expect(resolveVaultReference('/notes/a.md', '../../secret.md')).toBeNull()
    expect(resolveVaultReference('/notes/a.md', 'file:///secret.md')).toBeNull()
  })

  it('records markdown, images and canvas files while skipping fenced code', () => {
    const markdown = '[note](b.md) ![logo](../assets/logo.png)\n```md\n[x](b.md)\n```'
    expect(scanVaultReferences('/notes/a.md', markdown, paths).map(item => item.kind)).toEqual(['markdown', 'image'])
    const canvas = JSON.stringify({ nodes: [{ id: 'n', type: 'file', file: 'notes/b.md' }] })
    expect(scanVaultReferences('/map.canvas', canvas, paths)[0]?.target).toBe('/notes/b.md')
  })

  it('rewrites only exact targets and preserves labels and suffixes', () => {
    const markdown = '[label](b.md#part) and [external](https://example.com)'
    const result = rewriteReferences('/notes/a.md', markdown, '/notes/b.md', '/notes/renamed.md', paths)
    expect(result.content).toBe('[label](renamed.md#part) and [external](https://example.com)')
    expect(result.changed).toHaveLength(1)
  })

  it('includes reference-style definitions without rewriting unsafe paths', () => {
    const content = '[source]: <b.md>\n[escape](../../outside.md)'
    const references = scanVaultReferences('/notes/a.md', content, paths)
    expect(references.map(ref => ref.status)).toEqual(['resolved', 'unsafe'])
    expect(rewriteReferences('/notes/a.md', content, '/notes/b.md', '/notes/renamed.md', paths).content)
      .toContain('[source]: <renamed.md>')
  })
  it('checks and rewrites group backgrounds along with file nodes without losing custom fields',()=>{
    const canvas=JSON.stringify({nodes:[{id:'group',type:'group',background:'assets/logo.png',backgroundStyle:'repeat',custom:true},{id:'image',type:'file',file:'assets/logo.png'}]})
    expect(scanVaultReferences('/map.canvas',canvas,paths).map(item=>item.field)).toEqual(['background','file'])
    for(const rewrite of[rewriteReferences,rewritePathReferences]){
      const result=JSON.parse(rewrite('/map.canvas',canvas,'/assets/logo.png','/assets/renamed.png',paths).content)
      expect(result.nodes[0]).toEqual({id:'group',type:'group',background:'assets/renamed.png',backgroundStyle:'repeat',custom:true})
      expect(result.nodes[1].file).toBe('assets/renamed.png')
    }
  })
})
