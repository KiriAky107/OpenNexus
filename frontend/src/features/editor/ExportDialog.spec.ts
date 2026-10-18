// @vitest-environment jsdom
import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi,beforeEach} from 'vitest'
import ExportDialog from './ExportDialog.vue'
import {apiClient} from '@/services/apiClient'
vi.mock('@/stores/editor',()=>({useEditorStore:()=>({content:'# snapshot',currentFilePath:'note.md'})}))
vi.mock('@/stores/theme',()=>({useThemeStore:()=>({currentThemeId:'light'})}))
vi.mock('@/services/mermaidService',()=>({renderMermaid:vi.fn()}))
vi.mock('@/services/pdfSnapshotService',()=>({prepareExportSnapshot:vi.fn().mockResolvedValue('<html>snapshot</html>')}))
vi.mock('@/services/apiClient',()=>({apiClient:{post:vi.fn(),get:vi.fn()}}))
beforeEach(()=>{vi.useRealTimers();vi.clearAllMocks()})
it('starts each dialog with no completed history while retaining active background jobs',async()=>{
 vi.useFakeTimers()
 vi.mocked(apiClient.get)
  .mockResolvedValueOnce({items:[
   {job_id:'old',status:'completed',warnings:[],error:null,file:{file_name:'old.pdf',size:1}},
   {job_id:'active',status:'running',warnings:[],error:null,file:null},
  ]})
  .mockResolvedValueOnce({items:[
   {job_id:'old',status:'completed',warnings:[],error:null,file:{file_name:'old.pdf',size:1}},
   {job_id:'active',status:'completed',warnings:[],error:null,file:{file_name:'active.pdf',size:1}},
  ]})
 const wrapper=mount(ExportDialog,{global:{stubs:{AppDialog:{template:'<div><slot /></div>'}}}})
 await flushPromises()
 expect(wrapper.text()).not.toContain('old.pdf')
 expect(wrapper.text()).toContain('active')
 await vi.advanceTimersByTimeAsync(1500)
 await flushPromises()
 expect(wrapper.text()).toContain('active.pdf')
 wrapper.unmount()
})
it('closing the dialog after submitting preserves the background export',async()=>{
 let finish!:(value:unknown)=>void
 vi.mocked(apiClient.get).mockImplementation(async(path:string)=>path==='/api/exports'?{items:[]}:{job_id:'closing-job',status:'cancelled',warnings:[],error:null,file:null})
 vi.mocked(apiClient.post).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never).mockResolvedValue({status:'completed'})
 const wrapper=mount(ExportDialog,{global:{stubs:{AppDialog:{template:'<div><slot /></div>'}}}})
 await flushPromises()
 await wrapper.findAll('button').find(b=>b.text()==='开始导出')!.trigger('click')
 await flushPromises()
 expect(apiClient.post).toHaveBeenCalledWith('/api/exports',expect.anything())
 wrapper.unmount()
 finish({job_id:'closing-job',status:'queued',warnings:[],error:null,file:null})
 await flushPromises()
 expect(apiClient.post).not.toHaveBeenCalledWith('/api/exports/closing-job/cancel')
})
it('retains a newly acknowledged job when the opening list arrives with an older empty snapshot',async()=>{
 vi.useFakeTimers()
 let finish!:(value:unknown)=>void
 vi.mocked(apiClient.get).mockReset().mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never)
 vi.mocked(apiClient.post).mockReset().mockResolvedValueOnce({job_id:'new-job',status:'queued',warnings:[],error:null,file:null})
 const wrapper=mount(ExportDialog,{global:{stubs:{AppDialog:{template:'<div><slot /></div>'}}}})
 try {
  await flushPromises()
  await wrapper.findAll('button').find(b=>b.text()==='开始导出')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('new-job')
  finish({items:[]});await flushPromises()
  expect(wrapper.text()).toContain('new-job')
  expect(apiClient.post).toHaveBeenCalledTimes(1)
 } finally { wrapper.unmount() }
})
it('keeps a new job actionable when a prior periodic list returns after its submission',async()=>{
 vi.useFakeTimers()
 let finish!:(value:unknown)=>void
 vi.mocked(apiClient.get).mockReset().mockResolvedValueOnce({items:[]})
  .mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never)
 vi.mocked(apiClient.post).mockReset().mockResolvedValueOnce({job_id:'periodic-job',status:'queued',warnings:[],error:null,file:null})
  .mockResolvedValue({status:'cancelled'})
 const wrapper=mount(ExportDialog,{global:{stubs:{AppDialog:{template:'<div><slot /></div>'}}}})
 try {
  await flushPromises();await vi.advanceTimersByTimeAsync(1500)
  await wrapper.findAll('button').find(b=>b.text()==='开始导出')!.trigger('click')
  await flushPromises()
  finish({items:[]});await flushPromises()
  expect(wrapper.text()).toContain('periodic-job')
  await wrapper.get('li button').trigger('click');await flushPromises()
  expect(apiClient.post).toHaveBeenLastCalledWith('/api/exports/periodic-job/cancel')
  expect(apiClient.post).toHaveBeenCalledTimes(2)
 } finally { wrapper.unmount() }
})
it('uses the returned job status once without duplicating a job created during the list request',async()=>{
 vi.useFakeTimers()
 let finish!:(value:unknown)=>void
 vi.mocked(apiClient.get).mockReset().mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never)
 vi.mocked(apiClient.post).mockReset().mockResolvedValueOnce({job_id:'same-job',status:'queued',warnings:[],error:null,file:null})
 const wrapper=mount(ExportDialog,{global:{stubs:{AppDialog:{template:'<div><slot /></div>'}}}})
 try {
  await flushPromises()
  await wrapper.findAll('button').find(b=>b.text()==='开始导出')!.trigger('click');await flushPromises()
  finish({items:[{job_id:'same-job',status:'completed',warnings:[],error:null,file:{file_name:'latest.pdf',size:4}}]})
  await flushPromises()
  expect(wrapper.findAll('li')).toHaveLength(1)
  expect(wrapper.text()).toContain('latest.pdf')
  expect(wrapper.text()).toContain('已完成')
  expect(wrapper.get('li button').text()).toBe('下载')
 } finally { wrapper.unmount() }
})
