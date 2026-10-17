import assert from 'node:assert/strict'
import test from 'node:test'
import vm from 'node:vm'
import { admitCycleResult, cycleExpression, cycleResultExpression, installCycleObserverExpression, touchPreview } from './sync_preview_touch.mjs'

const nonce='a326b9cd-165c-4444-b1c2-5b05b48f1994'

test('cycle observer refuses a second original, unrelated roots and untrusted or repeated click evidence',()=>{
  const handlers={};class Element{}
  const button=new Element();button.textContent='Sync one cycle now';button.closest=()=>button
  const root={contains:e=>e===button,addEventListener:(kind,handler)=>{handlers[kind]=handler}}
  const window={};const context={window,document:{querySelectorAll:()=>[root]},Element}
  assert.equal(vm.runInNewContext(installCycleObserverExpression(nonce),context).installed,true)
  assert.throws(()=>vm.runInNewContext(installCycleObserverExpression(nonce),context))
  for(const kind of ['touchstart','touchend','click'])handlers[kind]({target:button,isTrusted:true})
  const original=JSON.parse(JSON.stringify(vm.runInNewContext(cycleResultExpression(nonce),context)))
  assert.deepEqual(admitCycleResult(original,nonce),original)
  assert.equal(JSON.stringify(original).includes('Sync one cycle now'),false)
  for(const value of [{...original,nonce:'other'},{...original,overflow:true},
    {...original,events:original.events.map(e=>({...e,trusted:false}))},
    {...original,events:[...original.events,original.events[2]]}])assert.throws(()=>admitCycleResult(value,nonce))
  assert.throws(()=>vm.runInNewContext(installCycleObserverExpression(nonce),{...context,window:{},document:{querySelectorAll:()=>[root,root]}}))
})

test('cycle input takes one stable hit-tested DOM target rather than stale native accessibility bounds',async()=>{
  let time=0,reads=0;const calls=[],trace={}
  const cdp=async(method,params)=>{
    calls.push([method,params,time])
    if(method==='Runtime.evaluate'){
      assert.equal(params.expression,cycleExpression(nonce));reads++
      const top=reads<3?480:339
      return {result:{value:{observer:true,focused:true,visible:true,busy:false,width:412,height:915,
        targets:[{left:214,top,width:148,height:44,enabled:true,visible:true,hit:true}]}}}
    }
    return {}
  }
  await touchPreview(cdp,nonce,trace,{cycle:true,now:()=>time,sleep:async ms=>{time+=ms}})
  const input=calls.filter(x=>x[0]==='Input.dispatchTouchEvent')
  assert.deepEqual(input.map(x=>x[1].type),['touchStart','touchEnd'])
  assert.equal(input[0][1].touchPoints[0].y,361);assert.ok(input[0][2]>=480)
  assert.equal(trace.actual_UI_or_native_result_verified,false)
})

test('unknown cycle touch result never falls back or sends another original input',async()=>{
  for(const failed of ['touchStart','touchEnd']){
    let time=0;const inputs=[],trace={}
    const cdp=async(method,params)=>{
      if(method==='Runtime.evaluate')return {result:{value:{observer:true,focused:true,visible:true,busy:false,width:412,height:915,
        targets:[{left:214,top:339,width:148,height:44,enabled:true,visible:true,hit:true}]}}}
      inputs.push(params.type);if(params.type===failed)throw Error('Original result unknown');return {}
    }
    await assert.rejects(touchPreview(cdp,nonce,trace,{cycle:true,now:()=>time,sleep:async ms=>{time+=ms}}))
    assert.deepEqual(inputs,failed==='touchStart'?['touchStart']:['touchStart','touchEnd'])
  }
  for(const source of [cycleExpression(nonce),installCycleObserverExpression(nonce),cycleResultExpression(nonce)])
    assert.doesNotMatch(source,/\.click\(|\.value\b|invoke\(|pairInvoke|tauri/i)
})
