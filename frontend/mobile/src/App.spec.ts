// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import App from './App.vue'
import MobileCanvas from './MobileCanvas.vue'

const host = vi.hoisted(() => ({ mobileCapabilities: vi.fn(), mobileVault: vi.fn(), mobileRequest: vi.fn() }))
vi.mock('./native', () => host)

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  localStorage.setItem('opennexus.mobile.language', 'en')
  host.mobileCapabilities.mockResolvedValue({ protocol: 1, platform: 'android', workspace: true, notes: true, version: '0.1.0-dev1' })
  host.mobileVault.mockResolvedValue({ vault_id: 'owned-vault', note_count: 0 })
  host.mobileRequest.mockResolvedValue({ items: [], total: 0, directories: [], unsearched_large_notes: 0 })
})

it('changes interface language without rereading or changing the native vault', async () => {
  const wrapper = mount(App)
  await flushPromises()
  expect(wrapper.text()).toContain('Local vault opened')
  const nativeReads = host.mobileRequest.mock.calls.length
  await wrapper.get('.workspace-header select').setValue('zh-CN')
  await flushPromises()
  expect(wrapper.text()).toContain('本机知识库已打开')
  expect(document.documentElement.lang).toBe('zh-CN')
  expect(localStorage.getItem('opennexus.mobile.language')).toBe('zh-CN')
  expect(host.mobileCapabilities).toHaveBeenCalledTimes(1)
  expect(host.mobileVault).toHaveBeenCalledTimes(1)
  expect(host.mobileRequest).toHaveBeenCalledTimes(nativeReads)
  wrapper.unmount()
})

it('switches themes without submitting a path review or changing its original input', async () => {
  const wrapper = mount(App); await flushPromises()
  await wrapper.findAll('button').find(item => item.text() === 'New note')!.trigger('click')
  await wrapper.get('.mobile-dialog input').setValue('未提交 original.md')
  const before = host.mobileRequest.mock.calls.length
  await wrapper.get('.theme-choice select').setValue('dark'); await flushPromises()
  expect(wrapper.get<HTMLInputElement>('.mobile-dialog input').element.value).toBe('未提交 original.md')
  expect(host.mobileRequest).toHaveBeenCalledTimes(before)
  expect(document.documentElement.dataset.theme).toBe('dark')
  wrapper.unmount()
})

it('creates a Canvas through its own native format route and opens free editing instead of Markdown', async () => {
  const raw = '{\n  "nodes": [],\n  "edges": []\n}\n'
  host.mobileRequest.mockImplementation(async request => {
    if (request.kind === 'create_draft') return { draft: { id: 'canvas', file_id: null, path: request.path, base_hash: '', generation: 1, saved_generation: 0, content: raw, edit_session: 'canvas-lease' }, current: null, current_error: null, pending_save: null, pending_mutation: null }
    return { items: [], total: 0, directories: [], unsearched_large_notes: 0 }
  })
  const wrapper = mount(App); await flushPromises()
  await wrapper.findAll('button').find(item => item.text() === 'New canvas')!.trigger('click')
  await wrapper.get('.mobile-dialog input').setValue('Canvas/ideas')
  await wrapper.get('.mobile-dialog form').trigger('submit'); await flushPromises()
  expect(host.mobileRequest).toHaveBeenCalledWith({ kind: 'create_draft', path: 'Canvas/ideas.canvas' })
  expect(wrapper.find('.canvas-viewport').exists()).toBe(true); expect(wrapper.find('.cm-editor').exists()).toBe(false)
  await wrapper.findAll('.canvas-toolbar button').find(item => item.text() === 'Text')!.trigger('click')
  expect(wrapper.findAll('.canvas-node')).toHaveLength(1)
  expect(wrapper.text()).toContain('New text')
  wrapper.unmount()
})

it('resolves a Canvas file reference from the Vault root even when the Canvas is in a nested folder', async () => {
  host.mobileRequest.mockImplementation(async request => {
    if (request.kind === 'create_draft') return { draft: { id: 'nested', file_id: null, path: request.path, base_hash: '', generation: 1, saved_generation: 0, content: '{"nodes":[],"edges":[]}', edit_session: 'lease' }, current: null, current_error: null, pending_save: null, pending_mutation: null }
    if (request.kind === 'read_path') return { file_id: 'target', path: 'Notes/original.md', content: 'original', hash: 'hash', revision: 1 }
    if (request.kind === 'open_note') return { draft: { id: 'target-draft', file_id: 'target', path: 'Notes/original.md', base_hash: 'hash', generation: 1, saved_generation: 1, content: 'original', edit_session: 'target-lease' }, current: null, current_error: null, pending_save: null, pending_mutation: null }
    return { items: [], total: 0, directories: [], unsearched_large_notes: 0 }
  })
  const wrapper = mount(App); await flushPromises()
  await wrapper.findAll('button').find(item => item.text() === 'New canvas')!.trigger('click')
  await wrapper.get('.mobile-dialog input').setValue('Canvas/nested.canvas'); await wrapper.get('.mobile-dialog form').trigger('submit'); await flushPromises()
  wrapper.getComponent(MobileCanvas).vm.$emit('link', 'Notes/original.md'); await flushPromises()
  await wrapper.findAll('.mobile-dialog button').find(item => item.text() === 'Open')!.trigger('click'); await flushPromises()
  expect(host.mobileRequest).toHaveBeenCalledWith({ kind: 'read_path', path: 'Notes/original.md' })
  wrapper.unmount()
})
