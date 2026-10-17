// Read already loaded, same-origin compiled modules. No application invocation,
// callback interception, DOM mutation, credentials or resource execution.
import { pathToFileURL } from 'node:url'
import { boundedJson, hostedTarget } from './original_outcome_transport.mjs'

const need=ok=>{if(!ok)throw Error('OWNED_COMPILED_RESOURCE_REFUSED')}
export function compiledMarkers(text){
  const lists=[...text.matchAll(/new\s+Set\s*\(\s*\[([^\]]{1,18000})\]\s*\)/g)]
  return {
    declared_safe_code_list:lists.some(match=>['SYNC_CONNECTION_FAILED','SYNC_RESPONSE_INVALID','SYNC_REVIEW_CHANGED']
      .every(code=>match[1].includes('"'+code+'"')||match[1].includes("'"+code+"'"))),
    declared_presentation_case:/case\s*["']SYNC_REVIEW_CHANGED["']\s*:/.test(text),
  }
}

export const resourceExpression=`(async()=>{
  if(location.origin!=='http://tauri.localhost')throw Error('OWNED_RESOURCE_ORIGIN');
  const markers=${compiledMarkers.toString()};
  const urls=new Set();
  const admit=value=>{
    const url=new URL(value,location.href);
    if(url.origin==='http://tauri.localhost'&&!url.username&&!url.password&&!url.search&&!url.hash&&/^\\/assets\\/[A-Za-z0-9_.-]+\\.js$/.test(url.pathname))urls.add(url.href);
  };
  for(const script of document.querySelectorAll('script[type="module"][src]'))admit(script.src);
  for(const value of performance.getEntriesByType('resource'))admit(value.name);
  if(urls.size<1||urls.size>24)throw Error('OWNED_RESOURCE_BOUND');
  const assets=[];let total=0;
  for(const value of [...urls].sort()){
    const response=await fetch(value,{redirect:'error',credentials:'omit',cache:'force-cache'});
    if(response.status!==200||!response.body)throw Error('OWNED_RESOURCE_UNAVAILABLE');
    const reader=response.body.getReader(),parts=[];let size=0;
    try{while(true){const item=await reader.read();if(item.done)break;size+=item.value.byteLength;total+=item.value.byteLength;
      if(size>8*1024**2||total>16*1024**2)throw Error('OWNED_RESOURCE_BOUND');parts.push(item.value);}}
    finally{await reader.cancel();}
    const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.byteLength;}
    const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
    assets.push({path:new URL(value).pathname,bytes:size,sha256:hash,...markers(text)});
  }
  return {original_origin:location.origin,resource_read_only:true,assets,
    declared_safe_code_list:assets.some(a=>a.declared_safe_code_list),declared_presentation_case:assets.some(a=>a.declared_presentation_case)};
})()`

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
  const target=hostedTarget(config,context,env),read=dependencies.readJson??boundedJson
  const pages=await read(target.http+'/json/list');need(Array.isArray(pages))
  const own=pages.filter(p=>p.type==='page');need(own.length===1&&own[0].title==='OpenNexus'
    &&own[0].webSocketDebuggerUrl===target.url&&new URL(own[0].url).origin==='http://tauri.localhost')
  const socket=dependencies.socketFactory?await dependencies.socketFactory(target.url):new WebSocket(target.url)
  let timer
  try{
    const result=await new Promise((resolve,reject)=>{
      const fail=()=>reject(Error('OWNED_COMPILED_RESOURCE_UNCONFIRMED'))
      timer=setTimeout(fail,15000)
      const send=()=>socket.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression:resourceExpression,returnByValue:true,awaitPromise:true}}))
      socket.addEventListener('message',event=>{
        try{
          need(typeof event.data==='string'&&Buffer.byteLength(event.data)<=65536)
          const value=JSON.parse(event.data);need(value.id===1&&!value.error&&!value.result?.exceptionDetails
            &&value.result?.result?.type==='object')
          resolve(validateResources(value.result.result.value))
        }catch{fail()}
      })
      socket.addEventListener('error',fail);socket.addEventListener('close',fail)
      if(dependencies.socketFactory)send();else socket.addEventListener('open',send,{once:true})
    })
    return {nonce:config.nonce,original_pid:config.original_pid,...result,
      application_or_native_IPC_sent:false,Debugger_enabled:false,raw_resource_bytes_exported:false,
      current_native_SYNC_REVIEW_CHANGED_bug_or_execution_claimed:false}
  }finally{
    clearTimeout(timer)
    // This connection never enables the debugger or pauses a callback.
    socket.close()
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  Promise.resolve().then(()=>{
    need(process.argv.length===3&&Buffer.byteLength(process.argv[2])<=4096)
    const value=JSON.parse(process.argv[2]);need(value&&Object.keys(value).length===2&&value.configuration&&value.context)
    return observeCompiledResources(value.configuration,value.context,process.env)
  }).then(value=>process.stdout.write(JSON.stringify(value)+'\n'))
    .catch(()=>{process.stdout.write('{"error":"OWNED_COMPILED_RESOURCE_UNCONFIRMED"}\n');process.exitCode=1})
}
