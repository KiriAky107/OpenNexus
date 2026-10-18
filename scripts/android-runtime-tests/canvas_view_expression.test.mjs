import { test } from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { canvasViewExpression,canvasViewIds } from '../android-runtime/canvas_view_expression.mjs'

const dispatch='278eccde-7cda-42a4-8a3b-132c7bbba901'
const box=(left,top,width,height)=>({left,top,right:left+width,bottom:top+height,width,height})
function fixture() {
  const forbid=()=>{throw Error('Private content or mutation was accessed')}
  const element=(id,b,selected=false)=>Object.defineProperties({
    getBoundingClientRect:()=>({...b}),getAttribute:name=>name==='data-node-id'||name==='data-edge'?id:null,
    classList:{contains:name=>name==='selected'&&selected},querySelectorAll:()=>[],
    click:forbid,append:forbid,setAttribute:forbid,
  },{textContent:{get:forbid},innerHTML:{get:forbid},value:{get:forbid}})
  const a=element(canvasViewIds.a,box(100,360,200,175),true),b=element(canvasViewIds.b,box(350,325,100,140))
  const edge=element(canvasViewIds.edge,box(290,370,70,100)),world=element(null,box(20,285,0,0))
  const host=element(null,box(0,250,500,650))
  host.querySelectorAll=selector=>selector===':scope > .canvas-world'?[world]:selector==='.canvas-node'?[a,b]:selector==='g[data-edge]'?[edge]:[]
  const state={hosts:[host],saves:[{disabled:true}],nodes:[a,b],edge,host,world}
  const context={document:{querySelectorAll:selector=>selector==='.canvas-editor .canvas-viewport'?state.hosts:selector==='.editor-header .save-button'?state.saves:[]},
    getComputedStyle:()=>({transform:'matrix(1, 0, 0, 1, 20, 35)'}),
    window:{visualViewport:{offsetLeft:0,offsetTop:0,width:500,height:900,scale:1}},
    innerWidth:500,innerHeight:900,devicePixelRatio:3,scrollX:0,scrollY:0,
    performance:{now:()=>100,timeOrigin:100000},__TAURI_INTERNALS__:{invoke:forbid}}
  return {state,context}
}
function capture(context,stage='before'){return JSON.parse(vm.runInNewContext(canvasViewExpression(dispatch,stage),context,{timeout:500}))}

test('reads only declared geometry, fixed identity and the original disabled Save',()=>{
  const {state,context}=fixture();const before=JSON.stringify(state.nodes.map(node=>node.getBoundingClientRect()))
  const observed=capture(context)
  assert.equal(observed.dispatch_id,dispatch);assert.equal(observed.stage,'before')
  assert.deepEqual(observed.nodes.map(node=>node.id),[canvasViewIds.a,canvasViewIds.b])
  assert.deepEqual(observed.world.matrix,[1,0,0,1,20,35]);assert.equal(observed.save.enabled,false)
  assert.equal(JSON.stringify(state.nodes.map(node=>node.getBoundingClientRect())),before)
  assert.deepEqual(Object.keys(observed.nodes[0]).sort(),['id','rect','resize','selected'])
})
test('scope and nonce refuse arbitrary source text or unrecognized phases',()=>{
  for(const nonce of ['',dispatch+"');invoke()",'unrelated'])assert.throws(()=>canvasViewExpression(nonce,'before'))
  for(const stage of ['input','after;click()',''])assert.throws(()=>canvasViewExpression(dispatch,stage))
})
test('refuses ambiguous view roots and additional or foreign nodes',()=>{
  for(const mutate of [state=>state.hosts.push(state.host),state=>state.hosts.length=0,
    state=>state.nodes[1].getAttribute=()=>canvasViewIds.a,
    state=>state.host.querySelectorAll=selector=>selector==='.canvas-node'?[...state.nodes,state.nodes[0]]:selector===':scope > .canvas-world'?[state.world]:[state.edge]]){
    const {state,context}=fixture();mutate(state);assert.throws(()=>capture(context),/CANVAS_VIEW_GEOMETRY_UNAVAILABLE/)
  }
})
test('refuses missing visual viewport, invalid matrices and missing Save identity',()=>{
  for(const mutate of [(state,context)=>context.window.visualViewport=null,
    (state,context)=>context.getComputedStyle=()=>({transform:'none'}),
    (state,context)=>context.getComputedStyle=()=>({transform:'matrix(1, 0, 0, 1, Infinity, 0)'}),
    state=>state.saves.length=0]){
    const {state,context}=fixture();mutate(state,context);assert.throws(()=>capture(context),/CANVAS_VIEW_GEOMETRY_UNAVAILABLE/)
  }
})
test('observes independent after geometry without performing input or changing nodes',()=>{
  const {state,context}=fixture();state.nodes[0].getBoundingClientRect=()=>box(110+2/3,368,200,175)
  context.getComputedStyle=()=>({transform:'matrix(1, 0, 0, 1, 30.6666666667, 43)'})
  const after=capture(context,'after');assert.equal(after.stage,'after');assert.equal(after.nodes[0].rect.width,200)
  assert.ok(Math.abs(after.nodes[0].rect.left-110-2/3)<1e-8)
})
