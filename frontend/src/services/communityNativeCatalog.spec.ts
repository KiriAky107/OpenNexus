// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
const native=vi.hoisted(()=>({invoke:vi.fn()}))
vi.mock('@tauri-apps/api/core',()=>({invoke:native.invoke}))
vi.mock('./platform/desktop',()=>({isDesktop:()=>true}))
import { discoverSource, fetchCatalog } from './communityService'
import type { CommunitySource } from '@/contracts/community'

afterEach(()=>{native.invoke.mockReset();vi.unstubAllGlobals();localStorage.clear()})
const encoded=(value:unknown)=>btoa(JSON.stringify(value))
const source:CommunitySource={id:'native-catalog',url:'https://catalog.example/',enabled:true,keys:[]}
it('desktop browsing uses bounded Host IPC while browser fetch remains unavailable',async()=>{
  const fetch=vi.fn().mockRejectedValue(new TypeError('CSP blocked browser fetch'));vi.stubGlobal('fetch',fetch)
  native.invoke.mockImplementation(async(command)=>command==='extension_stage_prepare'?'owned-request':command==='extension_stage_cancel'?null:{status:200,body_base64:encoded({schema_version:1,source_id:'native-catalog',keys:[]}),etag:null})
  expect((await discoverSource(source.url)).source_id).toBe('native-catalog')
  expect(fetch).not.toHaveBeenCalled()
  expect(native.invoke).toHaveBeenCalledWith('community_catalog_request',{request:{request_id:'owned-request',query:{source:source.url,path:'/catalog/v1/sources',max_bytes:1024*1024,if_none_match:null}}})
  expect(native.invoke).toHaveBeenCalledWith('extension_stage_cancel',{requestId:'owned-request'})
})
it('retains query-specific conditional validation over the native transport',async()=>{
  let calls=0
  native.invoke.mockImplementation(async command=>{
    if(command==='extension_stage_prepare')return 'owned-request'
    if(command==='extension_stage_cancel')return null
    return ++calls===1?{status:200,body_base64:encoded({schema_version:1,items:[],total:0,offset:0,limit:30}),etag:'"owned-page"'}:{status:304,body_base64:null,etag:'"owned-page"'}
  })
  await fetchCatalog(source,'course','',undefined,0,30)
  expect((await fetchCatalog(source,'course','',undefined,0,30)).items).toEqual([])
  const requests=native.invoke.mock.calls.filter(call=>call[0]==='community_catalog_request')
  expect(requests[1]![1].request.query.if_none_match).toBe('"owned-page"')
})
it('abort before dispatch cancels the lease and sends no catalog operation',async()=>{
  const controller=new AbortController()
  native.invoke.mockImplementation(async command=>{if(command==='extension_stage_prepare'){controller.abort();return 'owned-request'}return null})
  await expect(discoverSource(source.url,controller.signal)).rejects.toThrow()
  expect(native.invoke.mock.calls.some(call=>call[0]==='community_catalog_request')).toBe(false)
  expect(native.invoke).toHaveBeenCalledWith('extension_stage_cancel',{requestId:'owned-request'})
})
it('a late native reply cannot overwrite a query cache after cancellation',async()=>{
  const controller=new AbortController()
  native.invoke.mockImplementation(async command=>{
    if(command==='extension_stage_prepare')return 'owned-request'
    if(command==='extension_stage_cancel')return null
    controller.abort()
    return {status:200,body_base64:encoded({schema_version:1,items:[],total:0,offset:0,limit:30}),etag:'"late"'}
  })
  await expect(fetchCatalog(source,'late','',controller.signal,0,30)).rejects.toThrow()
  expect(Object.keys(localStorage).some(key=>key.startsWith('community-cache-v2:'))).toBe(false)
})
