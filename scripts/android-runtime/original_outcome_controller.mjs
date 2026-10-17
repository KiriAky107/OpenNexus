// Parent-side admission for the original completion pause and owned stop.
// The caller supplies the actual read-only native/files/provider observations.
// This module creates no receipt, UI input, native operation, process or socket.
import { OriginalOutcomeGate, configuration } from './original_outcome_gate.mjs'

const need=value=>{if(!value)throw Error('OWNED_NATIVE_OUTCOME_PARENT_REFUSED')}
const digest=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value)
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)
const sameKeys=(value,keys)=>value&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k))
const ids=['operation_id','rename_id','copy_id','retire_id','restore_id']

export function baseline(value){
  need(sameKeys(value,['binding_id','remote_sha256','draft_sha256','draft_generation','draft_saved_generation','provider_metadata_sha256']))
  need(uuid(value.binding_id)&&digest(value.remote_sha256)&&digest(value.draft_sha256)&&digest(value.provider_metadata_sha256)
    &&Number.isSafeInteger(value.draft_generation)&&Number.isSafeInteger(value.draft_saved_generation)
    &&value.draft_generation>value.draft_saved_generation&&value.draft_saved_generation>=0)
  return Object.freeze({...value})
}

export function originalExit(value,pid){
  need(value?.original_pid===pid&&value.original_pid_before_stop===pid&&value.force_stop_returncode===0
    &&value.original_pidof_returncode===1&&value.original_pidof_stdout_empty===true&&value.original_pidof_stderr_empty===true
    &&value.owned_emulator_still_connected===true&&value.same_original_immutable_installed_app===true)
}

export class OriginalOutcomeController{
  constructor(config,protocol,original,transport,emit){
    this.config=configuration(config);need(this.config.mode==='complete')
    this.baseline=baseline(original);this.gate=new OriginalOutcomeGate(config,protocol)
    need(transport&&typeof transport.call==='function'&&typeof transport.close==='function'&&typeof emit==='function')
    this.transport=transport;this.emit=emit;this.state='new';this.stopAuthorized=false
    this.pauseCount=0;this.remoteCloseCount=0;this.earlyClose=false;this.explicitClose=false;this.decision=null
  }
  async arm(){
    need(this.state==='new');this.state='arming'
    const ready=await this.gate.arm(this.transport.call)
    need(!this.earlyClose);this.state='armed';this.emit({event:'armed',...ready})
  }
  async paused(event){
    this.pauseCount++;need(this.state==='armed'&&!this.earlyClose&&this.pauseCount===1)
    this.state='observing-original-pause'
    const value=await this.gate.paused(this.transport.call,event)
    need(!this.earlyClose);this.state='paused';this.emit({event:'paused',...value})
  }
  remoteClosed(){
    this.remoteCloseCount++
    if(!this.stopAuthorized&&this.state!=='finished'&&this.state!=='aborted'){
      this.earlyClose=true;this.state='unconfirmed-connection-closed'
      this.emit({event:'unconfirmed-connection-closed',nonce:this.config.nonce,original_pid:this.config.original_pid})
    }
  }
  authorizeOriginalStop(proof){
    need(this.state==='paused'&&!this.earlyClose&&this.pauseCount===1&&this.remoteCloseCount===0&&!this.stopAuthorized)
    need(sameKeys(proof,['original_pid','native','files','provider']))
    need(proof.original_pid===this.config.original_pid)
    const native=proof.native,files=proof.files,provider=proof.provider,d=native?.decision,b=this.baseline,c=this.config
    need(native?.binding_id===b.binding_id&&native.previous_resolution_rows===0&&native.resolution_rows===1
      &&d?.sequence===c.sequence&&d.state==='completed'&&d.choice===c.choice&&d.destination===c.destination
      &&d.expected_hash===c.expected_hash&&d.fingerprint===c.fingerprint
      &&ids.every(key=>uuid(d[key]))&&new Set(ids.map(key=>d[key])).size===5)
    need(files?.source_sha256===b.remote_sha256&&files.copy_sha256===c.expected_hash
      &&files.draft_sha256===b.draft_sha256&&files.draft_generation===b.draft_generation
      &&files.draft_saved_generation===b.draft_saved_generation)
    need(provider?.sequence===2&&provider.original_metadata_sha256===b.provider_metadata_sha256)
    this.decision=Object.freeze(Object.fromEntries(['sequence','fingerprint',...ids].map(k=>[k,d[k]])))
    this.stopAuthorized=true;this.state='owned-stop-authorized'
    // Authorization never stops a process or closes the debugger. The owning
    // caller must now prove the actual original stop before explicit close.
    this.emit({event:'owned-stop-authorized',nonce:c.nonce,original_pid:c.original_pid,fingerprint:c.fingerprint})
  }
  finishAfterOriginalExit(proof){
    need(this.state==='owned-stop-authorized'&&this.stopAuthorized&&!this.earlyClose&&!this.explicitClose)
    originalExit(proof,this.config.original_pid)
    const value=this.gate.originalExited(proof)
    need(this.gate.mayClose());this.state='finished';this.explicitClose=true;this.transport.close(proof)
    this.emit({event:'original-exited',...value,original_native_decision:this.decision,
      explicit_debugger_close_before_original_exit:false,Debugger_resume_sent:false,
      native_receipt_was_read_not_created_by_controller:true})
  }
  abortAfterOriginalExit(proof){
    need(this.state!=='finished'&&this.state!=='aborted'&&!this.explicitClose)
    originalExit(proof,this.config.original_pid)
    this.state='aborted';this.explicitClose=true;this.transport.close(proof)
    this.emit({event:'aborted-after-original-exit',nonce:this.config.nonce,original_pid:this.config.original_pid,
      native_completion_interruption_verified:false,raw_error_or_scope_exported:false})
  }
}
