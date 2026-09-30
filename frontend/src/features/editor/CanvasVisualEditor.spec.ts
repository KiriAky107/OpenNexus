// @vitest-environment happy-dom
import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,afterEach,expect,it,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useEditorStore} from '@/stores/editor'
import {useWorkspaceStore} from '@/stores/workspace'
import * as service from '@/services/workspaceService'
import {executeEditorCommand} from '@/services/editorCommandService'
import CanvasVisualEditor from './CanvasVisualEditor.vue'
import CanvasEditor from './CanvasEditor.vue'
const fixture=JSON.stringify({nodes:[{id:'a',type:'text',x:0,y:0,width:300,height:180,text:'Root',custom:'keep'},{id:'b',type:'file',x:400,y:0,width:300,height:180,file:'course/note.md',subpath:'#heading'}],edges:[{id:'e',fromNode:'a',toNode:'b',label:'Evidence'}],extension:{keep:true}})
beforeEach(()=>{
 localStorage.clear();setActivePinia(createPinia());const editor=useEditorStore();editor.currentFilePath='/map.canvas';editor.content=fixture;editor.saveStatus='saved';vi.spyOn(editor,'scheduleAutoSave').mockImplementation(()=>{})
 const workspace=useWorkspaceStore();workspace.vaultId='one';workspace.fileTree=[{id:'course',name:'course',path:'/course',type:'folder',children:[{id:'note',name:'note.md',path:'/course/note.md',type:'file',content_hash:'h'}]}]
 vi.spyOn(service,'readFileContent').mockResolvedValue('# Actual note\n\nReal preview.')
})
afterEach(()=>vi.restoreAllMocks())
const button=(wrapper:ReturnType<typeof mount>,text:string)=>wrapper.findAll('button').find(button=>button.text()===text)!
it('edits using the keyboard and property inspector, preserves extensions, copies and restores through shared undo',async()=>{
 const wrapper=mount(CanvasVisualEditor);await flushPromises();expect(wrapper.text()).toContain('Real preview.')
 expect(await executeEditorCommand('editor.undo')).toEqual({ok:false,reason:'unavailable'})
 await button(wrapper,'text · Root').trigger('click');await wrapper.get('.canvas-viewport').trigger('keydown',{key:'ArrowRight'})
 const editor=useEditorStore();expect(JSON.parse(editor.content).nodes[0]).toMatchObject({x:10,custom:'keep'})
 await wrapper.get('[aria-label="节点内容"]').setValue('Edited');await wrapper.get('.node-properties').trigger('submit')
 expect(JSON.parse(editor.content).nodes[0].text).toBe('Edited');expect(JSON.parse(editor.content).extension).toEqual({keep:true})
 await button(wrapper,'复制').trigger('click');await button(wrapper,'粘贴').trigger('click');expect(JSON.parse(editor.content).nodes).toHaveLength(3)
 await executeEditorCommand('editor.undo');expect(JSON.parse(editor.content).nodes).toHaveLength(2)
 await button(wrapper,'整理为思维导图').trigger('click');await button(wrapper,'撤销').trigger('click')
 expect(JSON.parse(editor.content).nodes[0].text).toBe('Edited');wrapper.unmount()
 expect(await executeEditorCommand('editor.undo')).toEqual({ok:false,reason:'unavailable'})
})
it('commits a drag once and blocks edits during revision conflict',async()=>{
 const wrapper=mount(CanvasVisualEditor);await flushPromises();const node=wrapper.get('[data-node-id="a"]'),viewport=wrapper.get('.canvas-viewport')
 await node.trigger('pointerdown',{button:0,clientX:10,clientY:10,pointerId:1});await viewport.trigger('pointermove',{clientX:40,clientY:50,pointerId:1});await viewport.trigger('pointerup',{clientX:40,clientY:50,pointerId:1})
 const editor=useEditorStore();expect(JSON.parse(editor.content).nodes[0].x).toBeGreaterThan(0);expect(editor.scheduleAutoSave).toHaveBeenCalledTimes(1)
 await button(wrapper,'撤销').trigger('click');expect(editor.content).toBe(fixture)
 editor.saveStatus='conflict';await viewport.trigger('keydown',{key:'Delete'});expect(editor.content).toBe(fixture);expect(wrapper.get('[role="alert"]').text()).toContain('冲突');wrapper.unmount()
})
it('never displays a previous vault preview after a delayed read and leaves invalid source unsaved',async()=>{
 let finish!:(value:string)=>void;vi.mocked(service.readFileContent).mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
 const wrapper=mount(CanvasVisualEditor);useWorkspaceStore().vaultId='two';useWorkspaceStore().fileTree=[];await flushPromises();finish('PRIVATE');await flushPromises()
 expect(wrapper.text()).not.toContain('PRIVATE');wrapper.unmount()
 const editor=useEditorStore();editor.content='{ damaged';const source=mount(CanvasEditor)
 expect(source.find('[aria-label="画布 JSON 源码"]').exists()).toBe(true);expect(source.text()).toContain('CANVAS_INVALID')
 const cancel=vi.spyOn(editor,'cancelPendingAutoSave');await source.get('textarea').setValue('{ still damaged');expect(cancel).toHaveBeenCalled();source.unmount()
})

it('renders imported group backgrounds with their repeat, ratio and cover styles',async()=>{
 const editor=useEditorStore();editor.content=JSON.stringify({nodes:['repeat','ratio','cover'].map((backgroundStyle,index)=>({id:backgroundStyle,type:'group',x:index*200,y:0,width:180,height:160,label:backgroundStyle,background:'course/image.png',backgroundStyle})),edges:[]})
 useWorkspaceStore().fileTree=[{id:'image',name:'image.png',path:'/course/image.png',type:'file',content_hash:'image-hash'}]
 vi.spyOn(service,'loadWorkspaceImage').mockResolvedValue(new Blob(['image'],{type:'image/png'}))
 const wrapper=mount(CanvasVisualEditor);await flushPromises()
 expect(wrapper.get('[data-node-id="repeat"] .canvas-group-background').attributes('style')).toContain('background-repeat: repeat')
 expect(wrapper.get('[data-node-id="ratio"] .canvas-group-background').attributes('style')).toContain('background-size: contain')
 expect(wrapper.get('[data-node-id="cover"] .canvas-group-background').attributes('style')).toContain('background-size: cover')
 expect(Number((wrapper.get('.canvas-edges').element as SVGElement).style.zIndex)).toBeGreaterThan(Number((wrapper.get('[data-node-id="cover"]').element as HTMLElement).style.zIndex))
 wrapper.unmount()
})
