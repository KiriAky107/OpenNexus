// Observe already-parsed original V8 modules. No evaluation, pause, breakpoint,
// callback replacement, page fetch or application/native command is sent.
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { boundedJson, hostedTarget } from './original_outcome_transport.mjs'
import { compiledMarkers, decodedResource, validateResources } from './original_compiled_resources.mjs'

const need=ok=>{if(!ok)throw Error('OWNED_COMPILED_SCRIPT_REFUSED')}
const stages=new Set(['admission','page-list','protocol','socket-open','script-list','script-content','resource-summary'])
const reasons=new Set(['unconfirmed','timeout','send-unconfirmed','connection-unconfirmed','reply-refused','bound','no-owned-script','script-ambiguous','unexpected-pause'])
class ScriptReadFailure extends Error {
  constructor(stage,reason,summary){super('OWNED_COMPILED_RESOURCE_UNCONFIRMED');this.stage=stage;this.reason=reason;this.summary=summary}
}
export function scriptDiagnostic(error){
  return {error:'OWNED_COMPILED_RESOURCE_UNCONFIRMED',stage:error instanceof ScriptReadFailure&&stages.has(error.stage)?error.stage:'admission',
    reason:error instanceof ScriptReadFailure&&reasons.has(error.reason)?error.reason:'unconfirmed',
    ...(error instanceof ScriptReadFailure&&error.summary?{script_summary:error.summary}:{})}
}
export function admitScriptProtocol(value){
  need(Array.isArray(value?.domains))
  const domains=value.domains.filter(d=>d.domain==='Debugger');need(domains.length===1)
  const commands=domains[0].commands;need(Array.isArray(commands))
  for(const name of ['enable','getScriptSource'])need(commands.filter(c=>c.name===name).length===1)
  const source=commands.find(c=>c.name==='getScriptSource')
  need(Array.isArray(source.parameters)&&source.parameters.some(p=>p.name==='scriptId'&&!p.optional))
  need(Array.isArray(domains[0].events)&&domains[0].events.some(e=>e.name==='scriptParsed'))
}
export function ownedParsedScript(value){
  if(typeof value?.url!=='string'||value.url.length>512)return null
  let url;try{url=new URL(value.url)}catch{return null}
  if(url.origin!=='http://tauri.localhost'||url.username||url.password||url.search||url.hash
    ||!/^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(url.pathname))return null
  need(typeof value.scriptId==='string'&&/^[A-Za-z0-9_.:-]{1,128}$/.test(value.scriptId)
    &&value.startLine===0&&value.startColumn===0&&Number.isSafeInteger(value.executionContextId)&&value.executionContextId>0
    &&value.hasSourceURL!==true&&value.isLiveEdit!==true)
  return {id:value.scriptId,url:url.href,path:url.pathname}
}
export function decodedScript(value){
  need(value&&Object.keys(value).length===1&&typeof value.scriptSource==='string')
  const result=decodedResource({content:value.scriptSource,base64Encoded:false})
  need(result.text===value.scriptSource)
  return result
}

