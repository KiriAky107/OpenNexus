// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import RunActivity from './RunActivity.vue'
import api from '@/services/apiClient'
import { getAgentTrace, getPermissionPreview, respondToPermission } from '@/services/agentService'
const state = vi.hoisted(() => ({ workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/apiClient', () => ({ default: { get: vi.fn() } }))
vi.mock('@/services/agentService', () => ({ getAgentTrace: vi.fn(), getPermissionPreview: vi.fn(), respondToPermission: vi.fn(), cancelAgentRun: vi.fn() }))
vi.mock('@/composables/useCitationNavigation', () => ({ useCitationNavigation: () => ({ openCitation: vi.fn() }) }))
vi.mock('@/components/common/MarkdownContent.vue', () => ({ default: { props: ['source'], template: '<p>{{ source }}</p>' } }))
vi.mock('./BudgetConfirmation.vue', () => ({ default: { template: '<div data-budget />' } }))
const current = { run_id: 'run_a', status: 'waiting_permission', current_step: 1 }
beforeEach(() => {
  vi.clearAllMocks()
  state.workspace = reactive({ vaultId: 'vault-a' })
  vi.mocked(api.get).mockResolvedValue(current)
  vi.mocked(getPermissionPreview).mockResolvedValue({ token: 'reviewed', file_path: 'note.md', operation: 'create', note_id: null, metadata: { title: 'note', tags: [] }, diff: { lines: [{ kind: '+', line: 1, text: 'new content' }], added_chars: 11, removed_chars: 0, before_chars: 0, after_chars: 11, truncated: false } })
  vi.mocked(getAgentTrace).mockResolvedValue({ items: [{ event: 'PermissionRequired', run_id: 'run_a', sequence: 1, timestamp: '', data: { request_id: 'p', tool_call: { name: 'notes.create', tool_call_id: 't' } } }], has_more: false } as never)
})
it('shows permission actions independently of collapsed execution detail', async () => {
  const wrapper = mount(RunActivity, { props: { runId: 'run_a' } })
  await flushPromises()
  expect(wrapper.get('.permission-card').text()).toContain('等待操作授权')
  expect(wrapper.find('.permission-review.ui-disclosure').exists()).toBe(true)
  expect(wrapper.find('.run-details.ui-disclosure').exists()).toBe(true)
  expect(wrapper.get('.badge.warning').text()).toContain('等待')
  const allow = wrapper.findAll('button').find(button => button.text() === '允许本次')!
  await allow.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('new content')
  expect(respondToPermission).toHaveBeenCalledWith('run_a', 'p', 'allow_once', 'reviewed')
  wrapper.unmount()
})
it('opens a permission dialog in chat and keeps the inline action when postponed', async () => {
  const wrapper = mount(RunActivity, { props: { runId: 'run_a', permissionDialog: true },
    global: { stubs: { AppDialog: { template: '<div data-dialog><slot /></div>' } } } })
  await flushPromises()
  expect(wrapper.get('[data-dialog]').text()).toContain('确认智能体操作')
  expect(wrapper.get('[data-dialog]').text()).toContain('允许本次')
  expect(getPermissionPreview).toHaveBeenCalledTimes(1)
  await wrapper.findAll('[data-dialog] button').find(button => button.text() === '稍后处理')!.trigger('click')
  expect(wrapper.find('[data-dialog]').exists()).toBe(false)
  expect(wrapper.get('.permission-card').text()).toContain('允许本次')
  await flushPromises()
  expect(getPermissionPreview).toHaveBeenCalledTimes(2)
  wrapper.unmount()
})
it('shows the latest real experiment outcome in chat even with execution details collapsed', async () => {
  vi.mocked(api.get).mockResolvedValue({ ...current, status: 'completed' })
  vi.mocked(getAgentTrace).mockResolvedValue({ items: ['running', 'failed'].map((status, index) => ({
    event: 'ExperimentState', run_id: 'run_a', sequence: index + 1, timestamp: '',
    data: { kind: 'experiment_run', vault_id: 'vault-a', operation_id: 'owned-experiment', state: status },
  })), has_more: false } as never)
  const wrapper = mount(RunActivity, { props: { runId: 'run_a' } }); await flushPromises()
  expect(wrapper.findAll('.experiment-event')).toHaveLength(1)
  expect(wrapper.get('.experiment-event').text()).toContain('实验运行 · 失败')
  expect(wrapper.get('.run-details').attributes('open')).toBeUndefined()
  wrapper.unmount()
})
it('ignores a late response after switching vaults', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(api.get).mockImplementationOnce(() => new Promise(done => { resolve = done }) as never)
  const wrapper = mount(RunActivity, { props: { runId: 'run_a' } })
  state.workspace.vaultId = 'vault-b'
  vi.mocked(api.get).mockRejectedValueOnce(new Error('not in this vault'))
  await flushPromises()
  resolve(current); await flushPromises()
  expect(wrapper.find('.permission-card').exists()).toBe(false)
  expect(wrapper.text()).toContain('not in this vault')
  wrapper.unmount()
})
it('explains an unavailable legacy run without showing an unknown status or dead link', async () => {
  vi.mocked(api.get).mockRejectedValueOnce(Object.assign(new Error('Agent run does not exist: run_old'), { code: 'AGENT_RUN_NOT_FOUND' }))
  const wrapper = mount(RunActivity, { props: { runId: 'run_old' } })
  await flushPromises()
  expect(wrapper.text()).toContain('当前知识库无法定位此历史运行记录')
  expect(wrapper.text()).not.toContain('run_old')
  expect(wrapper.text()).not.toContain('未知状态')
  expect(wrapper.text()).not.toContain('打开完整运行记录')
  wrapper.unmount()
})
