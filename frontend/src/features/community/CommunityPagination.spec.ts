// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import vector from '@/services/fixtures/community-python-vector.json'
const api = vi.hoisted(()=>({fetch:vi.fn(),cached:vi.fn(),install:vi.fn()}))
vi.mock('@/services/platform/desktop',()=>({isDesktop:()=>false}))
vi.mock('@/services/communityService',()=>({loadSources:()=>[{id:'first',url:'https://first.example',enabled:true,keys:[]},{id:'second',url:'https://second.example',enabled:true,keys:[]}],saveSources:vi.fn(),discoverSource:vi.fn(),fetchCatalog:api.fetch,cachedCatalog:api.cached,installRelease:api.install,isCommunityNetworkError:(reason:unknown)=>reason instanceof TypeError}))
import CommunityView from './CommunityView.vue'
const options = {global:{stubs:{AppDialog:{template:'<section><slot /></section>'}}}}
const result = (offset=0)=>({schema_version:1,items:[{...vector.release,name:offset?`Page item ${offset}`:'First page item'}],total:135,offset,limit:30,cache:{fetchedAt:'2026-10-06T00:00:00Z',checkedAt:'2026-10-06T00:00:00Z',revalidated:false}})
beforeEach(()=>{vi.clearAllMocks();localStorage.clear()})
const button = (wrapper:ReturnType<typeof mount>,text:string)=>wrapper.findAll('button').find(b=>b.text()===text)!

it('browses beyond 100 results and resets pagination/detail when the query changes',async()=>{
  api.fetch.mockImplementation(async(_s,_q,_k,_signal,offset)=>result(offset))
  const wrapper=mount(CommunityView,options)
  await button(wrapper,'搜索 / 刷新').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('1 / 5')
  for(let index=0;index<4;index++){await button(wrapper,'下一页').trigger('click');await flushPromises()}
  expect(wrapper.text()).toContain('Page item 120')
  expect(api.fetch.mock.calls.at(-1)?.[4]).toBe(120)
  expect(button(wrapper,'下一页').attributes('disabled')).toBeDefined()
  await wrapper.get('.community-card').trigger('click')
  expect(wrapper.text()).toContain('作者 / 来源')
  await wrapper.findAll('input')[1]!.setValue('changed')
  expect(wrapper.find('.community-card').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('作者 / 来源')
  await button(wrapper,'搜索 / 刷新').trigger('click');await flushPromises()
  expect(api.fetch.mock.calls.at(-1)?.slice(1,3)).toEqual(['changed',''])
  expect(api.fetch.mock.calls.at(-1)?.[4]).toBe(0)
  wrapper.unmount()
})

it('aborts source/query changes and refuses late results and errors',async()=>{
  let finish!:(data:unknown)=>void
  api.fetch.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve})).mockResolvedValueOnce(result())
  const wrapper=mount(CommunityView,options)
  await button(wrapper,'搜索 / 刷新').trigger('click')
  const originalSignal=api.fetch.mock.calls[0]?.[3] as AbortSignal
  await wrapper.findAll('select')[0]!.setValue('second');await flushPromises()
  expect(originalSignal.aborted).toBe(true)
  finish({...result(),items:[{...vector.release,name:'stale source'}]});await flushPromises()
  expect(wrapper.text()).not.toContain('stale source')
  expect(api.cached).not.toHaveBeenCalled()
  wrapper.unmount()
})

it('uses only the failed query page cache, shows its date and disables installation',async()=>{
  api.fetch.mockRejectedValue(new TypeError('Failed to fetch'))
  api.cached.mockReturnValue(result())
  const wrapper=mount(CommunityView,options)
  await wrapper.findAll('input')[1]!.setValue('课程')
  await wrapper.findAll('select')[1]!.setValue('persona')
  await button(wrapper,'搜索 / 刷新').trigger('click');await flushPromises()
  expect(api.cached).toHaveBeenCalledWith(expect.objectContaining({id:'first'}),'课程','persona',0,30)
  expect(wrapper.text()).toContain('此查询的离线缓存')
  expect(wrapper.text()).toContain('最近核对')
  await wrapper.get('.community-card').trigger('click')
  expect(button(wrapper,'校验并安装').attributes('disabled')).toBeDefined()
  expect(api.install).not.toHaveBeenCalled()
  wrapper.unmount()
})
