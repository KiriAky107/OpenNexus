// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'
import PermissionReview from './PermissionReview.vue'
import { getPermissionPreview, respondToPermission } from '@/services/agentService'
const state = vi.hoisted(() => ({ workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/agentService', () => ({ getPermissionPreview: vi.fn(), respondToPermission: vi.fn() }))
const preview = { token: 'revision-one', file_path: 'lesson.md', note_id: 'id', operation: 'replace' as const, metadata: { title: null, tags: ['keep'] }, diff: { lines: [{ kind: '+' as const, line: 1, text: '<script>text</script>' }], added_chars: 10, removed_chars: 0, before_chars: 0, after_chars: 10, truncated: false } }
const props = { runId: 'run', requestId: 'request', call: { name: 'notes.update', arguments: { markdown: 'proposed' } } }
beforeEach(() => {
  vi.clearAllMocks(); state.workspace = reactive({ vaultId: 'one' })
  vi.mocked(getPermissionPreview).mockResolvedValue(preview)
  vi.mocked(respondToPermission).mockResolvedValue({} as never)
})
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(item => item.text() === text)!
it('requires loaded preview and sends its token, rendering diff as escaped text', async () => {
  const wrapper = mount(PermissionReview, { props })
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  await flushPromises()
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.text()).toContain('<script>text</script>')
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'allow_once', 'revision-one')
  expect(wrapper.emitted('resolved')).toHaveLength(1)
  wrapper.unmount()
})
it('invalidates rejected previews and requires an explicit new preview', async () => {
  const wrapper = mount(PermissionReview, { props }); await flushPromises()
  vi.mocked(respondToPermission).mockRejectedValueOnce(new Error('笔记已变化'))
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('笔记已变化')
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  vi.mocked(getPermissionPreview).mockResolvedValue({ ...preview, token: 'revision-two' })
  await button(wrapper, '重新预览').trigger('click'); await flushPromises()
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenLastCalledWith('run', 'request', 'allow_once', 'revision-two')
  wrapper.unmount()
})
it('allows denial when a preview cannot be produced', async () => {
  vi.mocked(getPermissionPreview).mockRejectedValueOnce(new Error('目标不存在'))
  const wrapper = mount(PermissionReview, { props }); await flushPromises()
  await button(wrapper, '拒绝').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'deny')
  wrapper.unmount()
})
it('discards a preview fetched under the previous vault', async () => {
  let resolve!: (value: typeof preview) => void
  vi.mocked(getPermissionPreview).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  const wrapper = mount(PermissionReview, { props })
  vi.mocked(getPermissionPreview).mockRejectedValueOnce(new Error('不可访问'))
  state.workspace.vaultId = 'two'; await flushPromises()
  resolve(preview); await flushPromises()
  expect(wrapper.text()).not.toContain('lesson.md')
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
