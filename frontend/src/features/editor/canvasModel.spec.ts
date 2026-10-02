import{expect,it}from'vitest'
import{CanvasModel,edgeGeometry,nodeLabel,movementIds}from'./canvasModel'
import imported from '../../../tests/fixtures/imported.canvas?raw'
const fixture=JSON.stringify({nodes:[{id:'a',type:'text',x:0,y:0,width:300,height:180,text:'Root',custom:{a:1}},{id:'b',type:'file',x:400,y:0,width:300,height:180,file:'course/note.md',subpath:'#heading'}],edges:[{id:'e',fromNode:'a',toNode:'b',label:'evidence',custom:true}],custom:{future:'preserved'}})
it('moves nested and overlapping group members once and records one undo',()=>{
 const source=JSON.stringify({nodes:[
  {id:'outer',type:'group',x:0,y:0,width:600,height:600,label:'Outer',extra:true},
  {id:'inner',type:'group',x:100,y:100,width:300,height:300,label:'Inner'},
  {id:'overlap',type:'group',x:50,y:50,width:400,height:400,label:'Overlap'},
  {id:'child',type:'text',x:150,y:150,width:100,height:100,text:'Child'},
  {id:'outside',type:'text',x:800,y:0,width:100,height:100,text:'Outside'},
 ],edges:[]})
 const model=new CanvasModel(source),before=model.document.nodes.map(n=>({...n}))
 expect(new Set(movementIds(model.document.nodes,['outer','overlap','child']))).toEqual(new Set(['outer','inner','overlap','child']))
 model.move(['outer','overlap','child'],10,-10)
 model.document.nodes.forEach((node,index)=>expect(node).toMatchObject({x:before[index]!.x+(node.id==='outside'?0:10),y:before[index]!.y+(node.id==='outside'?0:-10)}))
 expect(model.document.nodes[0]!.extra).toBe(true)
 expect(model.undo()).toBe(true);expect(model.source).toBe(source);expect(model.canUndo).toBe(false)
 model.redo();expect(model.document.nodes[3]!.x).toBe(160)
})
it('edits, copies, removes and undo/redoes while preserving unknown fields and reference contents',()=>{
  const model=new CanvasModel(fixture);model.move(['a'],20,30);expect(model.document.nodes[0]).toMatchObject({x:20,y:30,custom:{a:1}})
  model.undo();expect(model.source).toBe(fixture);model.redo();expect(model.document.nodes[0]?.x).toBe(20)
  const selected=model.paste(model.copy(['a','b']));expect(selected).toHaveLength(2);expect(model.document.edges).toHaveLength(2)
  expect(model.document.nodes.find(node=>node.id===selected[1])).toMatchObject({file:'course/note.md',subpath:'#heading'})
  model.remove(selected);expect(model.document.nodes).toHaveLength(2);expect(model.document.edges).toHaveLength(1)
  expect(model.document.custom).toEqual({future:'preserved'})
})

it('centres levels with unequal node heights and uses vertical anchors without changing explicit sides',()=>{
 const nodes=[{id:'root',type:'text',x:0,y:0,width:300,height:100,text:'# Root\n\nBody'},{id:'a',type:'text',x:0,y:300,width:300,height:200,text:'A'},{id:'b',type:'text',x:0,y:600,width:300,height:300,text:'B'}]
 const edges=[{id:'a',fromNode:'root',toNode:'a'},{id:'b',fromNode:'root',toNode:'b'}]
 const model=new CanvasModel(JSON.stringify({nodes,edges}));const before=model.source;model.mindMap('root')
 const [root,a,b]=model.document.nodes
 expect(root!.y+root!.height/2).toBe((a!.y+b!.y+b!.height)/2)
 expect(a!.y+a!.height).toBeLessThan(b!.y)
 expect(nodeLabel(root!)).toBe('Root')
 expect(edgeGeometry(edges[0]!,nodes as any)?.path).toMatch(/^M 150 100 C/)
 expect(edgeGeometry({...edges[0]!,fromSide:'right'},nodes as any)?.path).toMatch(/^M 300 50 C/)
 model.undo();expect(model.source).toBe(before)
})
it('rejects invalid mutations and pasted unsafe paths without changing the original document',()=>{
  const model=new CanvasModel(fixture)
  expect(()=>model.add('link',0,0,'javascript:alert(1)')).toThrow('CANVAS_INVALID')
  expect(()=>model.updateNode('b',{file:'../secret.md'})).toThrow('CANVAS_INVALID')
  expect(()=>model.updateNode('a',{width:0})).toThrow('CANVAS_INVALID')
  expect(model.source).toBe(fixture);expect(model.canUndo).toBe(false)
  expect(()=>model.paste(JSON.stringify({opennexus_canvas:1,nodes:[{...model.document.nodes[1],file:'file:///secret'}],edges:[]}))).toThrow()
})
it('lays out cyclic and disconnected graphs, updates group bounds, and keeps content and edge labels unchanged',()=>{
  const model=new CanvasModel(fixture),group=model.group(['a','b'])!
  model.connect('b','a','cycle');model.add('text',300,500,'Disconnected')
  const before=model.source;model.mindMap('a')
  expect(model.document.nodes.find(node=>node.id==='b')?.x).toBeGreaterThan(model.document.nodes.find(node=>node.id==='a')!.x)
  expect(model.document.nodes.find(node=>node.id===group)?.label).toBe('分组')
  expect(model.document.nodes.find(node=>node.id==='a')?.text).toBe('Root')
  expect(model.document.edges[0]).toMatchObject({label:'evidence',custom:true,fromSide:'right',toSide:'left'})
  expect(edgeGeometry(model.document.edges[0]!,model.document.nodes)?.path).toContain('C')
  model.undo();expect(model.source).toBe(before)
})
it('round-trips an imported canvas file with every standard node type and exporter fields',()=>{
 const model=new CanvasModel(imported),original=JSON.parse(imported)
 model.mindMap('root');model.updateNode('root',{x:15});const reloaded=new CanvasModel(model.source)
 expect(reloaded.document.exporter).toEqual(original.exporter)
 expect(reloaded.document.nodes.find(node=>node.id==='root')).toMatchObject({text:original.nodes[1].text,custom:{preserve:[1,2,3]}})
 expect(reloaded.document.nodes.find(node=>node.id==='group')).toMatchObject({backgroundStyle:'repeat',exporter:{collapsed:false}})
 expect(reloaded.document.edges[0]).toMatchObject({fromEnd:'none',toEnd:'arrow',custom:true})
 model.undo();model.undo();expect(model.source).toBe(imported)
})
