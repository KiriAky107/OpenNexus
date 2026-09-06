// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import Commands from './WorkspacePluginCommands.vue'
import { useEditorStore } from '@/stores/editor'
import { listPluginCommands, executePluginCommand } from '@/services/pluginService'
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/services/pluginService', () => ({ listPluginCommands: vi.fn(), executePluginCommand: vi.fn() }))
const command = { command_id:'inspect',plugin_id:'p', title:'Inspect', enabled:true, when:['editor.has_selection'], parameters:{type:'object',properties:{}}, locations:['context_menu','toolbar'] }
afterEach(() => { document.body.replaceChildren(); vi.clearAllMocks() })
it('captures source selection for context commands and sends the snapshot', async () => {
  vi.mocked(listPluginCommands).mockResolvedValue([command as any])
  vi.mocked(executePluginCommand).mockResolvedValue({ effect:{type:'notification',payload:{message:'done'}} } as any)
  const pinia = createPinia(); const editor = useEditorStore(pinia); editor.currentFilePath = '/note.md'
  const wrapper = mount(Commands,{attachTo:document.body,global:{plugins:[pinia],stubs:{AppDialog:{template:'<div><slot/></div>'}}},slots:{default:'<textarea class="source">abcdef</textarea>'}})
  const input = wrapper.get('textarea').element as HTMLTextAreaElement
  input.focus(); input.setSelectionRange(1,4)
  await wrapper.get('textarea').trigger('contextmenu'); await flushPromises()
  expect(listPluginCommands).toHaveBeenCalledWith('context_menu')
  await wrapper.findAll('button').find(button=>button.text()==='Inspect')!.trigger('click')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(executePluginCommand).toHaveBeenCalledWith('inspect',{},expect.objectContaining({selection:'bcd',file_path:'/note.md'}))
  wrapper.unmount()
})
it('filters disabled commands and invalidates an open form when changing file', async () => {
  vi.mocked(listPluginCommands).mockResolvedValue([{...command,when:[],enabled:false} as any])
  const pinia=createPinia(); const editor=useEditorStore(pinia); editor.currentFilePath='/one.md'
  const wrapper=mount(Commands,{global:{plugins:[pinia],stubs:{AppDialog:{template:'<div><slot/></div>'}}}})
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(listPluginCommands).toHaveBeenCalledWith('toolbar')
  expect(wrapper.text()).not.toContain('Inspect')
  editor.currentFilePath='/two.md'; await flushPromises()
  expect(wrapper.find('section.modal').exists()).toBe(false)
  wrapper.unmount()
})
