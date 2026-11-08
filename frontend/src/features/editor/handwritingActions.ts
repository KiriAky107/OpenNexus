import { CanvasModel, type CanvasNode } from './canvasModel'
import { handwritingOf, INK_KEY, type Handwriting } from './handwriting'
export interface HandwritingProposal {
  kind: 'save'|'text'|'handwrite'|'markdown'
  original: {ink:Handwriting;text:string}
  output?: {ink?:Handwriting;text:string}
}
/** Commit the original and the reviewed result in one undo step, against the captured document. */
export function applyHandwritingProposal(model:CanvasModel,source:string,id:string|undefined,point:{x:number;y:number},proposal:HandwritingProposal){
  if(model.source!==source)throw Error('CANVAS_HANDWRITING_STALE')
  let selected=id
  model.change(doc=>{
    const original=proposal.original
    if(id){const node=doc.nodes.find(n=>n.id===id);if(!node)throw Error('CANVAS_NODE_CHANGED');const previous=handwritingOf(node);if(!previous||previous.width!==original.ink.width||previous.height!==original.ink.height)node.height=node.width*original.ink.height/original.ink.width+40;node.text=original.text;node[INK_KEY]=original.ink}
    else if(proposal.kind==='save'||original.ink.strokes.length||original.text.trim()){
      selected=crypto.randomUUID();doc.nodes.push({id:selected,type:'text',x:Math.round(point.x),y:Math.round(point.y),width:480,height:480*original.ink.height/original.ink.width+40,text:original.text,[INK_KEY]:original.ink})
    }
    if(proposal.output&&proposal.kind!=='markdown'){
      const next:CanvasNode={id:crypto.randomUUID(),type:'text',x:Math.round(point.x)+(selected?520:0),y:Math.round(point.y),width:480,height:proposal.output.ink?480*proposal.output.ink.height/proposal.output.ink.width+40:240,text:proposal.output.text}
      if(proposal.output.ink)next[INK_KEY]=proposal.output.ink
      doc.nodes.push(next);selected=next.id
    }
  })
  return {id:selected,markdown:proposal.kind==='markdown'?proposal.output?.text:undefined}
}
