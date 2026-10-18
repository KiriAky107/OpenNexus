import test from 'node:test'
import assert from 'node:assert/strict'
import { admitLiveProtocol,requireLiveProtocol } from '../android-runtime/original_live_protocol.mjs'
import { OriginalOutcomeGate } from '../android-runtime/original_outcome_gate.mjs'
const config={mode:'complete',nonce:'00000000-0000-4000-8000-000000000001',original_pid:321,
  sequence:2,choice:'copy',destination:'runtime-conflict-copy.md',fingerprint:'a'.repeat(64),expected_hash:'b'.repeat(64)}
const reply=()=>({domains:[{name:'Debugger',version:'1.3'},{name:'Runtime',version:'1.3'}]})
test('live reply binds one opaque witness to its original owner and exact scope, never a parent lookalike',()=>{
  const owner={call:async()=>({})},value=admitLiveProtocol(reply(),config,owner)
  requireLiveProtocol(value,{...config},owner);assert.equal(Object.isFrozen(value),true)
  for(const witness of [null,{},JSON.parse(JSON.stringify(value)),reply()])assert.throws(()=>requireLiveProtocol(witness,config,owner))
  assert.throws(()=>requireLiveProtocol(value,config,{call:owner.call}))
  for(const changed of [{original_pid:322},{nonce:'00000000-0000-4000-8000-000000000002'},{fingerprint:'c'.repeat(64)},{expected_hash:'c'.repeat(64)}])
    assert.throws(()=>requireLiveProtocol(value,{...config,...changed},owner))
  assert.throws(()=>admitLiveProtocol(reply(),config,owner))
})
test('missing duplicate oversized malformed or legacy descriptors refuse before any method',()=>{
  const cases=[null,[],{}, {domains:[]}, {domains:[{name:'Debugger',version:'1.3'}]},
    {domains:[...reply().domains,{name:'Debugger',version:'1.3'}]},
    {domains:[{domain:'Debugger',commands:[{name:'enable'}]},{domain:'Runtime',commands:[]}]},
    {domains:reply().domains,private:'SECRET'},
    {domains:[{name:'Debugger',version:'1.3',private:'SECRET'},reply().domains[1]]},
    {domains:Array.from({length:129},(_,i)=>({name:'Domain'+i,version:'1.3'}))}]
  for(const value of cases)assert.throws(()=>admitLiveProtocol(value,config,{}),error=>!String(error).includes('SECRET'))
  for(const version of ['',1,'1.3\u0000','SECRET','1000.1']){
    const value=reply();value.domains[0].version=version;assert.throws(()=>admitLiveProtocol(value,config,{}))
  }
})
test('invalid owner original PID or extra configuration is not a capability witness',()=>{
  for(const owner of [null,undefined,1,'socket'])assert.throws(()=>admitLiveProtocol(reply(),config,owner))
  for(const changed of [{original_pid:true},{original_pid:0},{nonce:'SECRET'},{private:'SECRET'},{sequence:3},{choice:'replace'}])
    assert.throws(()=>admitLiveProtocol(reply(),{...config,...changed},{}),error=>!String(error).includes('SECRET'))
})
test('gate rejects legacy descriptors, another connection and another command function before arming',async()=>{
  let calls=0;const owner={call:async()=>{calls++;return {}}},witness=admitLiveProtocol(reply(),config,owner)
  assert.throws(()=>new OriginalOutcomeGate(config,reply(),owner))
  assert.throws(()=>new OriginalOutcomeGate(config,witness,{call:owner.call}))
  const gate=new OriginalOutcomeGate(config,witness,owner)
  await assert.rejects(gate.arm(async()=>({})));assert.equal(calls,0)
})
test('an original refused Debugger enable cannot reach callback lookup or a breakpoint',async()=>{
  const methods=[],owner={call:async(method)=>{methods.push(method);return {private:'SECRET'}}}
  const gate=new OriginalOutcomeGate(config,admitLiveProtocol(reply(),config,owner),owner)
  await assert.rejects(gate.arm(owner.call),error=>!String(error).includes('SECRET'))
  assert.deepEqual(methods,['Debugger.enable'])
})
