// @vitest-environment happy-dom
import {mount,flushPromises} from '@vue/test-utils'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import BenchmarkView from './BenchmarkView.vue'
const service=vi.hoisted(()=>({datasets:vi.fn(),list:vi.fn(),start:vi.fn(),cancel:vi.fn(),report:vi.fn()}))
vi.mock('@/services/benchmarkService',()=>({benchmarkService:service}))
vi.mock('@/services/providerService',()=>({listProviders:vi.fn().mockResolvedValue([])}))
beforeEach(()=>{service.datasets.mockResolvedValue([{id:'rag-demo',cases:2}]);service.list.mockResolvedValue([])})
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
