// A bounded connection to the sole original hosted emulator WebView. No input,
// application invocation, scope/property dump, credential or external origin.
import { admitProtocol, configuration, condition, originalCallbackExpression } from './original_outcome_gate.mjs'
import { originalExit } from './original_outcome_controller.mjs'

const need=ok=>{if(!ok)throw Error('OWNED_NATIVE_OUTCOME_TRANSPORT_REFUSED')}
const ownKeys=(value,keys)=>value&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k))
const text=value=>typeof value==='string'&&value.length>0&&value.length<=256

export function hostedTarget(config,context,env){
  const c=configuration(config);need(c.mode==='complete')
  need(ownKeys(context,['run_id','tools_commit','socket_url']))
  need(Number.isSafeInteger(context.run_id)&&context.run_id>0&&typeof context.tools_commit==='string'&&/^[0-9a-f]{40}$/.test(context.tools_commit))
  need(env.GITHUB_ACTIONS==='true'&&env.RUNNER_ENVIRONMENT==='github-hosted'&&env.RUNNER_OS==='Linux'
    &&env.GITHUB_REPOSITORY==='KiriAky107/OpenNexus'&&env.GITHUB_RUN_ID===String(context.run_id)&&env.GITHUB_SHA===context.tools_commit)
  const url=new URL(context.socket_url)
  need(url.protocol==='ws:'&&url.hostname==='127.0.0.1'&&!url.username&&!url.password&&!url.search&&!url.hash
    &&Number.isInteger(Number(url.port))&&Number(url.port)>=1024&&Number(url.port)<=65535
    &&/^\/devtools\/page\/[A-Za-z0-9_-]+$/.test(url.pathname))
  return {url:url.href,http:'http://127.0.0.1:'+url.port}
}

export async function boundedJson(url){
  const parsed=new URL(url)
  need(parsed.protocol==='http:'&&parsed.hostname==='127.0.0.1'&&Number(parsed.port)>=1024&&Number(parsed.port)<=65535&&!parsed.username&&!parsed.password
    &&['/json/protocol','/json/list'].includes(parsed.pathname)&&!parsed.search&&!parsed.hash)
  const response=await fetch(url,{redirect:'error',signal:AbortSignal.timeout(5000)})
  need(response.status===200&&response.body)
  const parts=[];let size=0;const reader=response.body.getReader()
  try{
    while(true){const item=await reader.read();if(item.done)break;size+=item.value.byteLength;need(size<=4*1024**2);parts.push(item.value)}
  }finally{await reader.cancel()}
  need(size>0);const bytes=new Uint8Array(size);let offset=0
  for(const part of parts){bytes.set(part,offset);offset+=part.byteLength}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))
}

