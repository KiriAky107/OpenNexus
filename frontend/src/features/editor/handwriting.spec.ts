import { describe, expect, it } from 'vitest'
import { CanvasModel } from './canvasModel'
import { applyHandwritingProposal } from './handwritingActions'
import { emptyHandwriting, eraseStroke, fitHandwrittenText, handwritingOf, handwritingPlainText, INK_KEY, InkInput, layoutHandwrittenText, MAX_INK_POINTS, strokePath, validHandwriting } from './handwriting'
import { validateCanvasContent } from '@/services/workspaceDocuments'
const p=(x=1,y=1,pressure=.5)=>({x,y,pressure})
const stroke=()=>({id:'stroke',color:'#123456',width:3,points:[p(10,10),p(100,100,1)]})
describe('portable handwriting',()=>{
  it('keeps bounded pressure ink and rejects malformed or oversized extensions',()=>{
    const ink=emptyHandwriting();ink.strokes=[stroke()];expect(validHandwriting(ink)).toBe(true)
    for(const mutate of [(v:any)=>v.strokes[0].points[0].pressure=true,(v:any)=>v.strokes[0].points[0].x=-1,(v:any)=>v.strokes[0].color='url(https://bad)',(v:any)=>v.strokes.push(v.strokes[0]),(v:any)=>v.strokes[0].points=Array(MAX_INK_POINTS+1).fill(p()),(v:any)=>v.mode='text']){
      const next=structuredClone(ink);mutate(next);expect(validHandwriting(next)).toBe(false)
    }
  })
  it('validates ink at the canvas boundary and keeps fallback source text',()=>{
    const ink=emptyHandwriting();ink.strokes=[stroke()]
    const node={id:'ink',type:'text',x:0,y:0,width:480,height:360,text:'原文',[INK_KEY]:ink}
    const source=JSON.stringify({nodes:[node],edges:[],custom:{preserve:true}})
    validateCanvasContent(source);expect(handwritingOf(node)).toEqual(ink)
    ink.strokes[0]!.points[0]!.x=Infinity
    expect(()=>validateCanvasContent(JSON.stringify({nodes:[node],edges:[]}))).toThrow('CANVAS_HANDWRITING_INVALID')
  })
  it('emits deterministic text layout, preserves source and reports overflow',()=>{
    const ink=emptyHandwriting('text'),text='数学 😀\n3 * 4 = 12\n$\\frac{1}{2}$'
    expect(layoutHandwrittenText(text,ink)).toEqual(layoutHandwrittenText(text,ink))
    expect(layoutHandwrittenText(text,ink).glyphs.map(g=>g.char).join('')).toBe(text.replaceAll('\n',''))
    expect(layoutHandwrittenText('长'.repeat(10000),ink).overflow).toBe(true)
    expect(layoutHandwrittenText(text,ink).glyphs.every(g=>Number.isFinite(g.x)&&Number.isFinite(g.rotation))).toBe(true)
  })
  it('uses a closed pressure path and visible one-point dots',()=>{
    expect(strokePath(stroke())).toMatch(/^M .+ Z$/)
    expect(strokePath({...stroke(),points:[p()]})).toContain(' a ')
  })
  it('grows a long text page without truncating or changing the caller page',()=>{
    const original=emptyHandwriting('text'),text='长'.repeat(1200)
    const fitted=fitHandwrittenText(text,original)!
    expect(fitted.height).toBeGreaterThan(original.height);expect(fitted.height).toBeLessThanOrEqual(4096)
    expect(layoutHandwrittenText(text,fitted).glyphs.map(g=>g.char).join('')).toBe(text)
    expect(original.height).toBe(640);expect(fitted).not.toBe(original)
    expect(fitHandwrittenText('长'.repeat(10000),original)).toBeUndefined()
    expect(fitHandwrittenText(text,{...original,height:0})).toBeUndefined()
    expect(fitHandwrittenText(text,emptyHandwriting())).toBeUndefined()
  })
  it('cleans Markdown presentation without damaging math operators or source',()=>{
    const source='# 标题\n\n**重点**：3 * 4 * 5 = 60\n\n- 负数 -3\n- `x / y`'
    const rendered=handwritingPlainText(source)
    expect(rendered).toContain('3 * 4 * 5 = 60');expect(rendered).toContain('• 负数 -3');expect(rendered).toContain('x / y')
    expect(rendered).not.toContain('**');expect(rendered).not.toContain('# 标题')
    expect(()=>handwritingPlainText('> '.repeat(100)+'保留原文')).not.toThrow()
  })
  it('erases strokes by segment distance rather than only sampled points',()=>{
    const ink=emptyHandwriting();ink.strokes=[stroke()]
    expect(eraseStroke(ink,p(50,50))).toBe(true);expect(ink.strokes).toEqual([])
    ink.strokes=[stroke()];expect(eraseStroke(ink,p(800,500))).toBe(false)
  })
})
describe('pointer ownership',()=>{
  it('commits a touch stroke once and copies points',()=>{
    const input=new InkInput(),point=p();input.down(1,'touch',point,'#123456',3,false);point.x=900
    input.move(1,[p(2,2),p(3,3)]);const result=input.up(1)!
    expect(result.points[0]!.x).toBe(1);expect(result.points).toHaveLength(3);expect(input.up(1)).toBeUndefined()
  })
  it('never commits a pinch, cancelled stroke or lost capture',()=>{
    const input=new InkInput();input.down(1,'touch',p(),'#123456',3,false);input.down(2,'touch',p(),'#123456',3,false)
    expect(input.up(1)).toBeUndefined();expect(input.up(2)).toBeUndefined()
    input.down(3,'touch',p(),'#123456',3,false);input.cancel(3);expect(input.up(3)).toBeUndefined()
  })
  it('gives the stylus ownership and ignores palms',()=>{
    const input=new InkInput();input.down(1,'touch',p(),'#123456',3,true);expect(input.draft).toBeUndefined()
    input.down(2,'pen',p(10,10,.9),'#123456',3,true);input.down(3,'touch',p(),'#123456',3,true);input.cancel(3)
    expect(input.up(2)?.points[0]?.pressure).toBe(.9)
  })
  it('does not accept samples from another pointer',()=>{
    const input=new InkInput();input.down(1,'pen',p(),'#123456',3,false);input.move(2,[p(999,999)]);expect(input.up(2)).toBeUndefined();expect(input.up(1)?.points).toHaveLength(1)
  })
})
describe('reviewed conversion',()=>{
  const empty='{"nodes":[],"edges":[],"extension":{"keep":true}}'
  it('saves original ink and a reviewed summary atomically, with undo',()=>{
    const model=new CanvasModel(empty),ink=emptyHandwriting();ink.strokes=[stroke()]
    applyHandwritingProposal(model,empty,undefined,{x:0,y:0},{kind:'text',original:{ink,text:'transcript'},output:{text:'# Summary'}})
    expect(model.document.nodes).toHaveLength(2);expect(handwritingOf(model.document.nodes[0])).toEqual(ink)
    expect(model.document.nodes[1]!.text).toBe('# Summary');expect(model.document.extension).toEqual({keep:true})
    model.undo();expect(model.source).toBe(empty);model.redo();expect(model.document.nodes).toHaveLength(2)
  })
  it('preserves the original before exporting Markdown',()=>{
    const model=new CanvasModel(empty),ink=emptyHandwriting();ink.strokes=[stroke()]
    const result=applyHandwritingProposal(model,empty,undefined,{x:0,y:0},{kind:'markdown',original:{ink,text:''},output:{text:'Markdown'}})
    expect(result.markdown).toBe('Markdown');expect(model.document.nodes).toHaveLength(1)
  })
  it('rejects changed documents and invalid output without partial mutation',()=>{
    const model=new CanvasModel(empty),ink=emptyHandwriting()
    model.add('text',0,0,'human edit');const before=model.source
    expect(()=>applyHandwritingProposal(model,empty,undefined,{x:0,y:0},{kind:'save',original:{ink,text:''}})).toThrow('CANVAS_HANDWRITING_STALE')
    expect(model.source).toBe(before)
    ink.strokes=[{...stroke(),points:[p(-5,0)]}]
    expect(()=>applyHandwritingProposal(model,before,undefined,{x:0,y:0},{kind:'save',original:{ink,text:''}})).toThrow()
    expect(model.source).toBe(before)
  })
  it('resizes expanded paper while preserving an unchanged manually resized node',()=>{
    const ink=emptyHandwriting('text')
    const model=new CanvasModel(JSON.stringify({nodes:[{id:'ink',type:'text',x:0,y:0,width:480,height:500,text:'short',[INK_KEY]:ink}],edges:[]}))
    applyHandwritingProposal(model,model.source,'ink',{x:0,y:0},{kind:'save',original:{ink,text:'edited'}})
    expect(model.document.nodes[0]!.height).toBe(500)
    const expanded=fitHandwrittenText('长'.repeat(1200),ink)!
    applyHandwritingProposal(model,model.source,'ink',{x:0,y:0},{kind:'save',original:{ink:expanded,text:'长'.repeat(1200)}})
    expect(model.document.nodes[0]!.height).toBe(480*expanded.height/expanded.width+40)
    model.undo();expect(model.document.nodes[0]!.height).toBe(500)
  })
})
