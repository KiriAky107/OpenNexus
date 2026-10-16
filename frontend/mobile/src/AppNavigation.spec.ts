// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { installTestDialogApi } from './dialogTestSupport'
import App from './App.vue'

installTestDialogApi()

const host = vi.hoisted(() => ({ mobileCapabilities: vi.fn(), mobileVault: vi.fn(), mobileRequest: vi.fn(), mobileOnBack: vi.fn(), mobileBackground: vi.fn() }))
vi.mock('./native', () => host)
const unregister = vi.fn()
let back: () => void
let persist: ((request: { generation: number; content: string }) => Promise<unknown>) | undefined
let format = 'markdown'
const draft = () => ({ id: 'owned', file_id: null, path: format === 'canvas' ? 'owned.canvas' : 'owned.md', base_hash: '', generation: 1, saved_generation: 0, content: format === 'canvas' ? '{"nodes":[],"edges":[]}' : '', edit_session: 'lease' })
const button = (wrapper: ReturnType<typeof mount>, label: string) => { const found = wrapper.findAll('button').find(item => item.text() === label); if (!found) throw Error(`Missing ${label}`); return found }

beforeEach(() => {
  localStorage.clear(); localStorage.setItem('opennexus.mobile.language', 'en')
  vi.clearAllMocks(); format = 'markdown'; persist = undefined
  host.mobileCapabilities.mockResolvedValue({ protocol: 1, platform: 'android', workspace: true, notes: true, navigation: true, version: '0.1.0-dev1' })
  host.mobileVault.mockResolvedValue({ vault_id: 'owned', note_count: 0 })
  host.mobileOnBack.mockImplementation(async handler => { back = handler; return { unregister } })
  host.mobileBackground.mockResolvedValue({ state: 'backgrounded' })
  host.mobileRequest.mockImplementation(async request => {
    if (request.kind === 'create_draft') return { draft: draft(), current: null, current_error: null, pending_save: null, pending_mutation: null }
    if (request.kind === 'persist_draft') return persist ? persist(request) : { ...draft(), generation: request.generation, content: request.content }
    return { items: [], total: 0, directories: [], unsearched_large_notes: 0 }
  })
})

async function create(wrapper: ReturnType<typeof mount>) {
  await button(wrapper, format === 'canvas' ? 'New canvas' : 'New note').trigger('click')
  await wrapper.get('.mobile-dialog input').setValue(format === 'canvas' ? 'owned.canvas' : 'owned.md')
  await wrapper.get('.mobile-dialog form').trigger('submit'); await flushPromises()
}

it('uses one native Back listener and backgrounds only from the root, with cleanup on unmount', async () => {
  const wrapper = mount(App); await flushPromises()
  expect(host.mobileOnBack).toHaveBeenCalledTimes(1)
  await wrapper.get('.titlebar [aria-label="Settings"]').trigger('click'); await flushPromises()
  await wrapper.get('[aria-label="Interface language"]').setValue('zh-CN'); expect(host.mobileBackground).not.toHaveBeenCalled()
  back(); await flushPromises(); expect(wrapper.find('dialog').exists()).toBe(false); expect(host.mobileBackground).not.toHaveBeenCalled()
  back(); await flushPromises(); expect(host.mobileBackground).toHaveBeenCalledTimes(1)
  wrapper.unmount(); expect(unregister).toHaveBeenCalledTimes(1)
  back(); await flushPromises(); expect(host.mobileBackground).toHaveBeenCalledTimes(1)
})

