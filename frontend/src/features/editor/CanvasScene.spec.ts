// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect,it } from 'vitest'
import CanvasScene from './CanvasScene.vue'
import { CanvasGeometry } from './canvasGeometry'
import type { CanvasNode, CanvasEdge } from './canvasModel'

it('names an unnamed visible connection from the full node index and refreshes a renamed offscreen endpoint',async()=>{
  const nodes:CanvasNode[]=[{id:'a',type:'link',x:0,y:0,width:120,height:100,url:'https://example.invalid'},
    {id:'b',type:'file',x:900,y:0,width:120,height:100,file:'资料/原笔记😀.md'}]
  const edges:CanvasEdge[]=[{id:'edge',fromNode:'a',toNode:'b',label:''}]
  const graph=new CanvasGeometry({nodes,edges}),projected=graph.project(new Map())
  const wrapper=mount(CanvasScene,{props:{nodes:[nodes[0]!],nodeLookup:graph.nodes,nodeCount:2,edges:projected,
    order:graph.order,pan:{x:0,y:0},zoom:1,selected:[],selectedEdge:'',previews:{},readOnly:false,label:'Free canvas',emptyLabel:'Empty'}})
  expect(wrapper.get('g[role="button"]').attributes('aria-label')).toContain('example.invalid → 原笔记😀.md')
  const before=JSON.stringify({nodes,edges})
  await wrapper.setProps({nodeLookup:new Map([['a',nodes[0]!],['b',{...nodes[1]!,file:'资料/新名字😀.md'}]])})
  expect(wrapper.get('g[role="button"]').attributes('aria-label')).toContain('example.invalid → 新名字😀.md')
  expect(JSON.stringify({nodes,edges})).toBe(before)
  expect(wrapper.emitted('selectEdge')).toBeUndefined()
  wrapper.unmount()
})
