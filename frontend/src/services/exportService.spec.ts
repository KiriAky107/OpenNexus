import {describe,it,expect,vi} from 'vitest'
vi.mock('./apiClient',()=>({apiClient:{post:vi.fn(),get:vi.fn()}}))
import {apiClient} from './apiClient'
import {exportService} from './exportService'
describe('export snapshot contract',()=>{
  it('submits the unsaved Markdown snapshot and maps warnings and filename',async()=>{
    vi.mocked(apiClient.post).mockResolvedValue({job_id:'job',status:'queued',warnings:['print palette'],file:{file_name:'note.pdf'},error:null})
    const job=await exportService.create('# unsaved', 'note','pdf',{theme_id:'dark',include_title:true,page_size:'A4'},undefined,'folder/note.md')
    expect(apiClient.post).toHaveBeenCalledWith('/api/exports',expect.objectContaining({source:{type:'markdown',markdown:'# unsaved',file_path:'folder/note.md'},format:'pdf'}))
    expect(job.warnings).toEqual(['print palette']);expect(job.fileName).toBe('note.pdf')
  })
  it('aborted preparation does not create a backend job',async()=>{
    vi.mocked(apiClient.post).mockClear()
    const abort=new AbortController();abort.abort()
    await expect(exportService.create('snapshot','note','html',{theme_id:'light',include_title:true,page_size:'A4'},abort.signal)).rejects.toThrow()
    expect(apiClient.post).not.toHaveBeenCalled()
  })
})
