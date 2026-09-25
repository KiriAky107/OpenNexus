// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import AgentManager from './AgentManager.vue'
const service = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn() }))
vi.mock('@/services/agentManagement', () => ({ listDefinitions: service.list, createDefinition: service.create, updateDefinition: service.update,
  deleteDefinition: vi.fn(), listCollaborations: vi.fn().mockResolvedValue({ items: [] }) }))
vi.mock('./RunActivity.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./CollaborationCard.vue', () => ({ default: { template: '<div />' } }))
beforeEach(() => { setActivePinia(createPinia()); service.list.mockResolvedValue({ items: [] }); service.create.mockReset(); service.update.mockReset() })
it('defaults manual definitions to no token limit and allows opting into a finite budget', async () => {
  const wrapper = mount(AgentManager)
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '新建配置')!.trigger('click')
  const form = wrapper.get('form.panel')
  const checkbox = wrapper.findAll('label').find(label => label.text().includes('设置 Token 预算上限'))!.get('input')
  expect((checkbox.element as HTMLInputElement).checked).toBe(false)
  expect(form.find('input[type="number"][max="1000000"]').exists()).toBe(false)
  await checkbox.setValue(true)
  expect((form.get('input[type="number"][max="1000000"]').element as HTMLInputElement).value).toBe('8000')
  await checkbox.setValue(false)
  expect(form.find('input[type="number"][max="1000000"]').exists()).toBe(false)
  wrapper.unmount()
})
it('distinguishes creation sources without relabeling legacy definitions', async () => {
  service.list.mockResolvedValue({ items: ['manual', 'chat', undefined].map((origin, index) => ({ id: String(index), revision: 1, origin,
    config: { name: `Agent ${index}`, tools: [], enabled: true, token_budget: index ? 4000 : null } })) })
  const wrapper = mount(AgentManager); await flushPromises()
  expect(wrapper.text()).toContain('手动创建')
  expect(wrapper.text()).toContain('AI 对话创建')
  expect(wrapper.text()).toContain('历史配置')
  expect(wrapper.text()).toContain('不设上限')
  await wrapper.findAll('button').filter(button => button.text() === '编辑与管理')[1]!.trigger('click')
  expect((wrapper.get('input[type="number"][max="1000000"]').element as HTMLInputElement).value).toBe('4000')
  wrapper.unmount()
})
