import { describe, expect, it } from 'vitest'
import { inspectReferenceImpact } from './referenceImpact'

describe('rename reference impact', () => {
  it('keeps path references separate from stable note identity', () => {
    const paths = new Set(['/notes/a.md', '/notes/b.md', '/notes/c.md', '/map.canvas'])
    const documents = [
      { path: '/notes/a.md', content: '[target](b.md) and [other](c.md)' },
      { path: '/map.canvas', content: JSON.stringify({ nodes: [{ id: 'a', type: 'file', file: 'notes/b.md' }] }) },
      { path: '/notes/b.md', content: '[outgoing](c.md)' },
    ]
    const impact = inspectReferenceImpact(documents, paths, '/notes/b.md')
    expect(impact.incoming.map(ref => ref.source)).toEqual(['/notes/a.md', '/map.canvas'])
    expect(impact.outgoing.map(ref => ref.target)).toEqual(['/notes/c.md'])
  })
})