export async function observeCompiledScripts(config,context,env,dependencies={}){
  let stage='admission',socket,timer,openReject,enabled=false,paused=false,parsed=0,lastParsed=0
  const candidates=new Map(),ids=new Map(),pending=new Map();let failed=false,failure,next=0,received=0,messages=0
  const summary=()=>({parsed_notifications:Math.min(parsed,513),owned_candidates:Math.min(candidates.size,25),
    protocol_admitted:protocolAdmitted,Debugger_enable_reply_seen:enabled,unexpected_pause_observed:paused})
  let protocolAdmitted=false
  const fault=reason=>{
    if(failed)return
    failed=true;const error=new ScriptReadFailure(stage,reasons.has(reason)?reason:'unconfirmed',summary())
    failure=error
    openReject?.(error);for(const p of pending.values())p.reject(error);pending.clear()
  }
  try{
    const target=hostedTarget(config,context,env),read=dependencies.readJson??boundedJson
    stage='page-list'
    const pages=await read(target.http+'/json/list');need(Array.isArray(pages))
    const own=pages.filter(p=>p.type==='page');need(own.length===1&&own[0].title==='OpenNexus'
      &&own[0].webSocketDebuggerUrl===target.url&&new URL(own[0].url).origin==='http://tauri.localhost')
    stage='protocol';admitScriptProtocol(await read(target.http+'/json/protocol'));protocolAdmitted=true
    stage='socket-open'
    socket=dependencies.socketFactory?await dependencies.socketFactory(target.url):new WebSocket(target.url)
    const opened=dependencies.socketFactory?Promise.resolve():new Promise((resolve,reject)=>{
      openReject=reject;socket.addEventListener('open',resolve,{once:true})
    })
    socket.addEventListener('message',event=>{
      try{
        need(typeof event.data==='string'&&Buffer.byteLength(event.data)<=20*1024**2)
        received+=Buffer.byteLength(event.data);messages++;need(received<=40*1024**2&&messages<=1024)
        const value=JSON.parse(event.data)
        if(value.method==='Debugger.paused'){paused=true;fault('unexpected-pause');return}
        if(value.method==='Debugger.scriptParsed'){
          parsed++;lastParsed=Date.now();need(parsed<=512)
          const script=ownedParsedScript(value.params)
          if(script){
            const old=candidates.get(script.url),oldURL=ids.get(script.id)
            if(old&&old.id!==script.id||oldURL&&oldURL!==script.url){fault('script-ambiguous');return}
            candidates.set(script.url,script);ids.set(script.id,script.url)
            if(candidates.size>24){fault('bound');return}
          }
          return
        }
        const reply=pending.get(value?.id);if(!reply)return
        pending.delete(value.id)
        if(value.error)reply.reject(new ScriptReadFailure(stage,'reply-refused',summary()))
        else reply.resolve(value.result)
      }catch{fault('reply-refused')}
    })
    socket.addEventListener('error',()=>fault('connection-unconfirmed'));socket.addEventListener('close',()=>fault('connection-unconfirmed'))
    timer=setTimeout(()=>fault('timeout'),15000)
    await opened
    const call=(method,params)=>{
      need(!failed&&next<25&&pending.size===0)
      need(method==='Debugger.enable'&&next===0&&Object.keys(params).length===0
        ||method==='Debugger.getScriptSource'&&enabled&&Object.keys(params).length===1&&ids.has(params.scriptId))
      const id=++next
      return new Promise((resolve,reject)=>{
        pending.set(id,{resolve,reject})
        try{socket.send(JSON.stringify({id,method,params}))}catch{fault('send-unconfirmed')}
      })
    }
    stage='script-list'
    const enabledReply=await call('Debugger.enable',{})
    need(enabledReply&&typeof enabledReply==='object'&&!Array.isArray(enabledReply)
      &&Object.keys(enabledReply).every(k=>k==='debuggerId')
      &&(!Object.hasOwn(enabledReply,'debuggerId')||typeof enabledReply.debuggerId==='string'&&enabledReply.debuggerId.length>0&&enabledReply.debuggerId.length<=256))
    enabled=true
    // CDP sends all known/uncollected scriptParsed events on enable. Allow a
    // bounded quiet observation, never another enable or a script evaluation.
    if(dependencies.settleScripts)await dependencies.settleScripts()
    else{
      const until=Date.now()+1200
      do{
        await new Promise(resolve=>setTimeout(resolve,100));need(!failed)
        if(candidates.size&&Date.now()-lastParsed>=150)break
      }while(Date.now()<until)
    }
    need(!failed)
    if(!candidates.size)throw new ScriptReadFailure(stage,'no-owned-script',summary())
    const assets=[];let total=0
    for(const script of [...candidates.values()].sort((a,b)=>a.path.localeCompare(b.path))){
      stage='script-content'
      const {bytes,text}=decodedScript(await call('Debugger.getScriptSource',{scriptId:script.id}))
      total+=bytes.length;if(total>16*1024**2)throw new ScriptReadFailure(stage,'bound',summary())
      assets.push({path:script.path,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),...compiledMarkers(text)})
    }
    stage='resource-summary'
    const result=validateResources({original_origin:'http://tauri.localhost',resource_read_only:true,assets,
      declared_safe_code_list:assets.some(x=>x.declared_safe_code_list),declared_presentation_case:assets.some(x=>x.declared_presentation_case)})
    return {nonce:config.nonce,original_pid:config.original_pid,...result,
      application_or_native_IPC_sent:false,Debugger_enabled:true,Debugger_pause_breakpoint_or_evaluation_sent:false,
      page_fetch_or_CSP_change_sent:false,resource_read_transport:'Debugger.getScriptSource',
      resource_bytes_transferred_to_owned_reader:true,resource_bytes_written_to_artifacts:false,
      digest_scope:'UTF-8 bytes of decoded V8 script source, not HTTP wire encoding',
      current_native_SYNC_REVIEW_CHANGED_bug_or_execution_claimed:false}
  }catch(error){
    if(failure)throw failure
    if(error instanceof ScriptReadFailure)throw error
    throw new ScriptReadFailure(stage,'unconfirmed',summary())
  }finally{
    clearTimeout(timer)
    // No callback breakpoint or input has been armed by this separate reader.
    try{socket?.close()}catch{}
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  Promise.resolve().then(()=>{
    need(process.argv.length===3&&Buffer.byteLength(process.argv[2])<=4096)
    const value=JSON.parse(process.argv[2]);need(value&&Object.keys(value).length===2&&value.configuration&&value.context)
    return observeCompiledScripts(value.configuration,value.context,process.env)
  }).then(value=>process.stdout.write(JSON.stringify(value)+'\n'))
    .catch(error=>{process.stdout.write(JSON.stringify(scriptDiagnostic(error))+'\n');process.exitCode=1})
}
