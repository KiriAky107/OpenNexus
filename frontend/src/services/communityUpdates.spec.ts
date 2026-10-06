// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { CommunityRelease, CommunitySource } from '@/contracts/community'
import vector from './fixtures/community-python-vector.json'
import { findCompatibleUpdate } from './communityService'

const installed = vector.release as CommunityRelease
const runtime = {app_version:'0.6.0',platform:'windows',architecture:'x86_64'}
let source: CommunitySource
const response = (value:unknown)=>new Response(JSON.stringify(value))
const sources = (keys=[vector.key])=>response({schema_version:1,source_id:'fixture',keys})
const release = (version:string,extra:Partial<CommunityRelease>={})=>({...installed,release_id:`version-${version}`,version,...extra})
beforeEach(()=>{source={id:'source',source_id:'fixture',url:'https://catalog.example/',enabled:true,keys:[{...vector.key}]};localStorage.clear()})
afterEach(()=>{vi.unstubAllGlobals()})

it('scans beyond 100 versions while retaining only the highest compatible release',async()=>{
  const items=Array.from({length:135},(_,index)=>release(`2.0.${134-index}`,{min_app_version:index<130?'0.7.0':'0.6.0'}))
  const fetch=vi.fn(async(input:URL)=>{
    if(input.pathname.endsWith('/sources'))return sources()
    const offset=Number(input.searchParams.get('offset'))
    return response({schema_version:1,total:135,offset,limit:100,items:items.slice(offset,offset+100)})
  })
  vi.stubGlobal('fetch',fetch)
  expect((await findCompatibleUpdate(source,installed,runtime))?.version).toBe('2.0.4')
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(localStorage.length).toBe(0)
})

it('orders old unordered lists semantically and excludes withdrawn or incompatible architectures',async()=>{
  const items=[release('1.9.0'),release('1.10.0-beta.11'),release('1.10.0'),release('2.0.0',{withdrawn:true}),release('3.0.0',{architectures:['aarch64']})]
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(sources()).mockResolvedValueOnce(response({items})))
  expect((await findCompatibleUpdate(source,installed,runtime))?.version).toBe('1.10.0')
})

it.each([{...vector.key,public_key:'changed'},{...vector.key,revoked:true}])('requires renewed source review before querying versions after a key change',async key=>{
  const fetch=vi.fn().mockResolvedValueOnce(sources([key]));vi.stubGlobal('fetch',fetch)
  await expect(findCompatibleUpdate(source,installed,runtime)).rejects.toThrow('公钥')
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('rejects changing counts across pages rather than claiming a complete update result',async()=>{
  const first=Array.from({length:100},(_,i)=>release(`2.0.${i}`))
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(sources()).mockResolvedValueOnce(response({schema_version:1,total:101,offset:0,limit:100,items:first})).mockResolvedValueOnce(response({schema_version:1,total:102,offset:100,limit:100,items:[release('3.0.0')]})))
  await expect(findCompatibleUpdate(source,installed,runtime)).rejects.toThrow('分页已变化')
})

it('rejects unrelated package responses and unconfirmed signing keys',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(sources()).mockResolvedValueOnce(response({items:[release('2.0.0',{package_id:'unrelated'})]})).mockResolvedValueOnce(sources()).mockResolvedValueOnce(response({items:[release('2.0.0',{key_id:'not-pinned'})]}))
  vi.stubGlobal('fetch',fetch)
  await expect(findCompatibleUpdate(source,installed,runtime)).rejects.toThrow('不匹配')
  await expect(findCompatibleUpdate(source,installed,runtime)).rejects.toThrow('未确认')
})

it('discards a late version response after source mutation or cancellation',async()=>{
  let resolve!:(value:Response)=>void
  const fetch=vi.fn().mockResolvedValueOnce(sources()).mockImplementationOnce(()=>new Promise<Response>(done=>{resolve=done}))
  vi.stubGlobal('fetch',fetch)
  const pending=findCompatibleUpdate(source,installed,runtime)
  await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2))
  source.enabled=false;resolve(response({items:[release('2.0.0')]}))
  await expect(pending).rejects.toThrow('已改变')
  source.enabled=true
  fetch.mockResolvedValueOnce(sources()).mockImplementationOnce(()=>new Promise<Response>(done=>{resolve=done}))
  const abort=new AbortController(),second=findCompatibleUpdate(source,installed,runtime,abort.signal)
  await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(4))
  abort.abort();resolve(response({items:[release('2.0.0')]}))
  await expect(second).rejects.toThrow()
})

it('does not query a changed source after its key response arrives',async()=>{
  let resolve!:(value:Response)=>void
  const fetch=vi.fn(()=>new Promise<Response>(done=>{resolve=done}));vi.stubGlobal('fetch',fetch)
  const pending=findCompatibleUpdate(source,installed,runtime)
  await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1))
  source.url='https://changed.example/'
  resolve(sources())
  await expect(pending).rejects.toThrow('已改变')
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('rejects malformed review fields before a candidate can reach the dialog',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(sources()).mockResolvedValueOnce(response({items:[release('2.0.0',{permissions:[null] as unknown as string[]})]})))
  await expect(findCompatibleUpdate(source,installed,runtime)).rejects.toThrow('不匹配')
})

it('refuses a noncanonical installed version before making any network request',async()=>{
  const fetch=vi.fn();vi.stubGlobal('fetch',fetch)
  await expect(findCompatibleUpdate(source,{...installed,version:'v1.0.0'},runtime)).rejects.toThrow('无法比较')
  expect(fetch).not.toHaveBeenCalled()
})
