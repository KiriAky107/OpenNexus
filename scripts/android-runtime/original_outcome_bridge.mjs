// Hosted-only stdio bridge. Java sends the actual confirmation; this bridge
// observes the original callback and never performs a UI/native operation.
import { createInterface } from 'node:readline'
import { pathToFileURL } from 'node:url'
import { baseline, OriginalOutcomeController } from './original_outcome_controller.mjs'
import { configuration } from './original_outcome_gate.mjs'
import { hostedTarget, connectOriginalOutcome } from './original_outcome_transport.mjs'

const need=value=>{if(!value)throw Error('OWNED_ORIGINAL_BRIDGE_REFUSED')}
function parse(line){need(typeof line==='string'&&Buffer.byteLength(line,'utf8')<=65536);const value=JSON.parse(line);need(value&&typeof value==='object'&&!Array.isArray(value));return value}
const keys=(value,names)=>Object.keys(value).length===names.length&&names.every(k=>Object.hasOwn(value,k))

export async function runOriginalOutcomeBridge(lines,emit,env,dependencies={}){
  need(typeof lines?.[Symbol.asyncIterator]==='function'&&typeof emit==='function')
  const iterator=lines[Symbol.asyncIterator](),first=await iterator.next();need(!first.done)
  const initial=parse(first.value);need(keys(initial,['configuration','context','baseline']))
  configuration(initial.configuration);baseline(initial.baseline);hostedTarget(initial.configuration,initial.context,env)
  let controller=null,earlyClosed=false,faulted=false
  const fault=()=>{faulted=true;emit({event:'fault',code:'OWNED_ORIGINAL_BRIDGE_UNCONFIRMED'})}
  const handlers={paused:event=>controller?controller.paused(event):fault(),
    closed:()=>{if(controller)controller.remoteClosed();else earlyClosed=true},fault}
  const linked=await connectOriginalOutcome(initial.configuration,initial.context,env,handlers,dependencies)
  controller=new OriginalOutcomeController(initial.configuration,linked.protocol,initial.baseline,linked.transport,emit)
  if(earlyClosed)controller.remoteClosed()
  try{await controller.arm()}catch{fault()}
  let count=0
  while(true){
    const next=await iterator.next()
    // Never detach/close a potentially paused target on parent EOF. The owning
    // hosted workflow must stop its original app/emulator and preserve failure.
    if(next.done)throw Error('OWNED_ORIGINAL_EXIT_PROOF_MISSING')
    need(++count<=4);const command=parse(next.value);need(keys(command,['command','proof']))
    try{
      if(command.command==='native-before-stop'){
        need(!faulted);controller.authorizeOriginalStop(command.proof)
      }else if(command.command==='original-exited'){
        need(!faulted);controller.finishAfterOriginalExit(command.proof);return {state:'finished'}
      }else if(command.command==='cleanup-after-original-exit'){
        controller.abortAfterOriginalExit(command.proof);return {state:'aborted'}
      }else throw Error('OWNED_ORIGINAL_COMMAND_REFUSED')
    }catch{faulted=true;emit({event:'parent-command-refused',code:'OWNED_ORIGINAL_COMMAND_REFUSED'})}
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const lines=createInterface({input:process.stdin,crlfDelay:Infinity})
  runOriginalOutcomeBridge(lines,value=>process.stdout.write(JSON.stringify(value)+'\n'),process.env)
    .catch(()=>{process.stdout.write(JSON.stringify({event:'fault',code:'OWNED_ORIGINAL_BRIDGE_UNCONFIRMED'})+'\n');process.exitCode=1})
}
