// An admission witness belongs to the original live connection and scope.
// A JSON method list, copied witness or parent-supplied lookalike cannot arm it.
const need=value=>{if(!value)throw Error('OWNED_LIVE_PROTOCOL_REFUSED')}
const admissions=new WeakMap()
const owners=new WeakMap()
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)
const digest=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value)
function binding(config){
  const keys=['mode','nonce','original_pid','sequence','choice','destination','fingerprint','expected_hash',...(config?.mode==='reject'?['concurrent_hash']:[])]
  need(config&&uuid(config.nonce)&&Number.isSafeInteger(config.original_pid)&&config.original_pid>0
    &&['complete','reject'].includes(config.mode)&&digest(config.fingerprint)&&digest(config.expected_hash)
    &&config.sequence===2&&config.choice==='copy'&&config.destination==='runtime-conflict-copy.md'
    &&Object.keys(config).length===keys.length&&keys.every(k=>Object.hasOwn(config,k)))
  if(config.mode==='reject')need(digest(config.concurrent_hash)&&config.concurrent_hash!==config.expected_hash)
  return JSON.stringify([config.mode,config.nonce,config.original_pid,config.sequence,config.choice,
    config.destination,config.fingerprint,config.expected_hash,config.concurrent_hash??null])
}
export function admitLiveProtocol(reply,config,owner){
  const scope=binding(config)
  need(owner&&(typeof owner==='object'||typeof owner==='function')&&reply&&typeof reply==='object'
    &&!Array.isArray(reply)&&Object.keys(reply).length===1&&Array.isArray(reply.domains)
    &&reply.domains.length>0&&reply.domains.length<=128&&!owners.has(owner))
  const names=new Set()
  for(const domain of reply.domains){
    need(domain&&Object.keys(domain).length===2&&typeof domain.name==='string'
      &&/^[A-Za-z][A-Za-z0-9]{0,63}$/.test(domain.name)&&!names.has(domain.name)
      &&typeof domain.version==='string'&&/^[0-9]{1,3}\.[0-9]{1,3}$/.test(domain.version))
    names.add(domain.name)
  }
  need(names.has('Debugger')&&names.has('Runtime'))
  const witness=Object.freeze({kind:'owned-live-domain-reply'})
  admissions.set(witness,{scope,owner})
  owners.set(owner,witness)
  return witness
}
export function requireLiveProtocol(witness,config,owner){
  const value=witness&&admissions.get(witness)
  need(value&&value.owner===owner&&value.scope===binding(config))
}