export class OriginalOutcomeTransport{
  constructor(config,socket,onPaused,onClosed,onFault,options={}){
    this.config=configuration(config);need(this.config.mode==='complete')
    need(socket&&typeof socket.addEventListener==='function'&&typeof socket.send==='function'&&typeof socket.close==='function')
    need([onPaused,onClosed,onFault].every(fn=>typeof fn==='function'))
    this.socket=socket;this.onPaused=onPaused;this.onClosed=onClosed;this.onFault=onFault
    this.timeout=options.timeout??10000;need(Number.isSafeInteger(this.timeout)&&this.timeout>0&&this.timeout<=10000)
    this.next=0;this.pending=new Map();this.object=null;this.breakpoint=null;this.frame=null;this.closed=false;this.explicitClose=false
    socket.addEventListener('message',event=>this.receive(event))
    socket.addEventListener('close',()=>this.remoteClosed())
    socket.addEventListener('error',()=>this.onFault({code:'OWNED_DEBUGGER_CONNECTION_UNCONFIRMED'}))
  }
  allow(method,params){
    if(method==='Debugger.enable')return ownKeys(params,[])
    if(method==='Runtime.evaluate')return ownKeys(params,['expression','returnByValue'])&&params.expression===originalCallbackExpression&&params.returnByValue===false
    if(method==='Debugger.setBreakpointOnFunctionCall')return ownKeys(params,['objectId','condition'])&&text(this.object)&&params.objectId===this.object&&params.condition===condition(this.config)
    if(method==='Debugger.evaluateOnCallFrame')return ownKeys(params,['callFrameId','expression','returnByValue'])&&text(this.frame)&&params.callFrameId===this.frame&&params.expression===condition(this.config)&&params.returnByValue===true
    return false // Includes Debugger.resume, property/scope reads and every app invocation.
  }
  call=(method,params)=>{
    need(!this.closed&&!this.explicitClose&&this.next<32&&this.pending.size<4&&this.allow(method,params))
    const id=++this.next
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('OWNED_DEBUGGER_ORIGINAL_REPLY_UNKNOWN'));this.onFault({code:'OWNED_DEBUGGER_ORIGINAL_REPLY_UNKNOWN'})},this.timeout)
      this.pending.set(id,{resolve,reject,timer,method})
      // A send failure has unknown delivery. Reject without any resend or close.
      try{this.socket.send(JSON.stringify({id,method,params}))}
      catch{clearTimeout(timer);this.pending.delete(id);reject(Error('OWNED_DEBUGGER_ORIGINAL_SEND_UNKNOWN'));this.onFault({code:'OWNED_DEBUGGER_ORIGINAL_SEND_UNKNOWN'})}
    })
  }
  receive(event){
    try{
      need(typeof event.data==='string'&&Buffer.byteLength(event.data,'utf8')<=1024**2)
      const message=JSON.parse(event.data)
      if(Object.hasOwn(message,'id')){
        const pending=this.pending.get(message.id);if(!pending)return
        clearTimeout(pending.timer);this.pending.delete(message.id)
        if(message.error){pending.reject(Error('OWNED_DEBUGGER_ORIGINAL_COMMAND_REFUSED'));return}
        try{
          const value=message.result
          if(pending.method==='Runtime.evaluate'&&!value?.exceptionDetails&&value?.result?.type==='function'){
            need(text(value.result.objectId));this.object=value.result.objectId
          }
          if(pending.method==='Debugger.setBreakpointOnFunctionCall'){need(text(value?.breakpointId));this.breakpoint=value.breakpointId}
          pending.resolve(value)
        }catch{pending.reject(Error('OWNED_DEBUGGER_ORIGINAL_RESULT_REFUSED'));this.onFault({code:'OWNED_DEBUGGER_ORIGINAL_RESULT_REFUSED'})}
        return
      }
      if(message.method!=='Debugger.paused')return
      const value=message.params
      need(Array.isArray(value?.hitBreakpoints)&&value.hitBreakpoints.length===1&&value.hitBreakpoints[0]===this.breakpoint
        &&Array.isArray(value.callFrames)&&value.callFrames.length>0&&value.callFrames.length<=128)
      const frame=value.callFrames[0];need(frame.functionName==='runCallback'&&text(frame.callFrameId));this.frame=frame.callFrameId
      // Drop scopes, this, arguments, script locations and all other raw data.
      const safe={hitBreakpoints:[this.breakpoint],callFrames:[{functionName:'runCallback',callFrameId:this.frame}]}
      Promise.resolve(this.onPaused(safe)).catch(()=>this.onFault({code:'OWNED_ORIGINAL_PAUSE_UNCONFIRMED'}))
    }catch{this.onFault({code:'OWNED_DEBUGGER_MESSAGE_REFUSED'})}
  }
  remoteClosed(){
    this.closed=true
    for(const pending of this.pending.values()){clearTimeout(pending.timer);pending.reject(Error('OWNED_DEBUGGER_CLOSED_WITH_ORIGINAL_REPLY_UNKNOWN'))}
    this.pending.clear();this.onClosed()
  }
  close=(proof)=>{
    need(!this.explicitClose);originalExit(proof,this.config.original_pid)
    this.explicitClose=true;this.socket.close()
  }
}

export async function connectOriginalOutcome(config,context,env,handlers,dependencies={}){
  const target=hostedTarget(config,context,env),read=dependencies.readJson??boundedJson
  const pages=await read(target.http+'/json/list');need(Array.isArray(pages))
  const own=pages.filter(p=>p.type==='page');need(own.length===1&&own[0].title==='OpenNexus'
    &&own[0].webSocketDebuggerUrl===target.url&&new URL(own[0].url).origin==='http://tauri.localhost')
  const protocol=await read(target.http+'/json/protocol');admitProtocol(protocol)
  const socket=dependencies.socketFactory?await dependencies.socketFactory(target.url):new WebSocket(target.url)
  // An admission failure before any breakpoint/input may close its unopened
  // connection. Once transport is returned, only an original-exit proof closes.
  if(!dependencies.socketFactory)await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{socket.close();reject(Error('OWNED_DEBUGGER_CONNECT_TIMEOUT'))},5000)
    socket.addEventListener('open',()=>{clearTimeout(timer);resolve()},{once:true})
    socket.addEventListener('error',()=>{clearTimeout(timer);socket.close();reject(Error('OWNED_DEBUGGER_CONNECT_FAILED'))},{once:true})
  })
  return {protocol,transport:new OriginalOutcomeTransport(config,socket,handlers.paused,handlers.closed,handlers.fault,dependencies.options)}
}
