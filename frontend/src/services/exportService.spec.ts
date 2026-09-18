// @vitest-environment jsdom
import {webcrypto} from 'node:crypto'
import {describe,it,expect,vi,afterEach} from 'vitest'
const native=vi.hoisted(()=>({isDesktop:vi.fn(()=>false),hostInvoke:vi.fn()}))
vi.mock('./apiClient',()=>({apiClient:{post:vi.fn(),get:vi.fn()}}))
vi.mock('./platform/desktop',()=>native)
vi.mock('./mermaidService',()=>({renderMermaid:vi.fn()}))
vi.mock('./pdfSnapshotService',()=>({preparePdfSnapshot:vi.fn().mockResolvedValue('<html>theme snapshot</html>')}))
import {preparePdfSnapshot} from './pdfSnapshotService'
import {renderMermaid} from './mermaidService'
import {apiClient} from './apiClient'
import {exportService,captureExportPalette} from './exportService'
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

const reviewOptions={theme_id:'light',include_title:true,page_size:'A4'}
const queued={job_id:'review-job',status:'queued',warnings:[],file:null,error:null}
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.clearAllMocks()})
it('uses the native save dialog for desktop export downloads',async()=>{
  native.isDesktop.mockReturnValue(true)
  native.hostInvoke.mockResolvedValue('D:/Exports/note.pdf')
  await exportService.download({id:'export_0123456789ab',status:'completed',warnings:[],error:null,fileName:'note.pdf'})
  expect(native.hostInvoke).toHaveBeenCalledWith('export_save',{jobId:'export_0123456789ab',fileName:'note.pdf'})
  expect(apiClient.get).not.toHaveBeenCalled()
  native.isDesktop.mockReturnValue(false)
})
it('cancels a created server job after an in-flight submission is aborted',async()=>{
  let finish!:(value:unknown)=>void
  vi.mocked(apiClient.post).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never).mockResolvedValue({status:'completed'})
  vi.mocked(apiClient.get).mockResolvedValue({...queued,status:'cancelled'})
  const controller=new AbortController()
  const pending=exportService.create('# snapshot','review','html',reviewOptions,controller.signal)
  controller.abort();finish(queued)
  await expect(pending).rejects.toMatchObject({name:'AbortError'})
  expect(apiClient.post).toHaveBeenLastCalledWith('/api/exports/review-job/cancel')
})
it('does not report cancellation when the server job already completed',async()=>{
  let finish!:(value:unknown)=>void
  vi.mocked(apiClient.post).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never).mockResolvedValue({status:'completed'})
  vi.mocked(apiClient.get).mockResolvedValue({...queued,status:'completed'})
  const controller=new AbortController()
  const pending=exportService.create('# snapshot','review','html',reviewOptions,controller.signal)
  controller.abort();finish(queued)
  await expect(pending).rejects.toThrow('导出已完成，无法取消')
})
it('surfaces a server cancellation failure instead of claiming it was cancelled',async()=>{
  let finish!:(value:unknown)=>void
  vi.mocked(apiClient.post).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never).mockRejectedValue(new Error('network failure'))
  const controller=new AbortController()
  const pending=exportService.create('# snapshot','review','html',reviewOptions,controller.signal)
  controller.abort();finish(queued)
  await expect(pending).rejects.toThrow('network failure')
})
it.each(['mermaid','Mermaid','mermaid title="Flow"'])('prepares a static asset for %s',async language=>{
  vi.stubGlobal('crypto',webcrypto)
  vi.stubGlobal('Image',class {src='';decode(){return Promise.resolve()}})
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({fillStyle:'',fillRect:vi.fn(),drawImage:vi.fn()} as never)
  vi.spyOn(HTMLCanvasElement.prototype,'toDataURL').mockReturnValue('data:image/png;base64,YWJj')
  vi.mocked(renderMermaid).mockResolvedValue({svg:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"></svg>',warnings:[]} as never)
  vi.mocked(apiClient.post).mockResolvedValue(queued)
  await exportService.create('```'+language+'\nflowchart LR\n A-->B\n```','review','html',reviewOptions)
  expect(renderMermaid).toHaveBeenCalledWith('flowchart LR\n A-->B',{mode:'raster',theme:'light'})
  expect(apiClient.post).toHaveBeenCalledWith('/api/exports',expect.objectContaining({assets:[expect.objectContaining({kind:'mermaid',png_base64:'YWJj',source_hash:expect.stringMatching(/^[a-f0-9]{64}$/)})]}))
})

it('PDF submits the shared browser snapshot instead of raster assets',async()=>{
  vi.mocked(apiClient.post).mockResolvedValue(queued)
  const markdown=Array.from({length:17},(_,i)=>'```mermaid\nflowchart LR\n A'+i+'-->B\n```').join('\n\n')
  await exportService.create(markdown,'many','pdf',{...reviewOptions,theme_id:'dark'})
  expect(preparePdfSnapshot).toHaveBeenCalledWith(markdown,'many',expect.objectContaining({theme_id:'dark'}),undefined,undefined)
  expect(renderMermaid).not.toHaveBeenCalled()
  expect(apiClient.post).toHaveBeenCalledWith('/api/exports',expect.objectContaining({assets:[],print_html:'<html>theme snapshot</html>'}))
})
it('captures custom theme CSS as a portable palette',()=>{
  const values={'background-primary':'#010409','surface-primary':'rgb(22, 27, 34)','text-primary':'#e6edf3','text-secondary':'#b1bac4','background-secondary':'#21262d','border-default':'#57606a','accent-primary':'#79c0ff'}
  for(const [key,value] of Object.entries(values))document.documentElement.style.setProperty('--color-'+key,value)
  try { expect(captureExportPalette()).toMatchObject({surface:'#161b22',text:'#e6edf3',accent:'#79c0ff'}) }
  finally { for(const key of Object.keys(values))document.documentElement.style.removeProperty('--color-'+key) }
})
