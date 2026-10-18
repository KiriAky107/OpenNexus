import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { compiledMarkers, decodedResource, resourceCandidates, resourceTreeSummary, validateResources, observeCompiledResources, resourceDiagnostic } from '../android-runtime/original_compiled_resources.mjs'

const config={mode:'complete',nonce:'00000000-0000-4000-8000-000000000001',original_pid:321,sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const context={run_id:1234,tools_commit:'a'.repeat(40),socket_url:'ws://127.0.0.1:12345/devtools/page/owned'}
const env={GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'github-hosted',RUNNER_OS:'Linux',GITHUB_REPOSITORY:'KiriAky107/OpenNexus',GITHUB_RUN_ID:'1234',GITHUB_SHA:'a'.repeat(40)}
const url='http://tauri.localhost/assets/index-original.js'
const source='const privateBodyMustNotEscape="NO_RAW_CODE_EXPORT";const s=new Set(["SYNC_CONNECTION_FAILED","SYNC_RESPONSE_INVALID","SYNC_REVIEW_CHANGED"]);function m(x){switch(x){case"SYNC_REVIEW_CHANGED":return "review"}}'
const pages=()=>[{type:'page',title:'OpenNexus',url:'http://tauri.localhost/#/connection',webSocketDebuggerUrl:context.socket_url}]
const tree=()=>({frameTree:{frame:{id:'owned-frame',url:'http://tauri.localhost/#/connection'},resources:[{type:'Script',url}]}})
const summary=()=>({original_origin:'http://tauri.localhost',resource_read_only:true,declared_safe_code_list:true,declared_presentation_case:true,
  assets:[{path:'/assets/index-original.js',bytes:123,sha256:'c'.repeat(64),declared_safe_code_list:true,declared_presentation_case:true}]})
function fixture({respond,notifications=1}={}){
  const listeners={},calls=[];let closed=0
  const socket={addEventListener:(name,fn)=>{listeners[name]=fn},send:raw=>{
    const call=JSON.parse(raw);calls.push(call)
    for(let n=0;n<notifications;n++)listeners.message({data:JSON.stringify({method:'Page.notification',params:{n}})})
    const value=respond?respond(call):{result:call.method==='Page.getResourceTree'?tree():{content:source,base64Encoded:false}}
    listeners.message({data:JSON.stringify({id:call.id,...value})})
  },close:()=>{closed++}}
  return {calls,closed:()=>closed,dependencies:{readJson:async()=>pages(),socketFactory:async()=>socket}}
}

test('compiled markers require the safe list and switch shape rather than one bare error literal',()=>{
  assert.deepEqual(compiledMarkers(source),{declared_safe_code_list:true,declared_presentation_case:true})
  assert.deepEqual(compiledMarkers('"SYNC_REVIEW_CHANGED"'),{declared_safe_code_list:false,declared_presentation_case:false})
})

test('only already listed same-origin root-frame JS resources are admitted',()=>{
  const value=tree();value.frameTree.resources.push({type:'XHR',url:'http://tauri.localhost/private'},
    {type:'Script',url:'https://foreign.invalid/a.js'},{type:'Script',url:url+'?private=query'},{type:'Script',url})
  value.frameTree.childFrames=[{frame:{id:'foreign',url:'https://foreign.invalid/'},resources:[{type:'Script',url}]}]
  assert.deepEqual(resourceCandidates(value),{frameId:'owned-frame',urls:[url]})
  for(const change of [v=>v.frameTree.frame.url='https://foreign.invalid/',v=>v.frameTree.frame.id='invalid id',
    v=>v.frameTree.resources=[],v=>v.frameTree.resources=Array.from({length:25},(_,n)=>({type:'Script',url:'http://tauri.localhost/assets/'+n+'.js'}))]){
    const v=tree();change(v);assert.throws(()=>resourceCandidates(v))
  }
})

test('decoded CDP content preserves UTF-8 and canonical base64 without accepting hidden fields',()=>{
  const body=Buffer.from('原始编译内容 😀\n')
  assert.deepEqual(decodedResource({content:body.toString('utf8'),base64Encoded:false}).bytes,body)
  assert.deepEqual(decodedResource({content:body.toString('base64'),base64Encoded:true}).bytes,body)
  for(const v of [{content:'',base64Encoded:false},{content:'not base64!',base64Encoded:true},
    {content:'//8=',base64Encoded:true},{content:source,base64Encoded:false,body:'NO_EXPORT'}])assert.throws(()=>decodedResource(v))
})

test('resource summaries refuse foreign paths, duplicate hashes, missing markers and raw bodies',()=>{
  validateResources(summary())
  for(const change of [v=>v.assets[0].path='https://foreign.invalid/a.js',v=>v.assets.push({...v.assets[0]}),
    v=>v.assets[0].bytes=9*1024**2,v=>v.assets[0].body='NO_EXPORT',v=>v.declared_safe_code_list=false,
    v=>v.assets[0].declared_presentation_case=false]){
    const v=summary();change(v);assert.throws(()=>validateResources(v))
  }
})

test('fixed IPC-only CSP needs no page fetch, evaluation, debugger or policy change to read cached code',async()=>{
  const policy=JSON.parse(readFileSync(new URL('../../frontend/mobile/src-tauri/tauri.conf.json',import.meta.url),'utf8')).app.security.csp
  assert.equal(policy.split(';').map(x=>x.trim()).find(x=>x.startsWith('connect-src ')), 'connect-src ipc: http://ipc.localhost')
  const f=fixture();const result=await observeCompiledResources(config,context,env,f.dependencies)
  assert.deepEqual(f.calls.map(x=>x.method),['Page.getResourceTree','Page.getResourceContent'])
  assert.deepEqual(f.calls[1].params,{frameId:'owned-frame',url})
  assert.equal(result.assets[0].sha256,createHash('sha256').update(source).digest('hex'))
  assert.equal(result.assets[0].bytes,Buffer.byteLength(source));assert.equal(f.closed(),1)
  assert.equal(result.Debugger_enabled,false);assert.equal(result.page_fetch_or_CSP_change_sent,false)
  assert.equal(result.resource_bytes_transferred_to_owned_reader,true);assert.equal(result.resource_bytes_written_to_artifacts,false)
  assert.equal(result.resource_read_transport,'Page.getResourceContent');assert.equal(JSON.stringify(result).includes('NO_RAW_CODE_EXPORT'),false)
})

test('original CDP resource failure keeps a safe stage and never retries or exports raw protocol text',async()=>{
  const f=fixture({respond:call=>call.method==='Page.getResourceTree'?{result:tree()}:{error:{message:'PRIVATE_PROTOCOL_BODY'}}})
  await assert.rejects(observeCompiledResources(config,context,env,f.dependencies),error=>{
    assert.deepEqual(resourceDiagnostic(error),{error:'OWNED_COMPILED_RESOURCE_UNCONFIRMED',stage:'resource-content',reason:'reply-refused',tree_summary:resourceTreeSummary(tree())})
    assert.equal(JSON.stringify(resourceDiagnostic(error)).includes('PRIVATE'),false);return true
  })
  assert.equal(f.calls.length,2);assert.equal(f.closed(),1)
  assert.deepEqual(resourceDiagnostic(Object.assign(Error('PRIVATE'),{stage:'PRIVATE',reason:'PRIVATE'})),
    {error:'OWNED_COMPILED_RESOURCE_UNCONFIRMED',stage:'admission',reason:'unconfirmed'})
})

test('resource tree diagnostics retain only bounded classifications, never raw URLs or frame identifiers',()=>{
  const value=tree();value.frameTree.frame.id='PRIVATE_FRAME'
  value.frameTree.resources.push({type:'Other',url}, {type:'PRIVATE_TYPE',url:'https://foreign.invalid/PRIVATE_TOKEN'},
    {type:'Script',url:'http://tauri.localhost/assets/private.js?PRIVATE_TOKEN'})
  const result=resourceTreeSummary(value)
  assert.equal(result.types.Script,2);assert.equal(result.types.Other,1);assert.equal(result.types.Unknown,1)
  assert.equal(result.owned_script_candidates,1);assert.equal(result.owned_other_js_candidates,1)
  assert.equal(JSON.stringify(result).includes('PRIVATE'),false)
  const many=tree();many.frameTree.resources=Array.from({length:1000},()=>({type:'Script',url}))
  const bounded=resourceTreeSummary(many);assert.equal(bounded.resource_count_bounded,513);assert.equal(bounded.types.Script,512)
})

test('missing Script resources refuse without weakening candidate types and keep safe actual tree shape',async()=>{
  const value=tree();value.frameTree.resources[0].type='Other'
  const f=fixture({respond:()=>({result:value})})
  await assert.rejects(observeCompiledResources(config,context,env,f.dependencies),error=>{
    const result=resourceDiagnostic(error)
    assert.equal(result.stage,'resource-tree');assert.equal(result.reason,'no-owned-script')
    assert.equal(result.tree_summary.types.Other,1);assert.equal(result.tree_summary.owned_other_js_candidates,1)
    return true
  })
  assert.equal(f.calls.length,1);assert.equal(f.closed(),1)
})

test('local or foreign page is refused before a resource socket is opened',async()=>{
  let reads=0,connections=0
  const dependencies={readJson:async()=>{reads++;return [{type:'page',title:'OpenNexus',url:'https://foreign.invalid/',webSocketDebuggerUrl:context.socket_url}]},
    socketFactory:async()=>{connections++}}
  await assert.rejects(observeCompiledResources(config,context,{},dependencies));assert.equal(reads,0)
  await assert.rejects(observeCompiledResources(config,context,env,dependencies));assert.equal(reads,1);assert.equal(connections,0)
})

test('notification flood stays bounded and cannot replace the original resource reply',async()=>{
  const f=fixture({notifications:513})
  await assert.rejects(observeCompiledResources(config,context,env,f.dependencies))
  assert.equal(f.calls.length,1);assert.equal(f.closed(),1)
})