it('coalesces repeated Back presses while a draft acknowledgement is pending and never exits early', async () => {
  const wrapper = mount(App); await flushPromises(); await create(wrapper)
  // The real Canvas emits document edits through this same session boundary.
  const editor = wrapper.getComponent({ name: 'MobileEditor' }); editor.vm.$emit('update:modelValue', '原文 retained')
  let acknowledge!: (value: unknown) => void
  persist = request => new Promise(resolve => { acknowledge = () => resolve({ ...draft(), generation: request.generation, content: request.content }) })
  back(); back(); await flushPromises()
  expect(wrapper.find('.editor-page').exists()).toBe(true); expect(host.mobileBackground).not.toHaveBeenCalled()
  expect(host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'persist_draft')).toHaveLength(1)
  acknowledge(undefined); await flushPromises()
  expect(wrapper.find('.editor-page').exists()).toBe(false); expect(host.mobileBackground).not.toHaveBeenCalled()
  back(); await flushPromises(); expect(host.mobileBackground).toHaveBeenCalledTimes(1)
  wrapper.unmount()
})

it('keeps the editor and original input when Back encounters a real persistence rejection', async () => {
  const wrapper = mount(App); await flushPromises(); await create(wrapper)
  wrapper.getComponent({ name: 'MobileEditor' }).vm.$emit('update:modelValue', 'unsaved original')
  persist = async () => { throw Error('QUOTA_EXCEEDED') }
  back(); await flushPromises()
  expect(wrapper.find('.editor-page').exists()).toBe(true); expect(wrapper.text()).toContain('Storage is full')
  expect(host.mobileBackground).not.toHaveBeenCalled()
  persist = undefined; back(); await flushPromises()
  expect(wrapper.find('.editor-page').exists()).toBe(false)
  wrapper.unmount()
})

it('closes a creation review before leaving the vault and never submits it on Back', async () => {
  const wrapper = mount(App); await flushPromises()
  await button(wrapper, 'New note').trigger('click'); await wrapper.get('.mobile-dialog input').setValue('unsubmitted.md')
  back(); await flushPromises()
  expect(wrapper.find('.mobile-dialog').exists()).toBe(false)
  expect(host.mobileRequest.mock.calls.some(([r]) => r.kind === 'create_draft')).toBe(false)
  expect(host.mobileBackground).not.toHaveBeenCalled()
  wrapper.unmount()
})

it('closes the shared phone file drawer on Back without leaving or submitting the edited document', async () => {
  const width = window.innerWidth
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  const wrapper = mount(App)
  try {
    await flushPromises(); await create(wrapper)
    wrapper.getComponent({ name: 'MobileEditor' }).vm.$emit('update:modelValue', 'keep the original unsaved input')
    await wrapper.get('.titlebar [aria-label="Files"]').trigger('click')
    expect(wrapper.get('.app-shell').classes()).toContain('secondary-open')
    const writes = host.mobileRequest.mock.calls.filter(([request]) => request.kind === 'persist_draft').length
    back(); await flushPromises()
    expect(wrapper.get('.app-shell').classes()).not.toContain('secondary-open')
    expect(wrapper.find('.editor-page').exists()).toBe(true)
    expect(host.mobileRequest.mock.calls.filter(([request]) => request.kind === 'persist_draft')).toHaveLength(writes)
    expect(host.mobileBackground).not.toHaveBeenCalled()
  } finally {
    wrapper.unmount(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
  }
})

it('uses drawers at tablet portrait width and desktop columns only when landscape width is available', async () => {
  const width = window.innerWidth
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 })
  const wrapper = mount(App)
  try {
    await flushPromises()
    expect(wrapper.get('.app-shell').classes()).toContain('compact')
    const reads = host.mobileRequest.mock.calls.length
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
    window.dispatchEvent(new Event('resize')); await wrapper.vm.$nextTick()
    expect(wrapper.get('.app-shell').classes()).not.toContain('compact')
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 })
    window.dispatchEvent(new Event('resize')); await wrapper.vm.$nextTick()
    expect(wrapper.get('.app-shell').classes()).toContain('compact')
    expect(host.mobileRequest).toHaveBeenCalledTimes(reads)
  } finally { wrapper.unmount(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: width }) }
})

