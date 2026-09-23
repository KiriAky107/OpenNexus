// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia } from 'pinia'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import SecondarySidebar from './SecondarySidebar.vue'

it('collapses either tab into a rail, preserving panel instance and width', async () => {
  localStorage.clear()
  const pinia = createPinia()
  const layout = useLayoutPreferencesStore(pinia)
  layout.workspaceWidth = 336
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  const wrapper = mount(SecondarySidebar, { attachTo: document.body, props: { component: 'file-tree' }, global: { plugins: [router, pinia], stubs: { FileTreePanel: true } } })
  const panel = wrapper.findComponent({ name: 'FileTreePanel' }).vm
  try {
    layout.workspaceCollapsed = true
    await wrapper.vm.$nextTick()
    expect(wrapper.get('aside').attributes('style')).toContain('40px')
    expect(wrapper.find('[role="separator"]').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'FileTreePanel' }).vm).toBe(panel)
    expect(wrapper.get('.sidebar-content').isVisible()).toBe(false)
    await wrapper.get('[data-panel="outline"]').trigger('click')
    expect(layout.workspaceTab).toBe('outline')
    expect(wrapper.get('aside').attributes('style')).toContain('336px')
    layout.workspaceCollapsed = true
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-panel="files"]').trigger('click')
    expect(layout.workspaceTab).toBe('files')
    expect(layout.workspaceWidth).toBe(336)
  } finally { wrapper.unmount(); localStorage.clear() }
})

it('keeps conversation and file widths separate across route changes', async () => {
  localStorage.setItem('chat-sidebar-width', '320')
  localStorage.setItem('workspace-sidebar-width', '240')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  const wrapper = mount(SecondarySidebar, {props:{component:'conversation-list'}, global:{plugins:[router, createPinia()],stubs:{ConversationListPanel:true,FileTreePanel:true}}})
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
  const options = { props: { component: 'file-tree' }, global: { plugins: [router, createPinia()], stubs: { FileTreePanel: true } } }
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


it('applies remote widths but keeps viewport clamping out of portable preferences', async () => {
  const pinia = createPinia()
  const layout = useLayoutPreferencesStore(pinia)
  layout.workspaceWidth = 480
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  const wrapper = mount(SecondarySidebar, { props: { component: 'file-tree' }, global: { plugins: [router, pinia], stubs: { FileTreePanel: true } } })
  const original = window.innerWidth
  try {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 600 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()
    expect(wrapper.get('aside').attributes('style')).toContain('280px')
    expect(layout.workspaceWidth).toBe(480)
    layout.workspaceWidth = 420
    await wrapper.vm.$nextTick()
    expect(wrapper.get('aside').attributes('style')).toContain('280px')
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
    window.dispatchEvent(new Event('resize'))
    await wrapper.vm.$nextTick()
    expect(wrapper.get('aside').attributes('style')).toContain('420px')
    expect(layout.workspaceWidth).toBe(420)
  } finally {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: original })
    wrapper.unmount()
    localStorage.clear()
  }
})
