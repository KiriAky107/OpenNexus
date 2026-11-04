import { beforeEach, expect, it, vi } from 'vitest'
const host=vi.hoisted(()=>({invoke:vi.fn()}))
vi.mock('@tauri-apps/api/core',()=>({invoke:host.invoke,isTauri:()=>true}))
import { analyzeHandwriting, mobileAi } from './handwritingAi'
beforeEach(()=>{host.invoke.mockReset()})
it('uses only the native narrow AI command and returns a preview',async()=>{
  host.invoke.mockResolvedValue(JSON.stringify({text:'notes',persisted:false}))
  expect(await analyzeHandwriting({action:'recognize',text:'',image:'data:image/png;base64,owned',instruction:''},new AbortController().signal)).toBe('notes')
  const [command,args]=host.invoke.mock.calls[0]!
  expect(command).toBe('mobile_ai');expect(JSON.parse(args.request)).toMatchObject({action:'analyze',input:{action:'recognize'}})
  expect(args.request).not.toContain('api_key')
})
it('cancels the same operation without resending analysis',async()=>{
  const controller=new AbortController();let finish:(value:string)=>void=()=>{}
  host.invoke.mockImplementation((_name,args)=>JSON.parse(args.request).action==='cancel'?Promise.resolve('{}'):new Promise(resolve=>{finish=resolve}))
  const pending=analyzeHandwriting({action:'summarize',text:'source',image:'',instruction:''},controller.signal)
  controller.abort();finish(JSON.stringify({text:'late',persisted:false}));await expect(pending).rejects.toThrow()
  const calls=host.invoke.mock.calls.map(([,args])=>JSON.parse(args.request))
  expect(calls).toHaveLength(2);expect(calls[1]).toEqual({action:'cancel',operation_id:calls[0].operation_id})
})
it('does not issue an already cancelled request or accept persisted output',async()=>{
  const c=new AbortController();c.abort();await expect(analyzeHandwriting({action:'generate',text:'',image:'',instruction:'write'},c.signal)).rejects.toThrow();expect(host.invoke).not.toHaveBeenCalled()
  host.invoke.mockResolvedValue(JSON.stringify({text:'x',persisted:true}));await expect(analyzeHandwriting({action:'generate',text:'',image:'',instruction:'write'},new AbortController().signal)).rejects.toThrow('AI_RESPONSE_INVALID')
})
