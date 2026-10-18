import test from 'node:test'
import assert from 'node:assert/strict'
import { runOriginalOutcomeBridge } from '../android-runtime/original_outcome_bridge.mjs'

const configuration={mode:'complete',nonce:'00000000-0000-4000-8000-000000000001',original_pid:321,sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const context={run_id:1234,tools_commit:'a'.repeat(40),socket_url:'ws://127.0.0.1:12345/devtools/page/original'}
const baseline={binding_id:'00000000-0000-4000-8000-000000000002',remote_sha256:'c'.repeat(64),draft_sha256:'d'.repeat(64),draft_generation:4,draft_saved_generation:3,provider_metadata_sha256:'e'.repeat(64)}
const env={GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'github-hosted',RUNNER_OS:'Linux',GITHUB_REPOSITORY:'KiriAky107/OpenNexus',GITHUB_RUN_ID:'1234',GITHUB_SHA:'a'.repeat(40)}
const protocol={domains:[{domain:'Debugger',commands:['enable','evaluateOnCallFrame','resume'].map(name=>({name})).concat([{name:'setBreakpointOnFunctionCall',parameters:[{name:'objectId'},{name:'condition'}]}])},{domain:'Runtime',commands:[{name:'evaluate'}]}]}
const exit={original_pid:321,original_pid_before_stop:321,force_stop_returncode:0,original_pidof_returncode:1,original_pidof_stdout_empty:true,original_pidof_stderr_empty:true,owned_emulator_still_connected:true,same_original_immutable_installed_app:true}
class Socket{
  constructor(){this.listeners={};this.closed=0;this.sent=[]}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)}
  emit(name,event){for(const fn of this.listeners[name]??[])fn(event)}
  send(raw){const m=JSON.parse(raw);this.sent.push(m);let result={}
    if(m.method==='Schema.getDomains')result={domains:[{name:'Debugger',version:'1.3'},{name:'Runtime',version:'1.3'}]}
    if(m.method==='Runtime.evaluate')result={result:{type:'function',objectId:'original'}}
    if(m.method==='Debugger.setBreakpointOnFunctionCall')result={breakpointId:'original'}
    if(m.method==='Debugger.evaluateOnCallFrame')result={result:{type:'boolean',value:true}}
    this.emit('message',{data:JSON.stringify({id:m.id,result})})
  }
  close(){this.closed++;this.emit('close',{})}
  pause(){this.emit('message',{data:JSON.stringify({method:'Debugger.paused',params:{hitBreakpoints:['original'],callFrames:[{functionName:'runCallback',callFrameId:'original'}]}})})}
}
function harness(){const socket=new Socket(),events=[];return {socket,events,dependencies:{readJson:async url=>url.endsWith('/list')?[{type:'page',title:'OpenNexus',url:'http://tauri.localhost/',webSocketDebuggerUrl:context.socket_url}]:protocol,socketFactory:async()=>socket}}}
const start=()=>JSON.stringify({configuration,context,baseline})
async function* sequence(values){for(const value of values)yield typeof value==='string'?value:JSON.stringify(value)}

test('the complete stdio sequence binds original pause, read receipt and exit before closing once',async()=>{
  const h=harness(),waiting=new Map(),seen=new Set()
  const event=name=>seen.has(name)?Promise.resolve():new Promise(resolve=>waiting.set(name,resolve))
  const emit=value=>{h.events.push(value);seen.add(value.event);waiting.get(value.event)?.()}
  const proof={original_pid:321,native:{binding_id:baseline.binding_id,previous_resolution_rows:0,resolution_rows:1,
    decision:{sequence:2,choice:'copy',destination:configuration.destination,expected_hash:configuration.expected_hash,
      fingerprint:configuration.fingerprint,state:'completed',...Object.fromEntries(['operation_id','rename_id','copy_id','retire_id','restore_id'].map((k,i)=>[k,`00000000-0000-4000-8000-${String(10+i).padStart(12,'0')}`]))}},
    files:{source_sha256:baseline.remote_sha256,copy_sha256:configuration.expected_hash,draft_sha256:baseline.draft_sha256,
      draft_generation:4,draft_saved_generation:3},provider:{sequence:2,original_metadata_sha256:baseline.provider_metadata_sha256}}
  async function* original(){
    yield start();await event('armed');h.socket.pause();await event('paused')
    yield JSON.stringify({command:'native-before-stop',proof});await event('owned-stop-authorized')
    assert.equal(h.socket.closed,0);h.socket.emit('close',{})
    yield JSON.stringify({command:'original-exited',proof:exit})
  }
  const result=await runOriginalOutcomeBridge(original(),emit,env,h.dependencies)
  assert.equal(result.state,'finished');assert.equal(h.socket.closed,1)
  assert.deepEqual(h.events.map(e=>e.event),['armed','paused','owned-stop-authorized','original-exited'])
  assert.equal(h.socket.sent.some(m=>m.method==='Debugger.resume'),false)
  assert.equal(h.events.at(-1).original_native_decision.copy_id,'00000000-0000-4000-8000-000000000012')
})

test('hosted bridge arms the original observer and explicit cleanup closes only after the original exit proof',async()=>{
  const h=harness()
  const result=await runOriginalOutcomeBridge(sequence([start(),{command:'cleanup-after-original-exit',proof:exit}]),v=>h.events.push(v),env,h.dependencies)
  assert.equal(result.state,'aborted');assert.equal(h.events[0].event,'armed');assert.equal(h.socket.closed,1)
  assert.equal(h.events.at(-1).native_completion_interruption_verified,false)
})

test('parent EOF after arm keeps the original connection and has no automatic stop, close, resume or retry',async()=>{
  const h=harness()
  await assert.rejects(runOriginalOutcomeBridge(sequence([start()]),v=>h.events.push(v),env,h.dependencies),/EXIT_PROOF_MISSING/)
  assert.equal(h.socket.closed,0);assert.deepEqual(h.socket.sent.map(x=>x.method),['Schema.getDomains','Debugger.enable','Runtime.evaluate','Debugger.setBreakpointOnFunctionCall']);assert.equal(h.events.length,1)
})

test('invalid native observation or exit never releases the observer and the parent may only perform proven cleanup',async()=>{
  const h=harness()
  const result=await runOriginalOutcomeBridge(sequence([start(),{command:'native-before-stop',proof:{}},
    {command:'original-exited',proof:exit},{command:'cleanup-after-original-exit',proof:exit}]),v=>h.events.push(v),env,h.dependencies)
  assert.equal(result.state,'aborted');assert.equal(h.events.filter(e=>e.event==='parent-command-refused').length,2)
  assert.equal(h.events.some(e=>e.event==='original-exited'),false);assert.equal(h.socket.closed,1)
})

test('unowned startup and extra plaintext fields reject before metadata IO without reflecting input',async()=>{
  let reads=0;const dependencies={readJson:async()=>{reads++}}
  for(const first of [JSON.stringify({configuration,context,baseline,password:'PRIVATE_SECRET'}),start()]){
    const events=[];await assert.rejects(runOriginalOutcomeBridge(sequence([first]),v=>events.push(v),{},dependencies))
    assert.equal(reads,0);assert.equal(JSON.stringify(events).includes('PRIVATE_SECRET'),false)
  }
})
