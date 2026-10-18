import test from 'node:test'
import assert from 'node:assert/strict'
import { connectOriginalOutcome, OriginalOutcomeTransport } from '../android-runtime/original_outcome_transport.mjs'
import { condition, originalCallbackExpression } from '../android-runtime/original_outcome_gate.mjs'

const config={mode:'complete',nonce:'00000000-0000-4000-8000-000000000001',original_pid:321,sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const context={run_id:1234,tools_commit:'a'.repeat(40),socket_url:'ws://127.0.0.1:12345/devtools/page/original'}
const env={GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'github-hosted',RUNNER_OS:'Linux',GITHUB_REPOSITORY:'KiriAky107/OpenNexus',GITHUB_RUN_ID:'1234',GITHUB_SHA:'a'.repeat(40)}
const exit={original_pid:321,original_pid_before_stop:321,force_stop_returncode:0,original_pidof_returncode:1,original_pidof_stdout_empty:true,original_pidof_stderr_empty:true,owned_emulator_still_connected:true,same_original_immutable_installed_app:true}
const protocol={domains:[{domain:'Debugger',commands:['enable','evaluateOnCallFrame','resume'].map(name=>({name})).concat([{name:'setBreakpointOnFunctionCall',parameters:[{name:'objectId'},{name:'condition'}]}])},{domain:'Runtime',commands:[{name:'evaluate'}]}]}
class Socket{
  constructor(){this.events={};this.sent=[];this.closes=0;this.respond=true;this.invalidBreakpoint=false;this.domainReply={domains:[{name:'Debugger',version:'1.3'},{name:'Runtime',version:'1.3'}]}}
  addEventListener(name,fn){(this.events[name]??=[]).push(fn)}
  emit(name,event){for(const fn of this.events[name]??[])fn(event)}
  message(value){this.emit('message',{data:JSON.stringify(value)})}
  send(raw){
    const value=JSON.parse(raw);this.sent.push(value)
    if(!this.respond)return
    let result={}
    if(value.method==='Schema.getDomains')result=this.domainReply
    if(value.method==='Runtime.evaluate')result={result:{type:'function',objectId:'original-callback'}}
    if(value.method==='Debugger.setBreakpointOnFunctionCall')result={breakpointId:this.invalidBreakpoint?'x'.repeat(300):'original-breakpoint'}
    if(value.method==='Debugger.evaluateOnCallFrame')result={result:{type:'boolean',value:true}}
    this.message({id:value.id,result})
  }
  close(){this.closes++;this.emit('close',{})}
}
function handlers(){const paused=[],faults=[];let closed=0;return {paused,faults,closed:()=>closed,callbacks:{paused:value=>paused.push(value),fault:value=>faults.push(value),closed:()=>{closed++}}}}
function direct(options={}){const socket=new Socket(),h=handlers();const transport=new OriginalOutcomeTransport(config,socket,h.callbacks.paused,h.callbacks.closed,h.callbacks.fault,options);return {socket,h,transport}}
async function bind(d){if(d.transport.protocol===null)d.transport.admit(await d.transport.call('Schema.getDomains',{}));await d.transport.call('Debugger.enable',{});await d.transport.call('Runtime.evaluate',{expression:originalCallbackExpression,returnByValue:false});await d.transport.call('Debugger.setBreakpointOnFunctionCall',{objectId:'original-callback',condition:condition(config)})}

test('foreign/local context refuses before any metadata read or socket and only the sole own page is admitted',async()=>{
  for(const change of [{RUNNER_ENVIRONMENT:'self-hosted'},{GITHUB_ACTIONS:undefined},{GITHUB_SHA:'f'.repeat(40)},{RUNNER_OS:'Windows'}]){
    let reads=0,sockets=0
    await assert.rejects(connectOriginalOutcome(config,context,{...env,...change},handlers().callbacks,{readJson:()=>{reads++},socketFactory:()=>{sockets++}}))
    assert.equal(reads,0);assert.equal(sockets,0)
  }
  for(const bad of ['ws://localhost:12345/devtools/page/original','ws://127.0.0.1:80/devtools/page/original','ws://127.0.0.1:12345/devtools/page/original?secret=1','wss://external.example/devtools/page/original']){
    let reads=0
    await assert.rejects(connectOriginalOutcome(config,{...context,socket_url:bad},env,handlers().callbacks,{readJson:()=>{reads++}}));assert.equal(reads,0)
  }
  for(const pages of [[],[{type:'page',title:'Other',url:'http://tauri.localhost/',webSocketDebuggerUrl:context.socket_url}],
    Array(2).fill({type:'page',title:'OpenNexus',url:'http://tauri.localhost/',webSocketDebuggerUrl:context.socket_url}),
    [{type:'page',title:'OpenNexus',url:'https://external.example/',webSocketDebuggerUrl:context.socket_url}]]){
    let sockets=0
    await assert.rejects(connectOriginalOutcome(config,context,env,handlers().callbacks,{readJson:async()=>pages,socketFactory:()=>{sockets++}}));assert.equal(sockets,0)
  }
})

test('admitted connection reads exact local metadata once and exports no call frame scopes or arguments',async()=>{
  const socket=new Socket(),h=handlers(),reads=[]
  const linked=await connectOriginalOutcome(config,context,env,h.callbacks,{readJson:async url=>{
    reads.push(url);return url.endsWith('/list')?[{type:'page',title:'OpenNexus',url:'http://tauri.localhost/#/owned',webSocketDebuggerUrl:context.socket_url}]:protocol
  },socketFactory:async url=>{assert.equal(url,context.socket_url);return socket}})
  const d={transport:linked.transport};await bind(d)
  socket.message({method:'Debugger.paused',params:{hitBreakpoints:['original-breakpoint'],callFrames:[{functionName:'runCallback',callFrameId:'original-frame',scopeChain:[{PRIVATE_SECRET:'never export'}],this:{PRIVATE_SECRET:'never export'}}],data:{PRIVATE_SECRET:'never export'}}})
  assert.deepEqual(h.paused,[{hitBreakpoints:['original-breakpoint'],callFrames:[{functionName:'runCallback',callFrameId:'original-frame'}]}])
  assert.deepEqual(reads,['http://127.0.0.1:12345/json/list'])
  await linked.transport.call('Debugger.evaluateOnCallFrame',{callFrameId:'original-frame',expression:condition(config),returnByValue:true})
  assert.equal(socket.closes,0);assert.throws(()=>linked.transport.close({...exit,original_pid:322}));assert.equal(socket.closes,0)
  linked.transport.close(exit);assert.equal(socket.closes,1)
})

test('arbitrary evaluation, resume, property reads, another function/frame or IPC never leave the socket',async()=>{
  const d=direct();await bind(d);const sent=d.socket.sent.length
  const forbidden=[['Debugger.resume',{}],['Runtime.getProperties',{objectId:'original-callback'}],
    ['Runtime.evaluate',{expression:'window.__TAURI_INTERNALS__.invoke("mobile_sync_cycle")',returnByValue:true}],
    ['Runtime.evaluate',{expression:originalCallbackExpression,returnByValue:true}],
    ['Debugger.setBreakpointOnFunctionCall',{objectId:'auth-callback',condition:condition(config)}],
    ['Debugger.setBreakpointOnFunctionCall',{objectId:'original-callback',condition:'true'}],
    ['Debugger.evaluateOnCallFrame',{callFrameId:'unobserved-frame',expression:condition(config),returnByValue:true}]]
  for(const [method,params] of forbidden)assert.throws(()=>d.transport.call(method,params))
  assert.equal(d.socket.sent.length,sent);assert.equal(d.socket.closes,0);d.transport.close(exit)
})

test('a lost original command reply is not retried and does not close or resume a possibly paused target',async()=>{
  const d=direct({timeout:5});d.transport.admit(await d.transport.call('Schema.getDomains',{}));d.socket.respond=false
  await assert.rejects(d.transport.call('Debugger.enable',{}),/ORIGINAL_REPLY_UNKNOWN/)
  assert.equal(d.socket.sent.length,2);assert.equal(d.socket.closes,0)
  assert.deepEqual(d.h.faults,[{code:'OWNED_DEBUGGER_ORIGINAL_REPLY_UNKNOWN'}]);d.transport.close(exit)
})

test('malformed original command result rejects promptly without raw errors or a hung cleared timeout',async()=>{
  const d=direct();d.transport.admit(await d.transport.call('Schema.getDomains',{}));await d.transport.call('Debugger.enable',{});await d.transport.call('Runtime.evaluate',{expression:originalCallbackExpression,returnByValue:false});d.socket.invalidBreakpoint=true
  await assert.rejects(d.transport.call('Debugger.setBreakpointOnFunctionCall',{objectId:'original-callback',condition:condition(config)}),/ORIGINAL_RESULT_REFUSED/)
  assert.deepEqual(d.h.faults,[{code:'OWNED_DEBUGGER_ORIGINAL_RESULT_REFUSED'}]);assert.equal(d.socket.closes,0);d.transport.close(exit)
})

test('a foreign pause or malformed message only reports a static refusal and never evaluates its scopes',async()=>{
  const d=direct();await bind(d);const count=d.socket.sent.length
  d.socket.message({method:'Debugger.paused',params:{hitBreakpoints:['unrelated'],callFrames:[{functionName:'auth',callFrameId:'secret'}]}})
  d.socket.emit('message',{data:'PRIVATE_SECRET not valid JSON'})
  assert.equal(d.h.paused.length,0);assert.equal(d.h.faults.length,2)
  assert.equal(d.socket.sent.length,count);assert.equal(JSON.stringify(d.h.faults).includes('PRIVATE_SECRET'),false)
  assert.equal(d.socket.closes,0);d.transport.close(exit)
})

test('unadmitted or copied domain data cannot issue another command or manufacture a witness',async()=>{
  const d=direct()
  assert.throws(()=>d.transport.call('Debugger.enable',{}));assert.throws(()=>d.transport.call('Runtime.evaluate',{expression:originalCallbackExpression,returnByValue:false}))
  assert.equal(d.socket.sent.length,0)
  const actual=await d.transport.call('Schema.getDomains',{})
  assert.throws(()=>d.transport.admit(JSON.parse(JSON.stringify(actual))))
  d.transport.admit(actual);assert.throws(()=>d.transport.admit(actual));assert.throws(()=>d.transport.call('Schema.getDomains',{}))
  assert.equal(d.socket.sent.length,1);d.transport.close(exit)
})
test('a refused original live domain reply closes only the unarmed admission socket and never sends enable or callback reads',async()=>{
  const socket=new Socket(),h=handlers();socket.domainReply={domains:[{name:'Runtime',version:'1.3'}],private:'SECRET'}
  await assert.rejects(connectOriginalOutcome(config,context,env,h.callbacks,{readJson:async()=>[{type:'page',title:'OpenNexus',url:'http://tauri.localhost/',webSocketDebuggerUrl:context.socket_url}],socketFactory:async()=>socket}),error=>!String(error).includes('SECRET'))
  assert.deepEqual(socket.sent.map(x=>x.method),['Schema.getDomains']);assert.equal(socket.closes,1)
})
