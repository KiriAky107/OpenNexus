import test from 'node:test'
import assert from 'node:assert/strict'
import { compiledMarkers, resourceExpression, validateResources, observeCompiledResources } from '../android-runtime/original_compiled_resources.mjs'

const config={mode:'complete',nonce:'00000000-0000-4000-8000-000000000001',original_pid:321,sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const context={run_id:1234,tools_commit:'a'.repeat(40),socket_url:'ws://127.0.0.1:12345/devtools/page/owned'}
const env={GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'github-hosted',RUNNER_OS:'Linux',GITHUB_REPOSITORY:'KiriAky107/OpenNexus',GITHUB_RUN_ID:'1234',GITHUB_SHA:'a'.repeat(40)}
const original=()=>({original_origin:'http://tauri.localhost',resource_read_only:true,declared_safe_code_list:true,declared_presentation_case:true,
  assets:[{path:'/assets/MobileSyncConnection-original.js',bytes:123,sha256:'c'.repeat(64),declared_safe_code_list:true,declared_presentation_case:true}]})

test('compiled code presence needs the actual safe list and switch shape, not a bare native error literal',()=>{
  const text='const s=new Set(["SYNC_CONNECTION_FAILED","SYNC_RESPONSE_INVALID","SYNC_REVIEW_CHANGED"]);function m(x){switch(x){case"SYNC_REVIEW_CHANGED":return "review"}}'
  assert.deepEqual(compiledMarkers(text),{declared_safe_code_list:true,declared_presentation_case:true})
  assert.deepEqual(compiledMarkers('"SYNC_REVIEW_CHANGED"'),{declared_safe_code_list:false,declared_presentation_case:false})
  new Function('return '+resourceExpression)
  for(const disallowed of ['__TAURI_INTERNALS__','.invoke(','.click(','.innerHTML','document.cookie','localStorage','Debugger.enable'])
    assert.equal(resourceExpression.includes(disallowed),false)
})

test('resource summaries reject foreign paths, duplicates, missing markers, raw body and unbounded content',()=>{
  validateResources(original())
  for(const change of [v=>v.assets[0].path='https://external.invalid/a.js',v=>v.assets[0].path='/assets/a.js?token=x',
    v=>v.assets.push({...v.assets[0]}),v=>v.assets[0].bytes=9*1024**2,v=>v.assets[0].sha256='bad',
    v=>v.assets[0].body='DO_NOT_EXPORT',v=>v.declared_safe_code_list=false,v=>v.assets[0].declared_presentation_case=false]){
    const v=original();change(v);assert.throws(()=>validateResources(v))
  }
})

test('hosted read-only connection evaluates one fixed expression and never enables debugger, invokes native or exports resource bytes',async()=>{
  const calls=[];let closed=0;const listeners={}
  const socket={addEventListener:(name,fn)=>{listeners[name]=fn},send:raw=>{
    const value=JSON.parse(raw);calls.push(value)
    listeners.message({data:JSON.stringify({id:1,result:{result:{type:'object',value:original()}}})})
  },close:()=>{closed++}}
  const result=await observeCompiledResources(config,context,env,{readJson:async()=>[{type:'page',title:'OpenNexus',url:'http://tauri.localhost/#/connection',webSocketDebuggerUrl:context.socket_url}],socketFactory:async()=>socket})
  assert.equal(calls.length,1);assert.equal(calls[0].method,'Runtime.evaluate');assert.equal(calls[0].params.expression,resourceExpression)
  assert.equal(closed,1);assert.equal(result.nonce,config.nonce);assert.equal(result.Debugger_enabled,false)
  assert.equal(result.application_or_native_IPC_sent,false);assert.equal(result.raw_resource_bytes_exported,false)
})

test('local host or another origin is rejected before a resource connection and without an external fallback',async()=>{
  let reads=0,connections=0
  const dependencies={readJson:async()=>{reads++;return [{type:'page',title:'OpenNexus',url:'https://external.invalid/',webSocketDebuggerUrl:context.socket_url}]},socketFactory:async()=>{connections++}}
  await assert.rejects(observeCompiledResources(config,context,{},dependencies));assert.equal(reads,0)
  await assert.rejects(observeCompiledResources(config,context,env,dependencies));assert.equal(reads,1);assert.equal(connections,0)
})
