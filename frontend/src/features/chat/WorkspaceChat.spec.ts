// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from '@/stores/editor'
import WorkspaceChat from './WorkspaceChat.vue'
import ChatView from './ChatView.vue'
vi.mock('./ChatView.vue', () => ({ default: { props: ['workspaceContext'], template: '<div class="chat-stub">{{ workspaceContext?.content }}</div>' } }))
it('keeps the same floating chat while closing and uses the live unsaved editor contents', async () => {
  localStorage.clear()
  setActivePinia(createPinia())
  const editor = useEditorStore(); editor.currentFilePath = 'draft.md'; editor.content = 'first'
  const wrapper = mount(WorkspaceChat, { props: { open: true }, attachTo: document.body })
  expect(document.querySelector('.chat-stub')?.textContent).toBe('first')
  const chat = wrapper.findComponent(ChatView).vm
  await wrapper.setProps({ open: false })
  editor.content = 'second'
  await wrapper.setProps({ open: true })
  expect(wrapper.findComponent(ChatView).vm).toBe(chat)
  expect(document.querySelector('.chat-stub')?.textContent).toBe('second')
  const before = (document.querySelector('.workspace-chat') as HTMLElement).style.left
  document.querySelector('.workspace-chat-handle')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })); await wrapper.vm.$nextTick()
  expect((document.querySelector('.workspace-chat') as HTMLElement).style.left).not.toBe(before)
  wrapper.unmount()
})

it('remembers resized bounds and resets the window', async () => {
  setActivePinia(createPinia())
  localStorage.setItem('notes-agent.workspace-chat.bounds.v1', JSON.stringify({x:30,y:20,width:420,height:400}))
  const wrapper = mount(WorkspaceChat, {props:{open:true},attachTo:document.body})
  const panel=document.querySelector('.workspace-chat') as HTMLElement
  expect(panel.style.width).toBe('420px')
  document.querySelector('.window-resizer')!.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})); await wrapper.vm.$nextTick()
  expect(panel.style.width).toBe('440px')
  expect(JSON.parse(localStorage.getItem('notes-agent.workspace-chat.bounds.v1')!).width).toBe(440)
  const reset=[...document.querySelectorAll('button')].find(b=>b.textContent==='重置窗口')!
  reset.click(); await wrapper.vm.$nextTick()
  expect(panel.style.width).toBe('640px')
  wrapper.unmount(); localStorage.clear()
})
