import test from 'node:test'
import { admitLiveProtocol } from '../android-runtime/original_live_protocol.mjs'
import assert from 'node:assert/strict'
import { OriginalOutcomeController } from '../android-runtime/original_outcome_controller.mjs'

const uuid=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`
const config={mode:'complete',nonce:uuid(1),original_pid:321,sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const original={binding_id:uuid(2),remote_sha256:'c'.repeat(64),draft_sha256:'d'.repeat(64),draft_generation:4,draft_saved_generation:3,provider_metadata_sha256:'e'.repeat(64)}
const protocol={domains:[{domain:'Debugger',commands:['enable','evaluateOnCallFrame','resume'].map(name=>({name})).concat([{name:'setBreakpointOnFunctionCall',parameters:[{name:'objectId'},{name:'condition'}]}])},{domain:'Runtime',commands:[{name:'evaluate'}]}]}
const pause={hitBreakpoints:['original'],callFrames:[{functionName:'runCallback',callFrameId:'original-frame'}]}
const exit={original_pid:321,original_pid_before_stop:321,force_stop_returncode:0,original_pidof_returncode:1,original_pidof_stdout_empty:true,original_pidof_stderr_empty:true,owned_emulator_still_connected:true,same_original_immutable_installed_app:true}
function receipt(){return {original_pid:321,native:{binding_id:original.binding_id,previous_resolution_rows:0,resolution_rows:1,decision:{sequence:2,choice:'copy',destination:config.destination,expected_hash:config.expected_hash,fingerprint:config.fingerprint,state:'completed',...Object.fromEntries(['operation_id','rename_id','copy_id','retire_id','restore_id'].map((k,i)=>[k,uuid(10+i)]))}},files:{source_sha256:original.remote_sha256,copy_sha256:config.expected_hash,draft_sha256:original.draft_sha256,draft_generation:4,draft_saved_generation:3},provider:{sequence:2,original_metadata_sha256:original.provider_metadata_sha256}}}
function driver(){
  const events=[],calls=[];let closed=0
  const transport={close:()=>{closed++},call:async(method,params)=>{
    calls.push([method,params]);if(method==='Runtime.evaluate')return {result:{type:'function',objectId:'original-function'}}
    if(method==='Debugger.setBreakpointOnFunctionCall')return {breakpointId:'original'}
    if(method==='Debugger.evaluateOnCallFrame')return {result:{type:'boolean',value:true}}
    return {}
  }}
  const value=new OriginalOutcomeController(config,admitLiveProtocol({domains:[{name:'Debugger',version:'1.3'},{name:'Runtime',version:'1.3'}]},config,transport),original,transport,event=>events.push(event))
  return {value,events,calls,closed:()=>closed}
}

test('holds the actual completion pause through native observation and owned stop, never resumes or generates IDs',async()=>{
  const d=driver();await d.value.arm();await d.value.paused(pause)
  assert.equal(d.closed(),0);d.value.authorizeOriginalStop(receipt());assert.equal(d.closed(),0)
  d.value.remoteClosed();d.value.finishAfterOriginalExit(exit)
  assert.equal(d.closed(),1);assert.equal(d.events.at(-1).event,'original-exited')
  assert.equal(d.events.at(-1).original_native_decision.copy_id,uuid(12))
  assert.equal(d.events.at(-1).original_callback_consumed,false)
  assert.equal(d.calls.some(([method])=>method==='Debugger.resume'),false)
  assert.throws(()=>d.value.finishAfterOriginalExit(exit));assert.throws(()=>d.value.authorizeOriginalStop(receipt()))
})

test('wrong completed receipt, draft, source, provider or identity cannot authorize a stop',async()=>{
  const cases=[r=>r.original_pid++,r=>r.native.resolution_rows=2,r=>r.native.previous_resolution_rows=1,
    r=>r.native.binding_id=uuid(99),r=>r.native.decision.fingerprint='f'.repeat(64),
    r=>r.native.decision.copy_id=r.native.decision.operation_id,r=>r.native.decision.state='pending',
    r=>r.native.decision.expected_hash='f'.repeat(64),r=>r.files.copy_sha256='f'.repeat(64),
    r=>r.files.source_sha256='f'.repeat(64),r=>r.files.draft_generation=5,r=>r.files.draft_sha256='f'.repeat(64),
    r=>r.files.draft_saved_generation=4,r=>r.provider.sequence=3,r=>r.provider.original_metadata_sha256='f'.repeat(64)]
  for(const change of cases){const d=driver();await d.value.arm();await d.value.paused(pause);const r=receipt();change(r)
    assert.throws(()=>d.value.authorizeOriginalStop(r));assert.equal(d.closed(),0);assert.equal(d.value.stopAuthorized,false)
  }
})

test('early debugger loss remains unconfirmed and cleanup needs the original exit rather than a replacement PID',async()=>{
  const d=driver();await d.value.arm();await d.value.paused(pause);d.value.remoteClosed()
  assert.throws(()=>d.value.authorizeOriginalStop(receipt()));assert.throws(()=>d.value.finishAfterOriginalExit(exit))
  assert.throws(()=>d.value.abortAfterOriginalExit({...exit,original_pid_before_stop:322}));assert.equal(d.closed(),0)
  d.value.abortAfterOriginalExit(exit);assert.equal(d.closed(),1)
  assert.equal(d.events.at(-1).native_completion_interruption_verified,false)
})

test('an unrelated or repeated pause cannot be reported as a valid original completion',async()=>{
  const d=driver();await d.value.arm()
  await assert.rejects(d.value.paused({...pause,hitBreakpoints:['foreign']}))
  assert.throws(()=>d.value.authorizeOriginalStop(receipt()));assert.equal(d.closed(),0)
  d.value.abortAfterOriginalExit(exit);assert.equal(d.events.at(-1).event,'aborted-after-original-exit')
  const repeated=driver();await repeated.value.arm();await repeated.value.paused(pause)
  await assert.rejects(repeated.value.paused(pause));assert.throws(()=>repeated.value.authorizeOriginalStop(receipt()))
})

test('missing, failed or disconnected original-exit observations never close a completed pause',async()=>{
  for(const change of [r=>delete r.original_pidof_returncode,r=>r.force_stop_returncode=1,
    r=>r.original_pidof_stdout_empty=false,r=>r.original_pidof_stderr_empty=false,r=>delete r.original_pidof_stderr_empty,
    r=>r.owned_emulator_still_connected=false,r=>r.same_original_immutable_installed_app=false]){
    const d=driver();await d.value.arm();await d.value.paused(pause);d.value.authorizeOriginalStop(receipt())
    const value={...exit};change(value);assert.throws(()=>d.value.finishAfterOriginalExit(value));assert.equal(d.closed(),0)
  }
})