it('consumes native Back to collapse formatting before leaving or persisting the document', async () => {
  const collapse = vi.fn().mockReturnValueOnce(true).mockReturnValue(false)
  const rich = defineComponent({ setup(_, { expose }) { expose({ handleBack: collapse }); return () => h('div', 'Shared rich editor') } })
  const wrapper = mount(App, { global: { stubs: { RichEditor: rich } } })
  try {
    await flushPromises(); await create(wrapper)
    await button(wrapper, 'Writing').trigger('click'); await flushPromises()
    const writes = host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'persist_draft').length
    back(); await flushPromises()
    expect(collapse).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.editor-page').exists()).toBe(true)
    expect(host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'persist_draft')).toHaveLength(writes)
    back(); await flushPromises()
    expect(collapse).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.editor-page').exists()).toBe(false)
  } finally { wrapper.unmount() }
})

it('retains an unfinished Canvas property on Back and closes a valid property panel before leaving', async () => {
  format = 'canvas'
  const wrapper = mount(App); await flushPromises(); await create(wrapper)
  await button(wrapper, 'URL').trigger('click'); await wrapper.get('.canvas-dialog input').setValue('https://example.org/')
  await wrapper.get('.canvas-dialog form').trigger('submit')
  const url = wrapper.get<HTMLTextAreaElement>('.canvas-properties textarea'); await url.setValue('h')
  back(); await flushPromises()
  expect(url.element.value).toBe('h'); expect(wrapper.find('.canvas-properties').exists()).toBe(true)
  expect(host.mobileBackground).not.toHaveBeenCalled()
  await url.setValue('https://example.org/new'); back(); await flushPromises()
  expect(wrapper.find('.canvas-properties').exists()).toBe(false); expect(wrapper.find('.editor-page').exists()).toBe(true)
  back(); await flushPromises(); expect(wrapper.find('.editor-page').exists()).toBe(false)
  wrapper.unmount()
})

it('removes a listener that finishes registering after the editor was unmounted', async () => {
  let registered!: (value: { unregister: () => void }) => void
  host.mobileOnBack.mockImplementation(() => new Promise(resolve => { registered = resolve }))
  const wrapper = mount(App); await flushPromises(); wrapper.unmount()
  registered({ unregister }); await flushPromises()
  expect(unregister).toHaveBeenCalledTimes(1)
  expect(host.mobileBackground).not.toHaveBeenCalled()
})

it('does not leave a Canvas while Chinese composition owns the property field', async () => {
  format = 'canvas'
  const wrapper = mount(App); await flushPromises(); await create(wrapper); await button(wrapper, 'Text').trigger('click')
  const field = wrapper.get<HTMLTextAreaElement>('.canvas-properties textarea')
  await field.trigger('compositionstart'); field.element.value = '中文组合'; await field.trigger('input')
  const before = host.mobileRequest.mock.calls.length
  back(); await flushPromises()
  expect(wrapper.find('.canvas-properties').exists()).toBe(true); expect(field.element.value).toBe('中文组合')
  expect(host.mobileRequest.mock.calls.length).toBe(before); expect(host.mobileBackground).not.toHaveBeenCalled()
  await field.trigger('compositionend'); await flushPromises(); back(); await flushPromises()
  expect(wrapper.find('.canvas-properties').exists()).toBe(false); expect(wrapper.find('.editor-page').exists()).toBe(true)
  wrapper.unmount()
})

it('shows a retryable startup failure if the promised Android Back listener cannot register', async () => {
  host.mobileOnBack.mockRejectedValue(Error('native listener unavailable'))
  const wrapper = mount(App); await flushPromises()
  expect(wrapper.find('.startup-card').exists()).toBe(true); expect(wrapper.text()).toContain('Android Back navigation could not initialize')
  expect(wrapper.find('.notes-page').exists()).toBe(false)
  host.mobileOnBack.mockImplementation(async handler => { back = handler; return { unregister } })
  await button(wrapper, 'Try again').trigger('click'); await flushPromises()
  expect(wrapper.find('.empty-workspace').exists()).toBe(true)
  wrapper.unmount()
})
