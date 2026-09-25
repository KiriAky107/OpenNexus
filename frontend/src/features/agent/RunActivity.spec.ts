// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import RunActivity from './RunActivity.vue'
import api from '@/services/apiClient'
import { getAgentTrace, respondToPermission } from '@/services/agentService'
const state = vi.hoisted(() => ({ workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/apiClient', () => ({ default: { get: vi.fn() } }))
vi.mock('@/services/agentService', () => ({ getAgentTrace: vi.fn(), respondToPermission: vi.fn(), cancelAgentRun: vi.fn() }))
vi.mock('@/composables/useCitationNavigation', () => ({ useCitationNavigation: () => ({ openCitation: vi.fn() }) }))
vi.mock('@/components/common/MarkdownContent.vue', () => ({ default: { props: ['source'], template: '<p>{{ source }}</p>' } }))
vi.mock('./BudgetConfirmation.vue', () => ({ default: { template: '<div data-budget />' } }))
const current = { run_id: 'run_a', status: 'waiting_permission', current_step: 1 }
beforeEach(() => {
  vi.clearAllMocks()
  state.workspace = reactive({ vaultId: 'vault-a' })
  vi.mocked(api.get).mockResolvedValue(current)
  vi.mocked(getAgentTrace).mockResolvedValue({ items: [{ event: 'PermissionRequired', run_id: 'run_a', sequence: 1, timestamp: '', data: { request_id: 'p', tool_call: { name: 'notes.create', tool_call_id: 't' } } }], has_more: false } as never)
})
it('shows permission actions independently of collapsed execution detail', async () => {
  const wrapper = mount(RunActivity, { props: { runId: 'run_a' } })
  await flushPromises()
  expect(wrapper.get('.permission-card').text()).toContain('等待操作授权')
  const allow = wrapper.findAll('button').find(button => button.text() === '允许本次')!
  await allow.trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run_a', 'p', 'allow_once')
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
