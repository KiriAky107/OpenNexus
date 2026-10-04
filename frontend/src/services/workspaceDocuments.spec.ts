import { describe, expect, it } from 'vitest'
import { EMPTY_CANVAS, resolveCanvasFile, validateCanvasContent, workspaceDocumentType } from './workspaceDocuments'

describe('workspace document contracts', () => {
  it.each(['C# lesson.md', '100%.md', 'literal%2F%23.md', '%2e%2e/note.md'])('preserves raw Canvas filename %s', path => {
    expect(resolveCanvasFile(path)).toBe(`/${path}`)
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'file', type: 'file', x: 0, y: 0, width: 10, height: 10, file: path, subpath: '#heading' }, { id: 'group', type: 'group', x: 0, y: 0, width: 10, height: 10, background: path }] }))).not.toThrow()
  })
  it.each(['../note.md', '/note.md', '.git/a.md', 'folder/.hidden/a.md', 'OpenNexus-Records/a.md', 'C:/note.md', 'a\\b.md', 'NUL.md', 'COM1/a.md', 'a./b.md', 'a /b.md'])('rejects unsafe raw Canvas path %s', path => {
    expect(resolveCanvasFile(path)).toBeNull()
  })
  it('keeps document kinds distinct', () => {
    expect(workspaceDocumentType('/notes/a.md')).toBe('markdown')
    expect(workspaceDocumentType('/maps/a.canvas')).toBe('canvas')
    expect(workspaceDocumentType('/assets/a.png')).toBe('image')
  })

  it('validates canvas links and preserves unknown fields', () => {
    expect(() => validateCanvasContent(EMPTY_CANVAS)).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'file', x: 0, y: 0, width: 100, height: 50, file: 'notes/a.md', unknown: true }], edges: [], custom: 1 }))).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'file', x: 0, y: 0, width: 100, height: 50, file: '../secret' }] }))).toThrow('CANVAS_INVALID')
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'file', x: 0, y: 0, width: 100, height: 50, file: '.git/secret' }] }))).toThrow('CANVAS_INVALID')
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0.5, y: -2.25, width: 100.5, height: 50, url: 'https://example.com/path' }] }))).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0, y: 0, width: 100, height: 50, url: 'https://[::1]/path' }] }))).not.toThrow()
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0, y: 0, width: 100, height: 50, url: 'https://example.com:0' }] }))).toThrow('CANVAS_INVALID')
    expect(() => validateCanvasContent(JSON.stringify({ nodes: [{ id: 'a', type: 'link', x: 0, y: 0, width: 100, height: 50, url: 'https://user:pass@example.com' }] }))).toThrow('CANVAS_INVALID')
  })
  it('rejects malformed optional standard fields before rendering',()=>{
    const node={id:'a',type:'text',x:0,y:0,width:100,height:50,text:'safe'}
    for(const fields of[{color:'url(evil)'},{subpath:'heading'},{background:'../outside.png'},{label:42},{backgroundStyle:'invalid'}])
      expect(()=>validateCanvasContent(JSON.stringify({nodes:[{...node,...fields}]}))).toThrow('CANVAS_INVALID')
    for(const fields of[{label:42},{fromSide:'diagonal'},{toEnd:'triangle'},{color:'#fff'}])
      expect(()=>validateCanvasContent(JSON.stringify({nodes:[node],edges:[{id:'e',fromNode:'a',toNode:'a',...fields}]}))).toThrow('CANVAS_INVALID')
  })
})
