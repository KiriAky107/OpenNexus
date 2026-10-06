// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import CollaborationCard from './CollaborationCard.vue'
import { getCollaboration, type Collaboration } from '@/services/agentManagement'

vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ vaultId: 'vault' }) }))
vi.mock('@/services/agentManagement', () => ({ getCollaboration: vi.fn(), reviewCollaboration: vi.fn(), cancelCollaboration: vi.fn() }))
const group = (status: string, error: string) => ({ title: 'Group', status, error, members: [],
  token_usage: 0, plan: { token_budget: 1000 } }) as unknown as Collaboration
function mountCard() { return mount(CollaborationCard, { props: { id: 'group' }, global: { stubs: { BudgetConfirmation: true, RunActivity: true } } }) }
beforeEach(() => { vi.clearAllMocks() })

it('shows cancellation status without presenting an expected CancelledError as a failure', async () => {
  vi.mocked(getCollaboration).mockResolvedValue(group('cancelled', 'CancelledError'))
  const wrapper = mountCard(); await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.find('.badge').text()).not.toContain('CancelledError')
  expect(wrapper.text()).toContain('已取消')
  wrapper.unmount()
})

it('keeps actual failure and cancellation cleanup errors visible', async () => {
  for (const [status, error] of [['failed', 'Runtime failed'], ['cancelled', 'Cleanup failed'], ['failed', 'CancelledError']]) {
    vi.mocked(getCollaboration).mockResolvedValue(group(status!, error!))
    const wrapper = mountCard(); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe(error)
    wrapper.unmount()
  }
})

it('keeps refresh failures visible even after a normal cancellation', async () => {
  vi.mocked(getCollaboration).mockResolvedValueOnce(group('cancelled', 'CancelledError')).mockRejectedValueOnce(new Error('Disconnected'))
  const wrapper = mountCard(); await flushPromises()
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toBe('Disconnected')
  wrapper.unmount()
})
