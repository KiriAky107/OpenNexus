// @vitest-environment happy-dom
import {mount,flushPromises} from '@vue/test-utils'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useWorkspaceStore} from '@/stores/workspace'
import BenchmarkView from './BenchmarkView.vue'
const service=vi.hoisted(()=>({datasets:vi.fn(),list:vi.fn(),start:vi.fn(),cancel:vi.fn(),report:vi.fn(),importDataset:vi.fn(),exportDataset:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>({query:{}})}))
vi.mock('@/services/benchmarkService',()=>({benchmarkService:service}))
vi.mock('@/services/providerService',()=>({listProviders:vi.fn().mockResolvedValue([])}))
beforeEach(()=>{setActivePinia(createPinia());localStorage.clear();const workspace=useWorkspaceStore();workspace.vaultId='vault-a';workspace.vaultName='课程库';workspace.hasVault=true;service.datasets.mockResolvedValue([{id:'rag-demo',cases:2,scope:'vault',version:'1'}]);service.list.mockResolvedValue([])})
afterEach(()=>vi.clearAllMocks())
it('shows a useful empty state and submits the selected retrieval configuration',async()=>{
 const wrapper=mount(BenchmarkView,{global:{stubs:{RouterLink:true}}});await flushPromises()
 expect(wrapper.text()).toContain('还没有评测记录')
 await wrapper.get('#benchmark-fusion').setValue('weighted')
 expect(wrapper.get('#benchmark-rrfk').attributes('disabled')).toBeDefined()
 await wrapper.get('form').trigger('submit');await flushPromises()
 expect(service.start).toHaveBeenCalledWith('rag',expect.objectContaining({dataset_id:'rag-demo',retrieval:expect.objectContaining({fusion:'weighted'})}))
 wrapper.unmount()
})
it('imports a dataset into the current vault and selects the returned ID',async()=>{
 const wrapper=mount(BenchmarkView,{global:{stubs:{RouterLink:true}}});await flushPromises()
 service.importDataset.mockResolvedValue({dataset_id:'course-rag',kind:'rag'})
 service.datasets.mockResolvedValue([{id:'course-rag',cases:1,scope:'vault'}])
 await wrapper.get('textarea').setValue('{"dataset_id":"course-rag"}')
 await wrapper.findAll('button').find(b=>b.text()==='导入到当前知识库')!.trigger('click');await flushPromises()
 expect(service.importDataset).toHaveBeenCalledWith('{"dataset_id":"course-rag"}',undefined)
 expect((wrapper.get('#benchmark-dataset').element as HTMLSelectElement).value).toBe('course-rag')
 expect(wrapper.text()).toContain('已保存到当前知识库')
 wrapper.unmount()
})
it('discards a late dataset response from the previous vault',async()=>{
 let resolveOld!:(value:unknown)=>void
 service.datasets.mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve}))
 const wrapper=mount(BenchmarkView,{global:{stubs:{RouterLink:true}}});await flushPromises()
 service.datasets.mockResolvedValue([{id:'project-rag',cases:1,scope:'vault'}])
 useWorkspaceStore().vaultId='vault-b';await flushPromises()
 resolveOld([{id:'course-rag',cases:1}]);await flushPromises()
 expect((wrapper.get('#benchmark-dataset').element as HTMLSelectElement).value).toBe('project-rag')
 expect(wrapper.text()).not.toContain('course-rag')
 wrapper.unmount()
})
it('renders report percentages, unavailable metrics and localized terminal states',async()=>{
 service.list.mockResolvedValue([{id:'r',datasetId:'agent-demo',status:'completed',progress:1,errorCode:null}])
 service.report.mockResolvedValue({metrics:{task_success_rate:.75,tool_argument_accuracy:null,token_usage:120},cases:[],config_snapshot:{}})
 const wrapper=mount(BenchmarkView,{global:{stubs:{RouterLink:true}}});await flushPromises()
 expect(wrapper.text()).toContain('已完成')
 await wrapper.findAll('button').find(button=>button.text()==='查看报告')!.trigger('click');await flushPromises()
 expect(wrapper.get('.benchmark-report').text()).toContain('75%')
 expect(wrapper.get('.benchmark-report').text()).toContain('不适用')
 expect(wrapper.get('.benchmark-report').text()).toContain('agent-demo')
 wrapper.unmount()
})
