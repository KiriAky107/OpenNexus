// @vitest-environment happy-dom
import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,afterEach,expect,it,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useWorkspaceStore} from '@/stores/workspace'
import {benchmarkService,type BenchmarkRun} from '@/services/benchmarkService'
import BenchmarkComparison from './BenchmarkComparison.vue'
const run=(id:string):BenchmarkRun=>({id,kind:'rag',datasetId:'course',datasetHash:'hash',status:'completed',progress:1,errorCode:null,configSnapshot:{vault_scope:'one'}})
const report=(id:string)=>({run_id:id,kind:'rag' as const,dataset_id:'course',dataset_hash:'hash',status:'completed',metrics:{recall:id==='a'?1:.5,unavailable:null},config_snapshot:{vault_scope:'one',top_k:id==='a'?5:10},cases:[{case_id:'source',mode:'fts',repeat:0,recall:id==='a'?1:.5}]})
beforeEach(()=>{setActivePinia(createPinia());useWorkspaceStore().vaultId='one';vi.spyOn(benchmarkService,'report').mockImplementation(async id=>report(id))})
afterEach(()=>vi.restoreAllMocks())
async function select(wrapper:ReturnType<typeof mount>){await wrapper.get('[aria-label="基线运行"]').setValue('a');await wrapper.get('[aria-label="候选运行"]').setValue('b');await flushPromises()}
it('shows real deltas, unavailable values and regressions and opens the chosen full report',async()=>{
 const wrapper=mount(BenchmarkComparison,{props:{runs:[run('a'),run('b')]}});await select(wrapper)
 expect(wrapper.get('table').text()).toContain('-0.5');expect(wrapper.get('table').text()).toContain('不可计算')
 expect(wrapper.get('.case-comparison').text()).toContain('退步：recall')
 await wrapper.findAll('button').find(button=>button.text()==='查看候选完整报告')!.trigger('click')
 expect(wrapper.emitted('report')?.[0]).toEqual([run('b')]);wrapper.unmount()
})
it('rejects incompatible datasets and unfinished runs before requesting reports',async()=>{
 const wrapper=mount(BenchmarkComparison,{props:{runs:[run('a'),{...run('b'),datasetHash:'different'}]}});await select(wrapper)
 expect(wrapper.text()).toContain('数据集内容哈希不同');expect(benchmarkService.report).not.toHaveBeenCalled()
 await wrapper.setProps({runs:[run('a'),{...run('b'),status:'failed'}]});await flushPromises()
 expect(wrapper.text()).toContain('只有两次完整运行');expect(benchmarkService.report).not.toHaveBeenCalled();wrapper.unmount()
})
it('discards report responses from an old vault and rejects mismatched report identity',async()=>{
 let finish!:(value:ReturnType<typeof report>)=>void
 vi.mocked(benchmarkService.report).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}))
 const wrapper=mount(BenchmarkComparison,{props:{runs:[run('a'),run('b')]}});await select(wrapper)
 useWorkspaceStore().vaultId='two';finish(report('a'));await flushPromises();expect(wrapper.find('table').exists()).toBe(false)
 vi.mocked(benchmarkService.report).mockResolvedValue(report('other'));await select(wrapper)
 expect(wrapper.get('[role="alert"]').text()).toContain('报告身份或状态');expect(wrapper.find('table').exists()).toBe(false);wrapper.unmount()
})
