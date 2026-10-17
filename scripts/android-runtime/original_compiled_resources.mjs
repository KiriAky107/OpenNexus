// Read already loaded, same-origin compiled modules. No application invocation,
// callback interception, DOM mutation, credentials or resource execution.
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { boundedJson, hostedTarget } from './original_outcome_transport.mjs'

const need=ok=>{if(!ok)throw Error('OWNED_COMPILED_RESOURCE_REFUSED')}
const stages=new Set(['admission','page-list','socket-open','resource-tree','resource-content','resource-summary'])
const reasons=new Set(['unconfirmed','timeout','send-unconfirmed','connection-unconfirmed','reply-refused','summary-refused',
  'origin','bound','resource-unavailable','no-owned-script'])
class ResourceReadFailure extends Error{
  constructor(stage,reason){super('OWNED_COMPILED_RESOURCE_UNCONFIRMED');this.stage=stage;this.reason=reason}
}
export function resourceDiagnostic(error){
  return {error:'OWNED_COMPILED_RESOURCE_UNCONFIRMED',
    stage:error instanceof ResourceReadFailure&&stages.has(error.stage)?error.stage:'admission',
    reason:error instanceof ResourceReadFailure&&reasons.has(error.reason)?error.reason:'unconfirmed'}
}
export function compiledMarkers(text){
  const lists=[...text.matchAll(/new\s+Set\s*\(\s*\[([^\]]{1,18000})\]\s*\)/g)]
  return {
    declared_safe_code_list:lists.some(match=>['SYNC_CONNECTION_FAILED','SYNC_RESPONSE_INVALID','SYNC_REVIEW_CHANGED']
      .every(code=>match[1].includes('"'+code+'"')||match[1].includes("'"+code+"'"))),
    declared_presentation_case:/case\s*["']SYNC_REVIEW_CHANGED["']\s*:/.test(text),
  }
}

export function resourceCandidates(result){
  const tree=result?.frameTree,frame=tree?.frame
  const origin=frame&&new URL(frame.url)
  need(frame&&typeof frame.id==='string'&&/^[A-Za-z0-9_.:-]{1,128}$/.test(frame.id)
    &&origin.origin==='http://tauri.localhost'&&!origin.username&&!origin.password
    &&Array.isArray(tree.resources)&&tree.resources.length<=512)
  const urls=new Set()
  for(const item of tree.resources){
    if(item.type!=='Script'||typeof item.url!=='string')continue
    const url=new URL(item.url)
    if(url.origin==='http://tauri.localhost'&&!url.username&&!url.password&&!url.search&&!url.hash
      &&/^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(url.pathname))urls.add(url.href)
  }
  need(urls.size>0&&urls.size<=24)
  return {frameId:frame.id,urls:[...urls].sort()}
}

export function decodedResource(value){
  need(value&&Object.keys(value).length===2&&typeof value.content==='string'&&typeof value.base64Encoded==='boolean')
  need(value.content.length<=12*1024**2)
  const bytes=Buffer.from(value.content,value.base64Encoded?'base64':'utf8')
  need(bytes.length>0&&bytes.length<=8*1024**2)
  if(value.base64Encoded)need(bytes.toString('base64')===value.content)
  const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes)
  return {bytes,text}
}

export function validateResources(value){
  need(value&&Object.keys(value).length===5&&value.original_origin==='http://tauri.localhost'&&value.resource_read_only===true
    &&value.declared_safe_code_list===true&&value.declared_presentation_case===true&&Array.isArray(value.assets)
    &&value.assets.length>0&&value.assets.length<=24)
  let total=0;const paths=new Set()
  for(const item of value.assets){
    need(item&&Object.keys(item).length===5&&typeof item.path==='string'&&/^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(item.path)
      &&!paths.has(item.path)&&Number.isSafeInteger(item.bytes)&&item.bytes>0&&item.bytes<=8*1024**2
      &&typeof item.sha256==='string'&&/^[0-9a-f]{64}$/.test(item.sha256)
      &&typeof item.declared_safe_code_list==='boolean'&&typeof item.declared_presentation_case==='boolean')
    paths.add(item.path);total+=item.bytes
  }
  need(total<=16*1024**2&&value.assets.some(x=>x.declared_safe_code_list)&&value.assets.some(x=>x.declared_presentation_case))
  return value
}

