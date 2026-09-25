// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BudgetConfirmation from './BudgetConfirmation.vue'
import type { AgentEvent } from '@/contracts'
import { cancelAgentRun, extendAgentBudget } from '@/services/agentService'

vi.mock('@/services/agentService', () => ({ cancelAgentRun: vi.fn(), extendAgentBudget: vi.fn() }))
const event: AgentEvent = { event: 'BudgetRequired', sequence: 4, run_id: 'run_demo', timestamp: '', data: { request_id: 'budget_demo', token_usage: 8200, token_budget: 8000 } }
function setup() {
  return mount(BudgetConfirmation, { props: { runId: 'run_demo', status: 'waiting_budget', events: [event] }, global: { stubs: { AppDialog: { template: '<div data-dialog><slot /></div>' } } } })
}
describe('BudgetConfirmation', () => {
  beforeEach(() => vi.clearAllMocks())
  it('dismisses without cancelling and can reopen', async () => {
    const wrapper = setup()
    await wrapper.findAll('button').find(button => button.text().includes('稍后决定'))!.trigger('click')
    expect(wrapper.find('[data-dialog]').exists()).toBe(false)
    expect(cancelAgentRun).not.toHaveBeenCalled()
    expect(extendAgentBudget).not.toHaveBeenCalled()
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[data-dialog]').exists()).toBe(true)
  })
  it('sends a bounded addition for the same run and request', async () => {
    const wrapper = setup()
    await wrapper.find('input').setValue('2000')
    await wrapper.findAll('button').find(button => button.text().includes('追加预算并继续'))!.trigger('click')
    await flushPromises()
    expect(extendAgentBudget).toHaveBeenCalledExactlyOnceWith('run_demo', 'budget_demo', 2000)
    expect(wrapper.emitted('resolved')).toHaveLength(1)
    expect(wrapper.find('[data-dialog]').exists()).toBe(false)
  })
  it('rejects invalid additions and shows API failures without losing the request', async () => {
    const wrapper = setup()
    await wrapper.find('input').setValue('0')
    const button = wrapper.findAll('button').find(button => button.text().includes('追加预算并继续'))!
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.find('input').setValue('10')
    vi.mocked(extendAgentBudget).mockRejectedValueOnce(new Error('Disconnected'))
    await button.trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('Disconnected')
    expect(wrapper.find('[data-dialog]').exists()).toBe(true)
  })
})
