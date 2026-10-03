// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import * as service from '@/services/workspaceService'
import FolderContents from './FolderContents.vue'
import { folderNoteSummary } from './folderSummary'
vi.mock('@/components/common/MarkdownContent.vue',()=>({default:{props:['source','sourcePath'],template:'<div class="intro-body">{{source}}</div>'}}))
beforeEach(()=>{localStorage.clear();setActivePinia(createPinia());vi.restoreAllMocks()})
function setup(count=2){
  const workspace=useWorkspaceStore();workspace.vaultId='one';workspace.selectFolder('/course')
  workspace.fileTree=[{id:'folder',name:'course',path:'/course',type:'folder',children:[{id:'sub',name:'sub',path:'/course/sub',type:'folder',children:[]},...Array.from({length:count},(_,i)=>({id:`n${i}`,name:i===0?'index.md':`n${i}.md`,path:i===0?'/course/index.md':`/course/n${i}.md`,type:'file' as const,content_hash:`h${i}`,updated_at:'2026-09-30T00:00:00Z'}))]}]
  const read=vi.spyOn(service,'readFileContent').mockImplementation(async path=>path.endsWith('index.md')?'# 课程导言\n\n实际介绍。':`---\ntitle: 实际标题\n---\n# 正文标题\n\n实际摘要 ${path}`)
  return{workspace,read}
}
it('shows actual titles, summaries, introduction and opens the correct note or subfolder',async()=>{
  const {workspace}=setup();const editor=useEditorStore();const load=vi.spyOn(editor,'loadFile')
  vi.spyOn(service,'getNoteId').mockResolvedValue('n1')
  const wrapper=mount(FolderContents,{props:{path:'/course'}});await flushPromises()
  expect(wrapper.get('.intro-body').text()).toContain('实际介绍')
  expect(wrapper.findAll('.folder-item').map(item=>item.text()).join()).toContain('实际标题')
  const target=wrapper.findAll('.folder-item').find(item=>item.text().includes('实际标题'))!
  await target.trigger('click');await flushPromises();expect(load).toHaveBeenCalledWith('/course/n1.md');expect(workspace.activeFilePath).toBe('/course/n1.md')
  await wrapper.findAll('.folder-item')[0]!.trigger('click');expect(workspace.activeFolderPath).toBe('/course/sub')
  wrapper.unmount()
})
it('loads only visible notes and keeps list preference, filtering and external refresh consistent',async()=>{
  const {workspace,read}=setup(100);const wrapper=mount(FolderContents,{props:{path:'/course'}});await flushPromises()
  expect(read).toHaveBeenCalledTimes(39)
  await wrapper.findAll('button').find(button=>button.text()==='列表')!.trigger('click')
  expect(wrapper.get('.folder-items').classes()).toContain('list');expect(localStorage.getItem('folder-view')).toBe('list')
  await wrapper.get('input').setValue('n99');await flushPromises()
  expect(wrapper.findAll('.folder-item')).toHaveLength(1)
  const changed=workspace.fileTree[0]!.children!.find(item=>item.name==='n99.md')!;changed.content_hash='updated'
  await flushPromises();expect(read).toHaveBeenLastCalledWith('/course/n99.md')
  wrapper.unmount()
})
it('ignores late note content after switching vault',async()=>{
  const{workspace,read}=setup();let finish!:(value:string)=>void
  read.mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
  const wrapper=mount(FolderContents,{props:{path:'/course'}})
  workspace.vaultId='two';workspace.fileTree=[]
  finish('# PRIVATE');await flushPromises()
  expect(wrapper.text()).not.toContain('PRIVATE');wrapper.unmount()
})
it('truncates plain summaries safely and retains real metadata titles',()=>{
  const result=folderNoteSummary('/a.md','---\ntitle: 真实标题\n---\n# Heading\n\n<script>alert(1)</script> '+ '🌳'.repeat(220))
  expect(result.title).toBe('真实标题');expect(result.summary).not.toContain('<script>')
  expect(result.summary.endsWith('…')).toBe(true);expect(result.summary).not.toContain('\ud83c…')
})
