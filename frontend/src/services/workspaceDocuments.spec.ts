import { describe, expect, it } from 'vitest'
import { EMPTY_CANVAS, validateCanvasContent, workspaceDocumentType } from './workspaceDocuments'

describe('workspace document contracts', () => {
  it('keeps document kinds distinct', () => {
    expect(workspaceDocumentType('/notes/a.md')).toBe('markdown')
    expect(workspaceDocumentType('/maps/a.canvas')).toBe('canvas')
    expect(workspaceDocumentType('/assets/a.png')).toBe('image')
  })

  it('validates canvas links and preserves unknown fields', () => {
    expect(() => validateCanvasContent(EMPTY_CANVAS)).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'file', x: 0, y: 0, width: 100, height: 50, file: 'notes/a.md', unknown: true }], edges: [], custom: 1 }))).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'file', x: 0, y: 0, width: 100, height: 50, file: '../secret' }] }))).toThrow('CANVAS_INVALID')
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'file', x: 0, y: 0, width: 100, height: 50, file: '%2e%2e/secret' }] }))).toThrow('CANVAS_INVALID')
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0.5, y: -2.25, width: 100.5, height: 50, url: 'https://example.com/path' }] }))).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0, y: 0, width: 100, height: 50, url: 'https://[::1]/path' }] }))).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0, y: 0, width: 100, height: 50, url: 'https://example.com:0' }] }))).toThrow('CANVAS_INVALID')
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0, y: 0, width: 100, height: 50, url: 'https://user:pass@example.com' }] }))).toThrow('CANVAS_INVALID')
  })
})
