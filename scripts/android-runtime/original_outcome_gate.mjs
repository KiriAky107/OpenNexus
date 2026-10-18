// Conditional observation of the original immutable Tauri callback. No Tauri
// invocation, binding rewrite, UI input, scope dump or native operation here.
import { requireLiveProtocol } from './original_live_protocol.mjs'
const digest=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value)
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)
const need=ok=>{if(!ok)throw Error('OWNED_NATIVE_OUTCOME_GATE_REFUSED')}

export function configuration(value){
  need(value&&['complete','reject'].includes(value.mode))
  const keys=['mode','nonce','original_pid','sequence','choice','destination','fingerprint','expected_hash',...(value.mode==='reject'?['concurrent_hash']:[])]
  need(Object.keys(value).length===keys.length&&Object.keys(value).every(k=>keys.includes(k)))
  need(uuid(value.nonce)&&Number.isSafeInteger(value.original_pid)&&value.original_pid>0&&value.sequence===2&&value.choice==='copy'
    &&value.destination==='runtime-conflict-copy.md'&&digest(value.fingerprint)&&digest(value.expected_hash))
  if(value.mode==='reject')need(digest(value.concurrent_hash)&&value.concurrent_hash!==value.expected_hash)
  return Object.freeze({...value})
}

export function admitProtocol(protocol){
  // Legacy offline descriptor validation. It is never a live admission witness.
  need(protocol&&Array.isArray(protocol.domains))
  for(const [domain,commands]of [['Debugger',['enable','setBreakpointOnFunctionCall','evaluateOnCallFrame','resume']],['Runtime',['evaluate']]]){
    const matches=protocol.domains.filter(d=>d.domain===domain);need(matches.length===1&&Array.isArray(matches[0].commands))
    need(commands.every(name=>matches[0].commands.some(command=>command.name===name)))
  }
  const command=protocol.domains.find(d=>d.domain==='Debugger').commands.find(c=>c.name==='setBreakpointOnFunctionCall')
  need(Array.isArray(command.parameters)&&['objectId','condition'].every(name=>command.parameters.some(p=>p.name===name)))
}

export function condition(value){
  const c=configuration(value)
  if(c.mode==='reject')return `arguments.length===2&&(arguments[1]==='SYNC_REVIEW_CHANGED'||arguments[1]==='SYNC_CONFLICT_CHANGED')`
  return `(()=>{if(arguments.length!==2)return false;const value=arguments[1];
    if(!value||typeof value!=='object'||value.kind!=='result'||value.response?.kind!=='decision')return false;
    const d=value.response.decision;return !!d&&d.sequence===2&&d.state==='completed'
      &&d.choice==='copy'&&d.destination===${JSON.stringify(c.destination)}
      &&d.fingerprint===${JSON.stringify(c.fingerprint)}&&d.expected_hash===${JSON.stringify(c.expected_hash)};})()`
}

export const originalCallbackExpression=`(()=>{
  const value=window.__TAURI_INTERNALS__,d=value&&Object.getOwnPropertyDescriptor(value,'runCallback');
  if(!d||d.writable!==false||d.configurable!==false||typeof d.value!=='function'||d.value.name!=='runCallback')
    throw Error('OWNED_NATIVE_CALLBACK_DESCRIPTOR_REFUSED');
  return d.value;
})()`

export class OriginalOutcomeGate{
  constructor(value,protocol,owner){
    this.config=configuration(value);requireLiveProtocol(protocol,this.config,owner)
    need(owner&&typeof owner.call==='function');this.owner=owner;this.protocol=protocol;this.state='new';this.breakpoint=null
  }
  async arm(cdp){
    need(this.state==='new'&&cdp===this.owner.call);requireLiveProtocol(this.protocol,this.config,this.owner);this.state='arming'
    const enabled=await cdp('Debugger.enable',{})
    need(enabled&&typeof enabled==='object'&&!Array.isArray(enabled)&&Object.keys(enabled).every(k=>k==='debuggerId')
      &&(!Object.hasOwn(enabled,'debuggerId')||typeof enabled.debuggerId==='string'&&enabled.debuggerId.length>0&&enabled.debuggerId.length<=256))
    const original=await cdp('Runtime.evaluate',{expression:originalCallbackExpression,returnByValue:false})
    need(!original.exceptionDetails&&original.result?.type==='function'&&typeof original.result.objectId==='string')
    const bound=await cdp('Debugger.setBreakpointOnFunctionCall',{objectId:original.result.objectId,condition:condition(this.config)})
    need(typeof bound.breakpointId==='string'&&bound.breakpointId.length>0&&bound.breakpointId.length<=256)
    this.breakpoint=bound.breakpointId;this.state='armed'
    return {armed:true,nonce:this.config.nonce,original_pid:this.config.original_pid,callback_binding_rewritten:false,UI_input_sent:false}
  }
  async paused(cdp,event){
    need(cdp===this.owner.call&&this.state==='armed'&&event&&Array.isArray(event.hitBreakpoints)&&event.hitBreakpoints.length===1
      &&event.hitBreakpoints[0]===this.breakpoint&&Array.isArray(event.callFrames)&&event.callFrames.length>0)
    const frame=event.callFrames[0]
    need(frame.functionName==='runCallback'&&typeof frame.callFrameId==='string'&&frame.callFrameId.length<=256)
    this.state='observing-original-pause'
    const match=await cdp('Debugger.evaluateOnCallFrame',{callFrameId:frame.callFrameId,expression:condition(this.config),returnByValue:true})
    need(!match.exceptionDetails&&match.result?.type==='boolean'&&match.result.value===true)
    this.state='paused';return this.publicState()
  }
  publicState(){
    need(['armed','paused','original-exited','rejection-consumed'].includes(this.state))
    return {state:this.state,mode:this.config.mode,nonce:this.config.nonce,original_pid:this.config.original_pid,
      sequence:this.config.sequence,frozen_fingerprint:this.config.fingerprint,
      original_callback_consumed:this.state==='rejection-consumed',args_or_scopes_exported:false,
      callback_bindings_rewritten:false,native_or_server_operation_ID_created_by_driver:false}
  }
  originalExited(proof){
    need(this.state==='paused'&&this.config.mode==='complete'&&proof?.original_pid===this.config.original_pid
      &&proof.original_pid_before_stop===this.config.original_pid&&proof.force_stop_returncode===0
      &&proof.original_pidof_returncode===1&&proof.original_pidof_stdout_empty===true&&proof.original_pidof_stderr_empty===true
      &&proof.owned_emulator_still_connected===true&&proof.same_original_immutable_installed_app===true)
    this.state='original-exited';return this.publicState()
  }
  async resumeKnownRejection(cdp,proof){
    need(cdp===this.owner.call&&this.state==='paused'&&this.config.mode==='reject'&&proof?.frozen_fingerprint===this.config.fingerprint
      &&proof.native_resolution_rows===0&&proof.native_decision_missing===true&&proof.original_provider_sequence===2
      &&proof.actual_concurrent_source_sha256===this.config.concurrent_hash)
    // Continue this known rejected callback once. It is never used for the
    // completed-decision interruption, and never sends another Resolve.
    this.state='resuming-known-rejection';await cdp('Debugger.resume',{});this.state='rejection-consumed'
    return this.publicState()
  }
  mayClose(){return this.state==='original-exited'||this.state==='rejection-consumed'}
}
