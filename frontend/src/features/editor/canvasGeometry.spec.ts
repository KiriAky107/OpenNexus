import { expect, it } from 'vitest'
import { CanvasGeometry, intersects } from './canvasGeometry'
import { edgeGeometry, type CanvasDocument } from './canvasModel'

it('indexes 2000 nodes / 4000 edges and recomputes only four incident edges per drag frame', () => {
  let identityReads = 0
  const document: CanvasDocument = {
    nodes: Array.from({ length: 2000 }, (_, i) => ({ get id() { identityReads++; return `n${i}` }, type: 'text', x: i*400, y: i%5*300, width: 300, height: 180, text: 'Fixture' })),
    edges: Array.from({ length: 2000 }, (_, i) => [1,50].map(step => ({id:`e${i}-${step}`,fromNode:`n${i}`,toNode:`n${(i+step)%2000}`}))).flat(),
  }
  const baseline = document.edges.map(edge => edgeGeometry(edge, document.nodes))
  const baselineReads = identityReads
  identityReads = 0
  const cache = new CanvasGeometry(document), original = cache.project(new Map())
  expect(original.map(item => item.geometry)).toEqual(baseline)
  expect(identityReads).toBe(2000)
  expect(cache.stats).toEqual({indexedNodes:2000,endpointLookups:8000,calculations:4000})
  const node = cache.nodes.get('n1000')!, before = cache.stats.calculations
  for (let frame = 1; frame <= 90; frame++) {
    const projected = cache.project(new Map([['n1000',{...node,x:node.x+frame,y:node.y+frame}]]))
    expect(projected.find(item=>item.edge.id==='e1000-1')!.geometry!.path).not.toBe(original.find(item=>item.edge.id==='e1000-1')!.geometry!.path)
    expect(projected[0]).toBe(original[0])
  }
  expect(cache.stats.calculations-before).toBe(4*90)
  expect(cache.project(new Map())).toEqual(original)
  expect(cache.stats.calculations-before).toBe(4*90)
  expect(baselineReads).toBeGreaterThan(8000000)
  console.log(`CANVAS_GEOMETRY_PERF nodes=2000 edges=4000 baseline_identity_reads=${baselineReads} indexed_identity_reads=2000 indexed_endpoint_lookups=8000 drag_frames=90 drag_calculations=360`)
})

it('keeps crossing curves visible when both endpoints are outside, with padding for arrows and labels', () => {
  const document: CanvasDocument = {nodes:[
    {id:'a',type:'text',text:'A',x:-600,y:0,width:100,height:100},
    {id:'b',type:'text',text:'B',x:600,y:0,width:100,height:100},
    {id:'c',type:'text',text:'C',x:600,y:1000,width:100,height:100},
  ],edges:[{id:'cross',fromNode:'a',toNode:'b'},{id:'away',fromNode:'b',toNode:'c'}]}
  const items=new CanvasGeometry(document).project(new Map()), view={left:-100,right:100,top:-100,bottom:100}
  expect(intersects(items[0]!.geometry!.bounds,view)).toBe(true)
  expect(intersects(items[1]!.geometry!.bounds,view)).toBe(false)
  const label=edgeGeometry({id:'label',fromNode:'a',toNode:'b',label:'标签'.repeat(40)},[
    {id:'a',type:'text',text:'A',x:-400,y:0,width:100,height:100},
    {id:'b',type:'text',text:'B',x:-400,y:600,width:100,height:100},
  ])!
  expect(intersects(label.bounds,{left:-100,right:100,top:300,bottom:400})).toBe(true)
})
