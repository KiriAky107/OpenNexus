// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CanvasInspector from './CanvasInspector.vue'
import type { CanvasNode, CanvasEdge } from './canvasModel'

const nodes:CanvasNode[]=[
  {id:'link',type:'link',x:0,y:0,width:120,height:100,url:'https://example.invalid/owned-canvas'},
  {id:'file',type:'file',x:150,y:0,width:120,height:100,file:'资料/原笔记😀.md'},
]
it.each([undefined,'',' \t\n '])('shows readable endpoints for an unnamed connection (%j) and selects its original ID',async label=>{
  const edges:CanvasEdge[]=[{id:'original-edge',fromNode:'link',toNode:'file',label,vendor:{keep:true}}]
  const before=JSON.stringify({nodes,edges})
  const wrapper=mount(CanvasInspector,{props:{nodes,edges,selected:['link'],language:'en',touch:true}})
  const connection=wrapper.findAll('.canvas-node-list').at(-1)!.get('button')
  expect(connection.text()).toBe('example.invalid → 原笔记😀.md')
  await connection.trigger('click')
  expect(wrapper.emitted('selectEdge')).toEqual([['original-edge']])
  expect(JSON.stringify({nodes,edges})).toBe(before)
  wrapper.unmount()
})
it('keeps the original nonblank label as plain text without changing it or rendering markup',()=>{
  const label='  原说明 <script>bad()</script> 😀  '
  const edges:CanvasEdge[]=[{id:'named',fromNode:'link',toNode:'file',label}]
  const wrapper=mount(CanvasInspector,{props:{nodes,edges,selected:[],language:'zh-CN'}})
  const connection=wrapper.findAll('.canvas-node-list').at(-1)!.get('button')
  expect(connection.element.textContent).toBe(label)
  expect(connection.find('script').exists()).toBe(false)
  expect(edges[0]!.label).toBe(label)
  wrapper.unmount()
})
