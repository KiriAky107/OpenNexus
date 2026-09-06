// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import SecondarySidebar from './SecondarySidebar.vue'

it('keeps conversation and file widths separate across route changes', async () => {
  localStorage.setItem('chat-sidebar-width', '320')
  localStorage.setItem('workspace-sidebar-width', '240')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  const wrapper = mount(SecondarySidebar, {props:{component:'conversation-list'}, global:{plugins:[router],stubs:{ConversationListPanel:true,FileTreePanel:true}}})
  await wrapper.vm.$nextTick()
  expect(wrapper.get('aside').attributes('style')).toContain('320px')
  await wrapper.get('[role="separator"]').trigger('keydown', {key:'ArrowRight'})
  expect(localStorage.getItem('chat-sidebar-width')).toBe('336')
  await wrapper.setProps({component:'file-tree'})
  expect(wrapper.get('aside').attributes('style')).toContain('240px')
  await wrapper.setProps({component:'conversation-list'})
  expect(wrapper.get('aside').attributes('style')).toContain('336px')
  wrapper.unmount()
  localStorage.removeItem('chat-sidebar-width')
  localStorage.removeItem('workspace-sidebar-width')
})

it('resizes by keyboard, clamps bounds and restores the saved width', async () => {
  localStorage.removeItem('workspace-sidebar-width')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  const options = { props: { component: 'file-tree' }, global: { plugins: [router], stubs: { FileTreePanel: true } } }
  let wrapper = mount(SecondarySidebar, options)
  await wrapper.get('[role="separator"]').trigger('keydown', { key: 'ArrowRight' })
  expect(localStorage.getItem('workspace-sidebar-width')).toBe('288')
  wrapper.unmount()
  wrapper = mount(SecondarySidebar, options)
  await wrapper.vm.$nextTick()
  expect(wrapper.get('aside').attributes('style')).toContain('288px')
  await wrapper.get('[role="separator"]').trigger('keydown', { key: 'Home' })
  expect(wrapper.get('aside').attributes('style')).toContain('200px')
  wrapper.unmount()
  localStorage.removeItem('workspace-sidebar-width')
})
