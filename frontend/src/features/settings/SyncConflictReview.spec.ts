// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { hostInvoke } from '@/services/platform/desktop'
import SyncConflictReview from './SyncConflictReview.vue'
import type { SyncConflictReview as Review } from '@/contracts/sync'
const controls = vi.hoisted(() => ({ confirm: vi.fn(), editor: { currentFilePath: '', saveStatus: 'saved' } }))
const editor = reactive(controls.editor)
vi.mock('@/services/platform/desktop', () => ({ hostInvoke: vi.fn() }))
vi.mock('@/stores/editor', () => ({ useEditorStore: () => editor }))
vi.mock('@/composables/useActionDialog', () => ({ useActionDialog: () => ({ actionDialog: null, resolveAction: vi.fn(), askConfirm: controls.confirm }) }))
const content = (text: string) => ({ exists: true, hash: 'hash-'+text, byte_size: text.length, text, preview_bytes: text.length, truncated: false })
const props = () => ({ vaultId: 'vault', bindingId: 'binding', disabled: false, conflict: { sequence: 7, local_path: 'note.md', local_hash: 'hash-local', remote: { path: 'note.md', operation: 'put' } } })
const review = (): Review => ({ vault_id: 'vault', binding_id: 'binding', sequence: 7, file_id: 'file', local_path: 'note.md', local_file_id: 'file', related: [], remote: { path: 'note.md', operation: 'put', base_revision: 2, sequence: 7 }, local: content('local'), incoming: content('<img src=x onerror=alert(1)>'), base: content('base'), fingerprint: 'reviewed' })
async function read(wrapper: ReturnType<typeof mount>) { await flushPromises(); await wrapper.findAll('button').find(button => button.text() === '读取正文与差异')!.trigger('click'); await flushPromises() }
afterEach(() => { vi.resetAllMocks(); editor.currentFilePath = ''; editor.saveStatus = 'saved' })
it('shows verified three-way changes as escaped text and binds only accepted decisions', async () => {
  vi.mocked(hostInvoke).mockImplementation(async command => command === 'sync_conflict_review' ? review() : null)
  const wrapper = mount(SyncConflictReview, { props: props() }); await read(wrapper)
  expect(wrapper.text()).toContain('三方比较'); expect(wrapper.findAll('img')).toHaveLength(0)
  expect(wrapper.text()).toContain('基线 → 远端')
  const button = wrapper.findAll('button').find(button => button.text() === '采用远端')!
  controls.confirm.mockResolvedValue(false); await button.trigger('click'); await flushPromises()
  expect(vi.mocked(hostInvoke).mock.calls.some(([name]) => name === 'sync_resolve')).toBe(false)
  controls.confirm.mockResolvedValue(true); await button.trigger('click'); await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('sync_resolve', { request: { vault_id: 'vault', binding_id: 'binding', sequence: 7, choice: 'remote', destination: '', expected: 'hash-local', fingerprint: 'reviewed' } })
  wrapper.unmount()
})
it('reviews other rename paths and protects unsaved source editors, including late confirmation', async () => {
  const value = review(); value.related = [{ path: 'experiments/source.py', file_id: 'source', content: content('源文件\r\n') }]
  vi.mocked(hostInvoke).mockResolvedValue(value)
  const wrapper = mount(SyncConflictReview, { props: props() }); await read(wrapper)
  expect(wrapper.text()).toContain('其他受影响路径：experiments/source.py')
  editor.currentFilePath = '/experiments/source.py'; editor.saveStatus = 'save_failed'; await flushPromises()
  const button = wrapper.findAll('button').find(button => button.text() === '采用远端')!
  expect(button.attributes('disabled')).toBeDefined()
  editor.saveStatus = 'saved'; await flushPromises()
  let confirm!: (value: boolean) => void
  controls.confirm.mockImplementation(() => new Promise(resolve => { confirm = resolve }))
  await button.trigger('click'); editor.saveStatus = 'dirty'; confirm(true); await flushPromises()
  expect(vi.mocked(hostInvoke).mock.calls.some(([name]) => name === 'sync_resolve')).toBe(false)
  wrapper.unmount()
})
it('discards late previews and confirmation when vault or conflict changes', async () => {
  let answer!: (value: unknown) => void
  vi.mocked(hostInvoke).mockImplementation(() => new Promise(resolve => { answer = resolve }))
  const wrapper = mount(SyncConflictReview, { props: props() }); await wrapper.get('button').trigger('click')
  await wrapper.setProps({ vaultId: 'another' }); answer(review()); await flushPromises()
  expect(wrapper.text()).not.toContain('三方比较')
  await wrapper.setProps({ vaultId: 'vault' }); vi.mocked(hostInvoke).mockResolvedValue(review()); await read(wrapper)
  let confirm!: (value: boolean) => void
  controls.confirm.mockImplementation(() => new Promise(resolve => { confirm = resolve }))
  await wrapper.findAll('button').find(button => button.text() === '采用远端')!.trigger('click')
  await wrapper.setProps({ conflict: { ...props().conflict, current_hash: 'human-edit' } }); confirm(true); await flushPromises()
  expect(vi.mocked(hostInvoke).mock.calls.some(([name]) => name === 'sync_resolve')).toBe(false)
  wrapper.unmount()
})
it('blocks unsaved editors, clears stale failures and labels prefix-only reviews', async () => {
  const value = review(); value.base = null; value.local.truncated = true
  vi.mocked(hostInvoke).mockImplementation(async command => { if (command === 'sync_conflict_review') return value; throw new Error('SYNC_CONFLICT_CHANGED') })
  const wrapper = mount(SyncConflictReview, { props: props() }); editor.currentFilePath = '/note.md'; editor.saveStatus = 'dirty'; await flushPromises()
  expect(wrapper.get('button').attributes('disabled')).toBeDefined(); expect(wrapper.text()).toContain('未保存')
  editor.saveStatus = 'saved'; await read(wrapper); expect(wrapper.text()).toContain('两方比较'); expect(wrapper.text()).toContain('不是全文')
  controls.confirm.mockResolvedValue(true); await wrapper.findAll('button').find(button => button.text() === '采用远端')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('SYNC_CONFLICT_CHANGED'); expect(wrapper.text()).not.toContain('采用远端')
  wrapper.unmount()
})
