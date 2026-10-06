// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
const controls=vi.hoisted(()=>({get:vi.fn(),post:vi.fn(),workspace:{vaultId:'vault-one'}}))
vi.mock('@/services/apiClient',()=>({default:{get:controls.get,post:controls.post}}))
vi.mock('@/stores/workspace',async()=>{const {reactive}=await import('vue');controls.workspace=reactive(controls.workspace);return {useWorkspaceStore:()=>controls.workspace}})
import ConfigurationCandidateDialog from './ConfigurationCandidateDialog.vue'
const slot='a'.repeat(64),fingerprint='b'.repeat(64),afterHash='c'.repeat(64)
const review={review_id:'11111111-1111-4111-a111-111111111111',slot,kind:'mcp',fingerprint,target:'mcp:new123',before:null,after:{version:1,name:'<img src=x onerror=alert(1)>',command:'python',args:['server.py'],enabled:false,approved_digest:null,tested_digest:null},after_sha256:afterHash,package_name:'Example',package_version:'1.0.0'}
function component(kind:'mcp'|'model'='mcp'){return mount(ConfigurationCandidateDialog,{props:{slot,kind,sourceRevision:'fixed'},global:{stubs:{AppDialog:{template:'<section><slot /></section>'}}}})}
function button(wrapper:ReturnType<typeof component>,label:string){return wrapper.findAll('button').find(button=>button.text()===label)!}
async function previewed(){const wrapper=component();await flushPromises();await wrapper.get('select').setValue('mcp:new');await button(wrapper,'预览配置差异').trigger('click');await flushPromises();return wrapper}
beforeEach(()=>{
  controls.get.mockReset();controls.post.mockReset();controls.workspace.vaultId='vault-one'
  controls.get.mockResolvedValue({kind:'mcp',items:[{id:'mcp:new',label:'New MCP'}]})
  controls.post.mockImplementation(async(path,body)=>path.endsWith('/preview')?review:{operation_id:body.operation_id,fingerprint,state:'applied',target:review.target,after_sha256:afterHash})
})
it('loads actual targets, then requires explicit selection, preview and independent confirmation',async()=>{
  const wrapper=component();await flushPromises();expect(controls.post).not.toHaveBeenCalled()
  expect(button(wrapper,'预览配置差异').attributes('disabled')).toBeDefined()
  await wrapper.get('select').setValue('mcp:new');await button(wrapper,'预览配置差异').trigger('click');await flushPromises()
  expect(controls.post).toHaveBeenCalledTimes(1);expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.text()).toContain('保留已有加密密钥');wrapper.unmount()
})
it('sends only a sealed review identity and exact operation after confirmation',async()=>{
  const wrapper=await previewed();await button(wrapper,'确认应用配置').trigger('click');await flushPromises()
  expect(controls.post).toHaveBeenLastCalledWith('/api/community/configurations/apply',{review_id:review.review_id,fingerprint,operation_id:expect.any(String)},expect.objectContaining({signal:expect.any(AbortSignal)}))
  expect(wrapper.emitted('applied')).toHaveLength(1);wrapper.unmount()
})
it('recovers a lost reply only from the exact operation, fingerprint, target and committed hash',async()=>{
  let operation=''
  controls.post.mockImplementation(async(path,body)=>{if(path.endsWith('/preview'))return review;operation=body.operation_id;throw new Error('lost reply')})
  controls.get.mockImplementation(async path=>path.includes('/operations/')?{operation_id:operation,fingerprint,state:'applied',target:review.target,after_sha256:afterHash}:{kind:'mcp',items:[{id:'mcp:new',label:'New'}]})
  const wrapper=await previewed();await button(wrapper,'确认应用配置').trigger('click');await flushPromises()
  expect(controls.get).toHaveBeenLastCalledWith(`/api/community/configurations/operations/${operation}`,{params:{fingerprint}})
  expect(wrapper.emitted('applied')).toHaveLength(1);expect(controls.post).toHaveBeenCalledTimes(2);wrapper.unmount()
})
it('refuses unrelated receipts and clears the review after an unconfirmed write',async()=>{
  controls.post.mockImplementation(async path=>{if(path.endsWith('/preview'))return review;throw new Error('lost reply')})
  controls.get.mockImplementation(async path=>path.includes('/operations/')?{operation_id:'other',fingerprint,state:'applied',target:review.target,after_sha256:afterHash}:{kind:'mcp',items:[{id:'mcp:new',label:'New'}]})
  const wrapper=await previewed();await button(wrapper,'确认应用配置').trigger('click');await flushPromises()
  expect(wrapper.emitted('applied')).toBeUndefined();expect(button(wrapper,'确认应用配置')).toBeUndefined();expect(controls.post).toHaveBeenCalledTimes(2);wrapper.unmount()
})
it('cancels an in-flight request and checks its result without repeating the write',async()=>{
  controls.post.mockImplementation((path,_body,options)=>path.endsWith('/preview')?Promise.resolve(review):new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('REQUEST_CANCELLED')))))
  controls.get.mockImplementation(async path=>path.includes('/operations/')?null:{kind:'mcp',items:[{id:'mcp:new',label:'New'}]})
  const wrapper=await previewed();await button(wrapper,'确认应用配置').trigger('click');await flushPromises()
  await button(wrapper,'取消并核对结果').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('应用结果未确认');expect(wrapper.emitted('applied')).toBeUndefined();expect(controls.post).toHaveBeenCalledTimes(2);wrapper.unmount()
})
it('ignores late target responses after vault changes and aborts their request',async()=>{
  let resolve!:(value:unknown)=>void,signal:AbortSignal|undefined
  controls.get.mockImplementationOnce((_path,options)=>{signal=options.signal;return new Promise(done=>{resolve=done})})
  const wrapper=component();await flushPromises();controls.workspace.vaultId='vault-two';await flushPromises()
  resolve({kind:'mcp',items:[{id:'mcp:old',label:'Old target'}]});await flushPromises()
  expect(signal?.aborted).toBe(true);expect(wrapper.text()).not.toContain('Old target');expect(wrapper.get('select').element.value).toBe('');wrapper.unmount()
})
it('invalidates the reviewed proposal after source changes and requires target selection again',async()=>{
  const wrapper=await previewed();await wrapper.setProps({sourceRevision:'new-key'});await flushPromises()
  expect(wrapper.get('select').element.value).toBe('');expect(button(wrapper,'确认应用配置')).toBeUndefined();wrapper.unmount()
})
it.each([{after:{...review.after,enabled:true}},{after:{...review.after,version:9}},{target:'model:local_runtime'}])('rejects inconsistent preview fields before showing confirmation (%j)',async change=>{
  controls.post.mockResolvedValue({...review,...change});const wrapper=await previewed()
  expect(button(wrapper,'确认应用配置')).toBeUndefined();expect(wrapper.emitted('applied')).toBeUndefined();wrapper.unmount()
})
it('previews a real model runtime target and does not download weights or start inference',async()=>{
  const modelReview={...review,kind:'model',target:'model:local_runtime',before:{version:1,embedding_model:'bekko'},after:{version:2,embedding_model:'granite',cpu_threads:4},model_plan:{source:'ibm-granite/granite-embedding-97m-multilingual-r2',revision:'fixed',license:'Apache-2.0',model_key:'granite'}}
  controls.get.mockResolvedValue({kind:'model',items:[{id:'model:local_runtime',label:'Local runtime'}]});controls.post.mockResolvedValue(modelReview)
  const wrapper=component('model');await flushPromises();await wrapper.get('select').setValue('model:local_runtime');await button(wrapper,'预览配置差异').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('granite');expect(wrapper.text()).toContain('重建索引');expect(controls.post).toHaveBeenCalledTimes(1);expect(wrapper.emitted('applied')).toBeUndefined();wrapper.unmount()
})
