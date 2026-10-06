// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'
import vector from './fixtures/community-python-vector.json'
import catalogContract from './fixtures/community-v1-catalog.json'
import { compare, valid } from 'semver'
import type { CommunityRelease, CommunitySource } from '@/contracts/community'
vi.mock('./platform/desktop', () => ({ isDesktop: () => false }))
import { cachedCatalog, fetchCatalog, installRelease, verifyRelease } from './communityService'
import { catalogIdentity, readCatalogCache } from './communityCatalogCache'

const release = vector.release as CommunityRelease
const source: CommunitySource = { id:'catalog',url:'https://catalog.example/',source_id:'fixture',enabled:true,keys:[vector.key] }
const page = (offset=0,total=135) => ({schema_version:1,items:[release],total,offset,limit:30})
const ok = (value:unknown,etag='"page-one"') => new Response(JSON.stringify(value),{headers:{ETag:etag}})
beforeEach(() => { localStorage.clear(); vi.stubGlobal('crypto',webcrypto) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shares canonical semantic ordering and validation with the service',()=>{
  const ordered = [...catalogContract.versions].sort((a,b)=>compare(b,a)||(a<b?-1:a>b?1:0))
  expect(ordered).toEqual(catalogContract.descending)
  expect(catalogContract.invalid.slice(0,5).every(version=>valid(version)===null)).toBe(true)
})

it.each(catalogContract.invalid)('rejects noncanonical signed versions before cryptographic installation: %s',async version=>{
  await expect(verifyRelease({...release,version},vector.key,new Uint8Array())).rejects.toThrow('版本')
})

it('keeps query/type/page/source URL and pinned key caches separate, with exact 304 reuse',async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(page())).mockResolvedValueOnce(new Response(null,{status:304,headers:{ETag:'"page-one"'}})).mockResolvedValueOnce(ok(page(30),'"page-two"'))
  vi.stubGlobal('fetch',fetch)
  const first = await fetchCatalog(source,'课程','persona')
  const validated = await fetchCatalog(source,'课程','persona')
  expect(validated.cache?.revalidated).toBe(true)
  expect(validated.cache?.fetchedAt).toBe(first.cache?.fetchedAt)
  expect(fetch.mock.calls[1]?.[1].headers).toEqual({'If-None-Match':'"page-one"'})
  await fetchCatalog(source,'课程','persona',undefined,30)
  expect(fetch.mock.calls[2]?.[1].headers).toEqual({})
  expect(cachedCatalog(source,'课程','persona',30)?.offset).toBe(30)
  const variants: Array<[CommunitySource,string,string,number]> = [[source,'different','persona',0],[source,'课程','theme',0],[source,'课程','persona',60],[{...source,url:'https://other.example/'},'课程','persona',0],[{...source,keys:[{...vector.key,public_key:'changed'}]},'课程','persona',0]]
  for (const args of variants) {
    expect(cachedCatalog(args[0],args[1],args[2],args[3])).toBeNull()
  }
  expect(cachedCatalog(source,'课程','persona')?.items).toEqual([release])
  expect(fetch.mock.calls[0]?.[1]).toMatchObject({credentials:'omit',redirect:'error',cache:'no-store'})
})

it('does not write cache or return a late response after abort or source mutation',async () => {
  let resolve!: (value:Response) => void
  vi.stubGlobal('fetch',vi.fn(() => new Promise<Response>(done => {resolve=done})))
  const abort = new AbortController()
  const pending = fetchCatalog(source,'old','',abort.signal)
  abort.abort(); resolve(ok(page()))
  await expect(pending).rejects.toThrow()
  expect(cachedCatalog(source,'old')).toBeNull()
  const mutable = {...source}
  const changed = fetchCatalog(mutable,'new')
  mutable.url = 'https://other.example/'
  resolve(ok(page()))
  await expect(changed).rejects.toThrow('改变')
  expect(cachedCatalog(source,'new')).toBeNull()
})

it('bounds owned cached pages, ignores old unscoped cache and preserves other settings',async () => {
  localStorage.setItem('community-cache:catalog',JSON.stringify(page()))
  localStorage.setItem('user-setting','keep')
  expect(cachedCatalog(source)).toBeNull()
  vi.stubGlobal('fetch',vi.fn().mockImplementation(async()=>ok(page())))
  for (let index=0;index<35;index++) await fetchCatalog(source,`query-${index}`)
  expect(Object.keys(localStorage).filter(key=>key.startsWith('community-cache-v2:')).length).toBe(32)
  expect(cachedCatalog(source,'query-0')).toBeNull()
  expect(cachedCatalog(source,'query-34')).not.toBeNull()
  expect(localStorage.getItem('user-setting')).toBe('keep')
  expect(localStorage.getItem('community-cache:catalog')).not.toBeNull()
})

it('keeps a valid live response when storage is unavailable and rejects malformed pages',async () => {
  const fetch = vi.fn().mockResolvedValueOnce(ok(page())).mockResolvedValueOnce(ok(page(30))).mockResolvedValueOnce(ok({...page(),total:0}))
  vi.stubGlobal('fetch',fetch)
  const storage = vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('quota')})
  expect((await fetchCatalog(source)).total).toBe(135)
  storage.mockRestore()
  await expect(fetchCatalog(source)).rejects.toThrow('协议')
  await expect(fetchCatalog(source)).rejects.toThrow('计数')
  expect(readCatalogCache(catalogIdentity(source,'','',0,30),0,30)).toBeNull()
})

it('live installation of an item after the first 100 avoids catalog cache and rechecks its signature',async () => {
  const bytes = Uint8Array.from(atob(vector.archive_base64),c=>c.charCodeAt(0))
  const fetch = vi.fn().mockResolvedValueOnce(ok(release)).mockResolvedValueOnce(ok({schema_version:1,source_id:'fixture',keys:[vector.key]})).mockResolvedValueOnce(new Response(bytes))
  vi.stubGlobal('fetch',fetch)
  expect(await installRelease(source,release)).toContain('候选')
  expect(String(fetch.mock.calls[0]?.[0])).toContain(`/catalog/v1/releases/${release.release_id}`)
  expect(fetch.mock.calls.every(call=>!String(call[0]).includes('/packages?'))).toBe(true)
  expect(fetch.mock.calls.every(call=>call[1].cache==='no-store')).toBe(true)
})

it('falls back to the old version endpoint and rejects changed permission metadata',async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(null,{status:404})).mockResolvedValueOnce(ok({items:[{...release,permissions:['new-permission']}]}))
  vi.stubGlobal('fetch',fetch)
  await expect(installRelease(source,release)).rejects.toThrow('权限')
  expect(String(fetch.mock.calls[1]?.[0])).toContain(`/packages/${release.namespace}/${release.package_id}/releases`)
  expect(new URL(String(fetch.mock.calls[1]?.[0])).searchParams.get('version')).toBe(release.version)
  expect(fetch).toHaveBeenCalledTimes(2)
})

it.each([
  {items:[release],schema_version:1,total:135,offset:0,limit:100},
  {items:[release],schema_version:1,total:1,offset:100,limit:100},
  {items:[release,release]},
])('rejects incomplete or duplicate live fallback results before reading keys or installing',async value=>{
  const fetch = vi.fn().mockResolvedValueOnce(new Response(null,{status:404})).mockResolvedValueOnce(ok(value))
  vi.stubGlobal('fetch',fetch)
  await expect(installRelease(source,release)).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(localStorage.getItem('community-candidates-v1')).toBeNull()
})
