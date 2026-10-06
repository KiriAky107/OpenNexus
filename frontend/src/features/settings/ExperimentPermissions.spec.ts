// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import ExperimentPermissions from './ExperimentPermissions.vue'
import { getExperimentPermissions, updateExperimentPermission } from '@/services/systemService'
vi.mock('@/services/systemService', () => ({ getExperimentPermissions: vi.fn(), updateExperimentPermission: vi.fn() }))
const initial = { revision: 0, rules: { 'experiments.run': 'deny' as const, 'experiments.import': 'deny' as const } }
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getExperimentPermissions).mockResolvedValue(initial)
})
it('keeps actions disabled until loaded and enables proposals rather than automatic approval', async () => {
  const wrapper = mount(ExperimentPermissions)
  expect(wrapper.get('input').attributes('disabled')).toBeDefined()
  await flushPromises()
  expect(wrapper.findAll('input').every(input => !(input.element as HTMLInputElement).checked)).toBe(true)
  expect(wrapper.text()).toContain('每次仍须审核内容并通过桌面原生确认')
  vi.mocked(updateExperimentPermission).mockResolvedValue({ revision: 1, rules: { ...initial.rules, 'experiments.run': 'confirm' } })
  await wrapper.get('input').setValue(true); await flushPromises()
  expect(updateExperimentPermission).toHaveBeenCalledWith('experiments.run', 'confirm', 0)
  expect((wrapper.findAll('input')[1]!.element as HTMLInputElement).checked).toBe(false)
  expect(wrapper.emitted('changed')).toHaveLength(1)
  wrapper.unmount()
})
it('a conflicting save requires fresh settings before another change', async () => {
  vi.mocked(updateExperimentPermission).mockRejectedValue(new Error('设置已变化'))
  const wrapper = mount(ExperimentPermissions); await flushPromises()
  await wrapper.get('input').setValue(true); await flushPromises()
  expect(wrapper.text()).toContain('设置已变化')
  expect(wrapper.findAll('input').every(input => input.attributes('disabled') !== undefined)).toBe(true)
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(getExperimentPermissions).toHaveBeenCalledTimes(2)
  expect((wrapper.get('input').element as HTMLInputElement).checked).toBe(false)
  wrapper.unmount()
})
