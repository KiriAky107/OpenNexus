import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { admitScriptProtocol, ownedParsedScript, decodedScript, observeCompiledScripts, scriptDiagnostic } from '../android-runtime/original_compiled_scripts.mjs'

const config={mode:'complete',nonce:'00000000-0000-4000-8000-000000000001',original_pid:321,sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const context={run_id:1234,tools_commit:'a'.repeat(40),socket_url:'ws://127.0.0.1:12345/devtools/page/owned'}
const env={GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'github-hosted',RUNNER_OS:'Linux',GITHUB_REPOSITORY:'KiriAky107/OpenNexus',GITHUB_RUN_ID:'1234',GITHUB_SHA:'a'.repeat(40)}
const source='const privateBody="SOURCE_MUST_NOT_ESCAPE";const codes=new Set(["SYNC_CONNECTION_FAILED","SYNC_RESPONSE_INVALID","SYNC_REVIEW_CHANGED"]);function m(x){switch(x){case"SYNC_REVIEW_CHANGED":return"review"}}'
const protocol=()=>({domains:[{domain:'Debugger',commands:[{name:'enable'},{name:'getScriptSource',parameters:[{name:'scriptId'}]}],events:[{name:'scriptParsed'}]}]})
const parsed=(id='1',url='http://tauri.localhost/assets/index-original.js')=>({scriptId:id,url,startLine:0,startColumn:0,executionContextId:1,hasSourceURL:false})
const pages=()=>[{type:'page',title:'OpenNexus',url:'http://tauri.localhost/#/connection',webSocketDebuggerUrl:context.socket_url}]
function fixture({scripts=[parsed()],respond,notificationCount=0,readJson,settleScripts}={}){
  const listeners={},calls=[],reads=[];let closed=0
  const emit=value=>listeners.message({data:JSON.stringify(value)})
  const socket={addEventListener:(name,fn)=>{listeners[name]=fn},send:raw=>{
    const call=JSON.parse(raw);calls.push(call)
    for(let n=0;n<notificationCount;n++)emit({method:'Debugger.unrelated',params:{n}})
    if(call.method==='Debugger.enable')for(const item of scripts)emit({method:'Debugger.scriptParsed',params:item})
    emit({id:call.id,...(respond?respond(call):{result:call.method==='Debugger.enable'?{debuggerId:'not-exported'}:{scriptSource:source}})})
  },close:()=>closed++}
  return {calls,reads,emit,closed:()=>closed,dependencies:{readJson:async url=>{
    reads.push(url);return readJson?readJson(url):url.endsWith('/json/list')?pages():protocol()
  },socketFactory:async()=>socket,settleScripts:async()=>settleScripts?.(emit)}}
}

test('only declared Debugger source observation methods and parsed events are admitted',()=>{
  admitScriptProtocol(protocol())
  for(const change of [v=>v.domains=[],v=>v.domains.push({...v.domains[0]}),
    v=>v.domains[0].commands.pop(),v=>v.domains[0].commands[1].parameters[0].optional=true,
    v=>v.domains[0].events=[]]){
    const value=protocol();change(value);assert.throws(()=>admitScriptProtocol(value))
  }
})
test('parsed modules require owned original external assets, not injected source URLs or live edits',()=>{
  assert.deepEqual(ownedParsedScript(parsed()),{id:'1',url:parsed().url,path:'/assets/index-original.js'})
  for(const url of ['https://foreign.invalid/assets/a.js','http://tauri.localhost/assets/a.js?private=1',
    'http://tauri.localhost/assets/a.js#private','http://u:p@tauri.localhost/assets/a.js','http://tauri.localhost/private.js']){
    assert.equal(ownedParsedScript(parsed('1',url)),null)
  }
  for(const extra of [{hasSourceURL:true},{isLiveEdit:true},{executionContextId:0},{executionContextId:true},
    {scriptId:'PRIVATE ID'},{startLine:1},{startColumn:1}])assert.throws(()=>ownedParsedScript({...parsed(),...extra}))
})
test('decoded V8 source is exact UTF-8 text without bytecode, extra fields or raw artifact export',()=>{
  const value='原始脚本 😀';assert.deepEqual(decodedScript({scriptSource:value}).bytes,Buffer.from(value))
  for(const value of [{scriptSource:''},{scriptSource:source,bytecode:'PRIVATE'},{scriptSource:source,body:'PRIVATE'},
    {scriptSource:'\ud800'},{source}])assert.throws(()=>decodedScript(value))
})
test('actual parsed-source path enables Debugger explicitly and sends no evaluation, pause or native input',async()=>{
  const f=fixture();const result=await observeCompiledScripts(config,context,env,f.dependencies)
  assert.deepEqual(f.calls.map(c=>c.method),['Debugger.enable','Debugger.getScriptSource'])
  assert.deepEqual(f.calls[0].params,{});assert.deepEqual(f.calls[1].params,{scriptId:'1'})
  assert.equal(result.Debugger_enabled,true);assert.equal(result.Debugger_pause_breakpoint_or_evaluation_sent,false)
  assert.equal(result.application_or_native_IPC_sent,false);assert.equal(result.page_fetch_or_CSP_change_sent,false)
  assert.equal(result.resource_read_transport,'Debugger.getScriptSource')
  assert.equal(result.resource_bytes_transferred_to_owned_reader,true);assert.equal(result.resource_bytes_written_to_artifacts,false)
  assert.equal(result.assets[0].sha256,createHash('sha256').update(source).digest('hex'))
  assert.equal(result.assets[0].bytes,Buffer.byteLength(source));assert.equal(f.closed(),1)
  assert.equal(JSON.stringify(result).includes('SOURCE_MUST_NOT_ESCAPE'),false)
  assert.equal(JSON.stringify(result).includes('not-exported'),false)
})
test('foreign parsed modules are ignored without reading their source or changing CSP',async()=>{
  const f=fixture({scripts:[parsed('foreign','https://foreign.invalid/assets/private.js'),parsed()]})
  await observeCompiledScripts(config,context,env,f.dependencies)
  assert.deepEqual(f.calls.filter(c=>c.method==='Debugger.getScriptSource').map(c=>c.params.scriptId),['1'])
})
test('duplicate parsed notifications for one original module do not cause another read',async()=>{
  const f=fixture({scripts:[parsed(),parsed()]});await observeCompiledScripts(config,context,env,f.dependencies)
  assert.equal(f.calls.length,2)
})
test('ambiguous original URL or reused script ID refuses before reading any body',async()=>{
  for(const scripts of [[parsed(),parsed('2')],[parsed(),parsed('1','http://tauri.localhost/assets/other.js')]]){
    const f=fixture({scripts})
    await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies),error=>{
      assert.equal(scriptDiagnostic(error).reason,'script-ambiguous');return true
    });assert.equal(f.calls.length,1);assert.equal(f.closed(),1)
  }
})
test('original source failure is not retried and raw protocol content is discarded',async()=>{
  const f=fixture({respond:c=>c.method==='Debugger.enable'?{result:{}}:{error:{message:'PRIVATE_ERROR_CONTENT'}}})
  await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies),error=>{
    const d=scriptDiagnostic(error);assert.equal(d.stage,'script-content');assert.equal(d.reason,'reply-refused')
    assert.equal(JSON.stringify(d).includes('PRIVATE'),false);return true
  });assert.equal(f.calls.length,2);assert.equal(f.closed(),1)
})
test('no owned compiled module stays unconfirmed without fallback fetch or another enable',async()=>{
  const f=fixture({scripts:[]})
  await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies),e=>scriptDiagnostic(e).reason==='no-owned-script')
  assert.equal(f.calls.length,1)
})
test('an unconfirmed enable reply never permits a source read or exports its extra fields',async()=>{
  for(const reply of [null,{body:'PRIVATE_ENABLE'},{debuggerId:''}]){
    const f=fixture({respond:()=>({result:reply})})
    await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies),error=>{
      assert.equal(JSON.stringify(scriptDiagnostic(error)).includes('PRIVATE'),false);return true
    });assert.equal(f.calls.length,1)
  }
})
test('bounded candidate set refuses before any source request',async()=>{
  const scripts=Array.from({length:25},(_,n)=>parsed(String(n),'http://tauri.localhost/assets/'+n+'.js'))
  const f=fixture({scripts})
  await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies),error=>{
    const d=scriptDiagnostic(error);assert.equal(d.reason,'bound');assert.equal(d.script_summary.owned_candidates,25);return true
  });assert.equal(f.calls.length,1)
})
test('non-hosted context refuses before a page, protocol or socket observation',async()=>{
  const f=fixture();await assert.rejects(observeCompiledScripts(config,context,{...env,RUNNER_ENVIRONMENT:'self-hosted'},f.dependencies))
  assert.equal(f.reads.length,0);assert.equal(f.calls.length,0)
})
test('foreign or ambiguous page refuses before Debugger enable',async()=>{
  const f=fixture({readJson:async()=>[...pages(),{type:'page',title:'Other',url:'https://foreign.invalid'}]})
  await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies));assert.equal(f.calls.length,0)
})
test('unexpected pause never resumes, evaluates or substitutes an original callback',async()=>{
  const f=fixture({settleScripts:emit=>emit({method:'Debugger.paused',params:{callFrames:[{PRIVATE:'NO_EXPORT'}]}})})
  await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies),error=>{
    const d=scriptDiagnostic(error);assert.equal(d.reason,'unexpected-pause');assert.equal(d.script_summary.unexpected_pause_observed,true)
    assert.equal(JSON.stringify(d).includes('PRIVATE'),false);return true
  });assert.deepEqual(f.calls.map(x=>x.method),['Debugger.enable'])
})
test('bounded notification flood cannot hang the reader or cause a resend',async()=>{
  const f=fixture({notificationCount:1025})
  await assert.rejects(observeCompiledScripts(config,context,env,f.dependencies));assert.equal(f.calls.length,1);assert.equal(f.closed(),1)
})
test('late initial parsed notifications are observed after the sole enable reply',async()=>{
  const f=fixture({scripts:[],settleScripts:emit=>emit({method:'Debugger.scriptParsed',params:parsed()})})
  await observeCompiledScripts(config,context,env,f.dependencies);assert.equal(f.calls.length,2)
})
test('arbitrary errors expose only allowlisted diagnostics',()=>{
  assert.deepEqual(scriptDiagnostic(Object.assign(Error('PRIVATE'),{stage:'PRIVATE',reason:'PRIVATE'})),
    {error:'OWNED_COMPILED_RESOURCE_UNCONFIRMED',stage:'admission',reason:'unconfirmed'})
})
