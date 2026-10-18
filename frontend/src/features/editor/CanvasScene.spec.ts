// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect,it } from 'vitest'
import { nextTick } from 'vue'
import { appLocale } from '../../i18n'
import CanvasScene from './CanvasScene.vue'
import { CanvasGeometry } from './canvasGeometry'
import type { CanvasNode, CanvasEdge } from './canvasModel'

it('updates the connection layer accessible name with the interface language while preserving graph content',async()=>{
  const language=appLocale.value
  const nodes:CanvasNode[]=[{id:'original-node',type:'text',x:0,y:0,width:120,height:100,text:'原文 😀'}]
  const edges:CanvasEdge[]=[]
  const original=JSON.stringify({nodes,edges})
  appLocale.value='en'
  const wrapper=mount(CanvasScene,{props:{nodes,nodeCount:1,edges:[],order:new Map([['original-node',0]]),
    pan:{x:0,y:0},zoom:1,selected:[],selectedEdge:'',previews:{},readOnly:false,label:'Free canvas',emptyLabel:'Empty'}})
  try {
    expect(wrapper.get('svg.canvas-edges').attributes('aria-label')).toBe('Node connections')
    appLocale.value='zh-CN';await nextTick()
    expect(wrapper.get('svg.canvas-edges').attributes('aria-label')).toBe('节点连接')
    expect(JSON.stringify({nodes,edges})).toBe(original)
    expect(wrapper.emitted('selectEdge')).toBeUndefined()
    expect(wrapper.emitted('nodePointer')).toBeUndefined()
  } finally { wrapper.unmount();appLocale.value=language }
})

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