export async function observeCompiledResources(config,context,env,dependencies={}){
  let stage='admission',socket,timer,openReject
  const pending=new Map();let next=0,failed=false,received=0,messages=0
  const fault=reason=>{
    failed=true;const error=new ResourceReadFailure(stage,reasons.has(reason)?reason:'unconfirmed')
    openReject?.(error)
    for(const value of pending.values())value.reject(error)
    pending.clear()
  }
  try{
    const target=hostedTarget(config,context,env),read=dependencies.readJson??boundedJson
    stage='page-list'
    const pages=await read(target.http+'/json/list');need(Array.isArray(pages))
    const own=pages.filter(p=>p.type==='page');need(own.length===1&&own[0].title==='OpenNexus'
      &&own[0].webSocketDebuggerUrl===target.url&&new URL(own[0].url).origin==='http://tauri.localhost')
    stage='socket-open'
    socket=dependencies.socketFactory?await dependencies.socketFactory(target.url):new WebSocket(target.url)
    const opened=dependencies.socketFactory?Promise.resolve():new Promise((resolve,reject)=>{
      openReject=reject;socket.addEventListener('open',resolve,{once:true})
    })
    socket.addEventListener('message',event=>{
      try{
        need(typeof event.data==='string'&&Buffer.byteLength(event.data)<=20*1024**2)
        received+=Buffer.byteLength(event.data);messages++;need(received<=40*1024**2&&messages<=512)
        const value=JSON.parse(event.data),reply=pending.get(value?.id)
        if(!reply)return // Unrelated bounded notifications never replace a reply.
        pending.delete(value.id)
        if(value.error)reply.reject(new ResourceReadFailure(stage,'reply-refused'))
        else reply.resolve(value.result)
      }catch{fault('reply-refused')}
    })
    socket.addEventListener('error',()=>fault('connection-unconfirmed'));socket.addEventListener('close',()=>fault('connection-unconfirmed'))
    timer=setTimeout(()=>fault('timeout'),15000)
    await opened
    let candidate
    const call=(method,params)=>{
      need(!failed&&next<25&&pending.size===0)
      need(method==='Page.getResourceTree'&&next===0&&Object.keys(params).length===0
        ||method==='Page.getResourceContent'&&candidate&&Object.keys(params).length===2
          &&params.frameId===candidate.frameId&&candidate.urls.includes(params.url))
      const id=++next
      return new Promise((resolve,reject)=>{
        pending.set(id,{resolve,reject})
        try{socket.send(JSON.stringify({id,method,params}))}catch{fault('send-unconfirmed')}
      })
    }
    stage='resource-tree';candidate=resourceCandidates(await call('Page.getResourceTree',{}))
    const assets=[];let total=0
    for(const url of candidate.urls){
      stage='resource-content'
      const {bytes,text}=decodedResource(await call('Page.getResourceContent',{frameId:candidate.frameId,url}))
      total+=bytes.length;need(total<=16*1024**2)
      assets.push({path:new URL(url).pathname,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),...compiledMarkers(text)})
    }
    stage='resource-summary'
    const result=validateResources({original_origin:'http://tauri.localhost',resource_read_only:true,assets,
      declared_safe_code_list:assets.some(a=>a.declared_safe_code_list),declared_presentation_case:assets.some(a=>a.declared_presentation_case)})
    return {nonce:config.nonce,original_pid:config.original_pid,...result,
      application_or_native_IPC_sent:false,Debugger_enabled:false,page_fetch_or_CSP_change_sent:false,
      resource_read_transport:'Page.getResourceContent',resource_bytes_transferred_to_owned_reader:true,
      resource_bytes_written_to_artifacts:false,digest_scope:'Decoded CDP resource content bytes, not HTTP wire encoding',
      current_native_SYNC_REVIEW_CHANGED_bug_or_execution_claimed:false}
  }catch(error){
    throw error instanceof ResourceReadFailure?error:new ResourceReadFailure(stage,'unconfirmed')
  }finally{
    clearTimeout(timer)
    // This connection never enables the debugger or pauses a callback.
    try{socket?.close()}catch{}
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  Promise.resolve().then(()=>{
    need(process.argv.length===3&&Buffer.byteLength(process.argv[2])<=4096)
    const value=JSON.parse(process.argv[2]);need(value&&Object.keys(value).length===2&&value.configuration&&value.context)
    return observeCompiledResources(value.configuration,value.context,process.env)
  }).then(value=>process.stdout.write(JSON.stringify(value)+'\n'))
    .catch(error=>{process.stdout.write(JSON.stringify(resourceDiagnostic(error))+'\n');process.exitCode=1})
}
