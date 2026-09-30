import { validateCanvasContent } from '@/services/workspaceDocuments'
export interface CanvasNode { id:string;type:'text'|'file'|'link'|'group';x:number;y:number;width:number;height:number;text?:string;file?:string;url?:string;label?:string;color?:string;subpath?:string;background?:string;backgroundStyle?:string;[key:string]:unknown }
export type CanvasSide='left'|'right'|'top'|'bottom'
export interface CanvasEdge { id:string;fromNode:string;toNode:string;fromSide?:CanvasSide;toSide?:CanvasSide;fromEnd?:'none'|'arrow';toEnd?:'none'|'arrow';label?:string;color?:string;[key:string]:unknown }
export interface CanvasDocument {nodes:CanvasNode[];edges:CanvasEdge[];[key:string]:unknown}
const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value))
export function parseCanvas(source:string):CanvasDocument {validateCanvasContent(source);const root=JSON.parse(source);return{...root,nodes:root.nodes??[],edges:root.edges??[]}}
export function nodeLabel(node:CanvasNode){
  if(node.type==='file')return (node.file?.split('/').pop()??'文件').slice(0,120)
  if(node.type==='link'){try{return new URL(node.url!).hostname}catch{return String(node.url??'网址').slice(0,120)}}
  return (String(node.text??node.label??'分组').split(/\r?\n/).find(line=>line.trim())??'').replace(/^\s*#{1,6}\s+/,'').trim().slice(0,120)
}
export function nodeBounds(nodes:CanvasNode[]){
  if(!nodes.length)return{x:0,y:0,width:640,height:480}
  const x=Math.min(...nodes.map(node=>node.x)),y=Math.min(...nodes.map(node=>node.y))
  return{x,y,width:Math.max(...nodes.map(node=>node.x+node.width))-x,height:Math.max(...nodes.map(node=>node.y+node.height))-y}
}
export function nodesInsideGroup(group:CanvasNode,nodes:CanvasNode[]){return nodes.filter(node=>node.id!==group.id&&node.x>=group.x&&node.y>=group.y&&node.x+node.width<=group.x+group.width&&node.y+node.height<=group.y+group.height).map(node=>node.id)}
export class CanvasModel {
  document:CanvasDocument
  source:string
  private past:string[]=[]
  private future:string[]=[]
  constructor(source:string){this.source=source;this.document=parseCanvas(source)}
  get canUndo(){return!!this.past.length}get canRedo(){return!!this.future.length}
  change(update:(document:CanvasDocument)=>void){
    const document=clone(this.document);update(document)
    const source=JSON.stringify(document,null,2)+'\n';validateCanvasContent(source)
    if(JSON.stringify(document)===JSON.stringify(this.document))return false
    this.past.push(this.source);let size=this.past.reduce((total,item)=>total+item.length*2,0)
    while(this.past.length>50||size>32*1024*1024&&this.past.length>1)size-=this.past.shift()!.length*2
    this.future=[];this.source=source;this.document=document;return true
  }
  undo(){const source=this.past.pop();if(source===undefined)return false;this.future.push(this.source);this.source=source;this.document=parseCanvas(source);return true}
  redo(){const source=this.future.pop();if(source===undefined)return false;this.past.push(this.source);this.source=source;this.document=parseCanvas(source);return true}
  add(type:CanvasNode['type'],x:number,y:number,value=''){
    const id=crypto.randomUUID(),node:CanvasNode={id,type,x:Math.round(x),y:Math.round(y),width:type==='group'?600:300,height:type==='group'?420:180}
    if(type==='text')node.text=value;if(type==='file')node.file=value;if(type==='link')node.url=value;if(type==='group')node.label=value
    this.change(document=>{if(type==='group')document.nodes.unshift(node);else document.nodes.push(node)});return id
  }
  updateNode(id:string,fields:Partial<CanvasNode>){return this.change(document=>{const node=document.nodes.find(node=>node.id===id);if(node)Object.assign(node,fields,{id:node.id,type:node.type})})}
  move(ids:string[],dx:number,dy:number){const set=new Set(ids);return this.change(document=>document.nodes.forEach(node=>{if(set.has(node.id)){node.x+=Math.round(dx);node.y+=Math.round(dy)}}))}
  remove(ids:string[],edgeId?:string){const set=new Set(ids);return this.change(document=>{document.nodes=document.nodes.filter(node=>!set.has(node.id));document.edges=document.edges.filter(edge=>edge.id!==edgeId&&!set.has(edge.fromNode)&&!set.has(edge.toNode))})}
  connect(from:string,to:string,label=''){const id=crypto.randomUUID();this.change(document=>document.edges.push({id,fromNode:from,toNode:to,label}));return id}
  updateEdge(id:string,fields:Partial<CanvasEdge>){return this.change(document=>{const edge=document.edges.find(edge=>edge.id===id);if(edge)Object.assign(edge,fields,{id:edge.id})})}
  copy(ids:string[]){const set=new Set(ids);return JSON.stringify({opennexus_canvas:1,nodes:this.document.nodes.filter(node=>set.has(node.id)),edges:this.document.edges.filter(edge=>set.has(edge.fromNode)&&set.has(edge.toNode))})}
  paste(source:string,dx=40,dy=40){
    const value=JSON.parse(source)
    if(!value||value.opennexus_canvas!==1||!Array.isArray(value.nodes)||!Array.isArray(value.edges))throw new Error('CANVAS_CLIPBOARD_INVALID')
    validateCanvasContent(JSON.stringify({nodes:value.nodes,edges:value.edges}))
    const identities=new Map<string,string>(value.nodes.map((node:CanvasNode)=>[node.id,crypto.randomUUID()]))
    const nodes:CanvasNode[]=value.nodes.map((node:CanvasNode)=>({...node,id:identities.get(node.id)!,x:node.x+dx,y:node.y+dy}))
    const edges:CanvasEdge[]=value.edges.map((edge:CanvasEdge)=>({...edge,id:crypto.randomUUID(),fromNode:identities.get(edge.fromNode)!,toNode:identities.get(edge.toNode)!}))
    this.change(document=>{document.nodes.push(...nodes);document.edges.push(...edges)})
    return nodes.map(node=>node.id)
  }
  group(ids:string[]){const nodes=this.document.nodes.filter(node=>ids.includes(node.id));if(!nodes.length)return
    const bounds=nodeBounds(nodes),id=crypto.randomUUID()
    this.change(document=>document.nodes.unshift({id,type:'group',label:'分组',x:bounds.x-30,y:bounds.y-60,width:bounds.width+60,height:bounds.height+90}));return id
  }
  mindMap(rootId?:string){
    return this.change(document=>{
      const ordinary=document.nodes.filter(node=>node.type!=='group'),ids=new Set(ordinary.map(node=>node.id)),depth=new Map<string,number>()
      const children=new Map<string,string[]>(),incoming=new Set<string>()
      for(const edge of document.edges)if(ids.has(edge.fromNode)&&ids.has(edge.toNode)){children.set(edge.fromNode,[...(children.get(edge.fromNode)??[]),edge.toNode]);incoming.add(edge.toNode)}
      const groups=document.nodes.filter(node=>node.type==='group').map(group=>({group,children:nodesInsideGroup(group,document.nodes).filter(id=>ids.has(id))}))
      const queue:string[]=[],visit=(id:string,d:number)=>{if(!depth.has(id)){depth.set(id,d);queue.push(id)}}
      if(rootId&&ids.has(rootId))visit(rootId,0)
      for(const node of ordinary)if(!incoming.has(node.id))visit(node.id,0)
      const drain=()=>{for(let index=0;index<queue.length;index++){const id=queue[index]!;for(const child of children.get(id)??[])visit(child,depth.get(id)!+1)}queue.length=0}
      drain();for(const node of ordinary)if(!depth.has(node.id)){visit(node.id,0);drain()}
      const widths:number[]=[],heights:number[]=[],x:number[]=[],nextY:number[]=[]
      for(const node of ordinary){const d=depth.get(node.id)!;widths[d]=Math.max(widths[d]??0,node.width);heights[d]=(heights[d]??-90)+node.height+90}
      for(let d=0;d<widths.length;d++)x[d]=d===0?0:x[d-1]!+widths[d-1]!+160
      const height=Math.max(0,...heights)
      for(const node of ordinary){const d=depth.get(node.id)!;node.x=x[d]!;node.y=Math.round(nextY[d]??(height-heights[d]!)/2);nextY[d]=node.y+node.height+90}
      for(const edge of document.edges)if(ids.has(edge.fromNode)&&ids.has(edge.toNode)){edge.fromSide='right';edge.toSide='left'}
      for(const{group,children}of groups){const members=ordinary.filter(node=>children.includes(node.id));if(!members.length)continue;const bounds=nodeBounds(members);group.x=bounds.x-30;group.y=bounds.y-60;group.width=bounds.width+60;group.height=bounds.height+90}
    })
  }
}
export function edgeGeometry(edge:CanvasEdge,nodes:CanvasNode[]){
  const from=nodes.find(node=>node.id===edge.fromNode),to=nodes.find(node=>node.id===edge.toNode);if(!from||!to)return null
  const anchors=(node:CanvasNode,side:CanvasSide)=>({left:[node.x,node.y+node.height/2],right:[node.x+node.width,node.y+node.height/2],top:[node.x+node.width/2,node.y],bottom:[node.x+node.width/2,node.y+node.height]}[side]!)
  const dx=to.x+to.width/2-from.x-from.width/2,dy=to.y+to.height/2-from.y-from.height/2
  const vertical=Math.abs(dy)/((from.height+to.height)/2)>Math.abs(dx)/((from.width+to.width)/2)
  const fromSide=edge.fromSide??(vertical?(dy>=0?'bottom':'top'):(dx>=0?'right':'left')),toSide=edge.toSide??(vertical?(dy>=0?'top':'bottom'):(dx>=0?'left':'right'))
  const[a,b]=anchors(from,fromSide),[c,d]=anchors(to,toSide),distance=Math.max(48,(vertical?Math.abs(d!-b!):Math.abs(c!-a!))/2)
  const control=(x:number,y:number,side:CanvasSide)=>side==='left'?[x-distance,y]:side==='right'?[x+distance,y]:side==='top'?[x,y-distance]:[x,y+distance]
  const[p,q]=control(a!,b!,fromSide),[r,s]=control(c!,d!,toSide)
  return{path:`M ${a} ${b} C ${p} ${q}, ${r} ${s}, ${c} ${d}`,x:(a!+c!)/2,y:(b!+d!)/2}
}
