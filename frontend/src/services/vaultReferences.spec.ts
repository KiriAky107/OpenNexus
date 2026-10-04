import { describe, expect, it } from 'vitest'
import { resolveVaultReference, rewriteReferences, rewritePathReferences, scanVaultReferences } from './vaultReferences'

describe('vault references', () => {
  it('renames a Canvas self-reference whose literal filename starts with a hash', () => {
    const canvas = JSON.stringify({ nodes: [{ id: 'self', type: 'file', file: '#map.canvas', subpath: '#target' }] })
    const result = rewritePathReferences('/#map.canvas', canvas, '/#map.canvas', '/#new%.canvas', new Set(['/#map.canvas']))
    expect(JSON.parse(result.content).nodes[0]).toEqual({ id: 'self', type: 'file', file: '#new%.canvas', subpath: '#target' })
  })
  it.each(['C# lesson.md', '100%.md', 'literal%2F%23.md'])('resolves and rewrites raw Canvas filename %s without decoding', file => {
    const canvas = JSON.stringify({ nodes: [{ id: 'note', type: 'file', file, subpath: '#part' }, { id: 'group', type: 'group', background: file }] })
    const paths = new Set([`/${file}`])
    expect(scanVaultReferences('/map.canvas', canvas, paths).map(ref => [ref.target, ref.status])).toEqual([[`/${file}`, 'resolved'], [`/${file}`, 'resolved']])
    for (const rewrite of [rewriteReferences, rewritePathReferences]) {
      const result = JSON.parse(rewrite('/map.canvas', canvas, `/${file}`, '/new#100%.md', paths).content)
      expect(result.nodes[0]).toEqual({ id: 'note', type: 'file', file: 'new#100%.md', subpath: '#part' })
      expect(result.nodes[1].background).toBe('new#100%.md')
    }
  })
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

  it.each([
    '``[b](b.md) [[b]] <img src="b.md">``',
    '`one `` [b](b.md) [[b]]`',
    '    [b](b.md) [[b]] <img src="b.md">\n',
    '````md\n```md\n[b](b.md) [[b]] <img src="b.md">\n```\n````',
    '\\[b](b.md) \\[[b]]',
    '<!-- [b](b.md) [[b]] <img src="b.md"> -->',
    '<script>"<img src=\"b.md\">"; [b](b.md) [[b]]</script>',
    '---\ntitle: "[b](b.md) [[b]]"\ntags: []\n---\n',
  ])('preserves non-link source bytes: %s', example => {
    const content = `${example}\n\n[real](b.md)`
    const references = scanVaultReferences('/notes/a.md', content, paths)
    expect(references).toHaveLength(1)
    expect(references[0]?.raw).toBe('b.md')
    expect(rewritePathReferences('/notes/a.md', content, '/notes/b.md', '/notes/new.md', paths).content)
      .toBe(`${example}\n\n[real](new.md)`)
  })

  it('resolves balanced parentheses, escaped destinations, entities and multiline angle destinations', () => {
    const files = new Set(['/notes/a.md', '/notes/note(1).md', '/notes/中文 空格(1).md'])
    const content = '[paren](note(1).md#段落 "标题") [escape](note\\(1\\).md) [entity](note&#40;1&#41;.md)\r\n[space](\r\n<中文 空格(1).md#中文>\r\n"多行标题"\r\n)'
    const references = scanVaultReferences('/notes/a.md', content, files)
    expect(references.map(ref => ref.target)).toEqual(['/notes/note(1).md', '/notes/note(1).md', '/notes/note(1).md', '/notes/中文 空格(1).md'])
    for (const ref of references) expect(content.slice(ref.start, ref.end)).toBe(ref.raw)
    const result = rewritePathReferences('/notes/a.md', content, '/notes/note(1).md', '/notes/新(2).md', files)
    expect(result.content).toBe(content.replace('note(1).md', '%E6%96%B0%282%29.md').replace('note\\(1\\).md', '%E6%96%B0%282%29.md').replace('note&#40;1&#41;.md', '%E6%96%B0%282%29.md'))
  })

  it('captures exact CRLF/Unicode offsets and replaces each repeated destination without changing titles or labels', () => {
    const content = '中文🙂\r\n[标签 b.md](b.md "b.md [fake](b.md)") ![b.md](b.md)\r\n[引用][ref]\r\n\r\n[ref]:\r\n  <b.md#部分>\r\n  "标题 b.md"\r\n'
    const result = rewritePathReferences('/notes/a.md', content, '/notes/b.md', '/notes/new.md', paths)
    expect(result.changed).toHaveLength(3)
    expect(result.content).toBe(content.replace('](b.md "', '](new.md "').replace('![b.md](b.md)', '![b.md](new.md)').replace('<b.md#部分>', '<new.md#部分>'))
    expect(result.edits).toHaveLength(3)
    for (const edit of result.edits!) expect(content.slice(edit.start, edit.end)).toBe(edit.before)
  })

  it('parses actual HTML href/src attributes with quotes, repeated text and entities while ignoring data attributes', () => {
    const files = new Set([...paths, '/notes/p&b.md'])
    const content = '<a data-href="b.md" title="b.md > fake" HREF = "b.md#段落">link</a> <img data-src="b.md" src=b.md>\r\n<a href=\'p&amp;b.md?a=1&amp;b=2\'>中文</a>'
    const refs = scanVaultReferences('/notes/a.md', content, files)
    expect(refs.map(ref => ref.target)).toEqual(['/notes/b.md', '/notes/b.md', '/notes/p&b.md'])
    for (const ref of refs) expect(content.slice(ref.start, ref.end)).toBe(ref.raw)
    const first = rewritePathReferences('/notes/a.md', content, '/notes/b.md', '/notes/new.md', files)
    expect(first.content).toBe(content.replace('HREF = "b.md#', 'HREF = "new.md#').replace('src=b.md>', 'src=new.md>'))
    expect(rewritePathReferences('/notes/a.md', content, '/notes/p&b.md', '/notes/new.md', files).content)
      .toBe(content.replace('p&amp;b.md?a=1&amp;b=2', 'new.md?a=1&amp;b=2'))
  })

  it('handles linked images and skips inline or unclosed raw-text HTML contexts', () => {
    const content = '[![logo](../assets/logo.png)](b.md) before <script>[b](b.md) <img src="b.md"></script> [real](b.md)'
    const refs = scanVaultReferences('/notes/a.md', content, paths)
    expect(refs.map(ref => ref.kind)).toEqual(['image', 'markdown', 'markdown'])
    expect(scanVaultReferences('/notes/a.md', '<script>\n<img src="b.md">\n[b](b.md)', paths)).toEqual([])
    expect(scanVaultReferences('/notes/a.md', 'before <textarea>[b](b.md)</textarea> [real](b.md)', paths)).toHaveLength(1)
  })
})
