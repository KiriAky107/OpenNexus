// @vitest-environment jsdom
import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi} from 'vitest'
import ExportDialog from './ExportDialog.vue'
import {apiClient} from '@/services/apiClient'
vi.mock('@/stores/editor',()=>({useEditorStore:()=>({content:'# snapshot',currentFilePath:'note.md'})}))
vi.mock('@/stores/theme',()=>({useThemeStore:()=>({currentThemeId:'light'})}))
vi.mock('@/services/mermaidService',()=>({renderMermaid:vi.fn()}))
vi.mock('@/services/apiClient',()=>({apiClient:{post:vi.fn(),get:vi.fn()}}))
it('closing the dialog after submitting preserves the background export',async()=>{
 let finish!:(value:unknown)=>void
 vi.mocked(apiClient.get).mockImplementation(async(path:string)=>path==='/api/exports'?{items:[]}:{job_id:'closing-job',status:'cancelled',warnings:[],error:null,file:null})
 vi.mocked(apiClient.post).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never).mockResolvedValue({status:'completed'})
 const wrapper=mount(ExportDialog,{global:{stubs:{AppDialog:{template:'<div><slot /></div>'}}}})
 await flushPromises()
 await wrapper.findAll('button').find(b=>b.text()==='开始导出')!.trigger('click')
 expect(apiClient.post).toHaveBeenCalledWith('/api/exports',expect.anything())
 wrapper.unmount()
 finish({job_id:'closing-job',status:'queued',warnings:[],error:null,file:null})
 await flushPromises()
 expect(apiClient.post).not.toHaveBeenCalledWith('/api/exports/closing-job/cancel')
})
