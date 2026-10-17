import assert from 'node:assert/strict'
import test from 'node:test'
import vm from 'node:vm'
import { OriginalOutcomeGate,admitProtocol,condition,originalCallbackExpression } from '../android-runtime/original_outcome_gate.mjs'

const config={mode:'complete',nonce:'a326b9cd-165c-4444-b1c2-5b05b48f1994',original_pid:321,
  sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const protocol={domains:[{domain:'Runtime',commands:[{name:'evaluate'}]},
  {domain:'Debugger',commands:['enable','setBreakpointOnFunctionCall','evaluateOnCallFrame','resume'].map(name=>({name,parameters:[{name:'objectId'},{name:'condition'}]}))}]}
function reply(){return {kind:'result',response:{kind:'decision',decision:{sequence:2,state:'completed',choice:'copy',
  destination:config.destination,fingerprint:config.fingerprint,expected_hash:config.expected_hash}}}}
function check(value,c=config){return new Function('data','return (function runCallback(id,data){return '+condition(c)+'})(17,data)')(value)}

test('condition selects the real result.response envelope and exact original scope without matching auth or another completed decision',()=>{
  assert.equal(check(reply()),true)
  for(const value of [{kind:'auth',password:'DO_NOT_EXPORT'},reply().response,null,{},
    {kind:'result',response:{kind:'status',status:{}}}])assert.equal(check(value),false)
  for(const [key,value]of [['fingerprint','c'.repeat(64)],['expected_hash','c'.repeat(64)],['sequence',3],['state','prepared'],['destination','other.md']]){
    const changed=reply();changed.response.decision[key]=value;assert.equal(check(changed),false)
  }
  const rejected={...config,mode:'reject',concurrent_hash:'d'.repeat(64)}
  assert.equal(check('SYNC_REVIEW_CHANGED',rejected),true);assert.equal(check('SYNC_CONFLICT_CHANGED',rejected),true)
  assert.equal(check('SYNC_NETWORK_ERROR',rejected),false);assert.equal(check(reply(),rejected),false)
})

test('unsupported protocol and writable callback refuse before arming or sending any UI input',async()=>{
  for(const p of [{domains:[]},{domains:[protocol.domains[0]]}])assert.throws(()=>admitProtocol(p))
  const gate=new OriginalOutcomeGate(config,protocol);const methods=[]
  const cdp=async(method)=>{methods.push(method);return method==='Runtime.evaluate'?{result:{type:'object'}}:{}}
  await assert.rejects(gate.arm(cdp));assert.deepEqual(methods,['Debugger.enable','Runtime.evaluate'])
  const window={__TAURI_INTERNALS__:{runCallback:function runCallback(){}}}
  assert.throws(()=>vm.runInNewContext(originalCallbackExpression,{window}))
  const frozen={};Object.defineProperty(frozen,'runCallback',{value:function runCallback(){}})
  assert.equal(vm.runInNewContext(originalCallbackExpression,{window:{__TAURI_INTERNALS__:frozen}}),frozen.runCallback)
})

test('matching pause exports no callback arguments or scopes and complete mode cannot resume or close before original process exit',async()=>{
  const gate=new OriginalOutcomeGate(config,protocol);const calls=[]
  const cdp=async(method)=>{calls.push(method);if(method==='Runtime.evaluate')return {result:{type:'function',objectId:'original-function'}}
    if(method==='Debugger.setBreakpointOnFunctionCall')return {breakpointId:'original-breakpoint'}
    if(method==='Debugger.evaluateOnCallFrame')return {result:{type:'boolean',value:true}};return {}}
  await gate.arm(cdp)
  const result=await gate.paused(cdp,{hitBreakpoints:['original-breakpoint'],callFrames:[{functionName:'runCallback',callFrameId:'frame-1',scopeChain:[{DO_NOT_EXPORT_SECRET:'raw'}]}]})
  assert.equal(JSON.stringify(result).includes('DO_NOT_EXPORT'),false);assert.equal(gate.mayClose(),false)
  await assert.rejects(gate.resumeKnownRejection(cdp,{}));assert.equal(calls.includes('Debugger.resume'),false)
  const exit={original_pid:321,original_pid_before_stop:321,force_stop_returncode:0,original_pidof_returncode:1,
    original_pidof_stdout_empty:true,original_pidof_stderr_empty:true,owned_emulator_still_connected:true,same_original_immutable_installed_app:true}
  for(const changed of [{...exit,original_pid:999},{...exit,original_pidof_returncode:undefined},
    {...exit,owned_emulator_still_connected:false},{...exit,original_pid_before_stop:999},
    {...exit,original_pidof_stderr_empty:false},{...exit,original_pidof_stderr_empty:undefined}])assert.throws(()=>gate.originalExited(changed))
  const final=gate.originalExited(exit)
  assert.equal(gate.mayClose(),true);assert.equal(final.original_callback_consumed,false)
})

test('a known stale rejection requires zero native decisions and the actual concurrent source before exactly one original resume',async()=>{
  const gate=new OriginalOutcomeGate({...config,mode:'reject',concurrent_hash:'d'.repeat(64)},protocol)
  gate.state='paused';const cdpCalls=[];const cdp=async(method)=>{cdpCalls.push(method);return {}}
  const proof={frozen_fingerprint:config.fingerprint,native_resolution_rows:0,native_decision_missing:true,
    original_provider_sequence:2,actual_concurrent_source_sha256:'d'.repeat(64)}
  for(const changed of [{...proof,native_resolution_rows:1},{...proof,actual_concurrent_source_sha256:config.expected_hash}])
    await assert.rejects(gate.resumeKnownRejection(cdp,changed))
  assert.deepEqual(cdpCalls,[]);const result=await gate.resumeKnownRejection(cdp,proof)
  assert.equal(result.original_callback_consumed,true);assert.deepEqual(cdpCalls,['Debugger.resume'])
  await assert.rejects(gate.resumeKnownRejection(cdp,proof));assert.deepEqual(cdpCalls,['Debugger.resume'])
})
