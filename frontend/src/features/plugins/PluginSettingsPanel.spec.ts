// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { PluginSettingsSchema } from '@/contracts'
import * as service from '@/services/pluginService'
import PluginSettingsPanel from './PluginSettingsPanel.vue'

vi.mock('@/services/pluginService', () => ({ getPluginSettings: vi.fn(), updatePluginSettings: vi.fn(), putPluginSecret: vi.fn(), deletePluginSecret: vi.fn() }))
const schema = (value = ''): PluginSettingsSchema => ({ plugin_id: 'demo', schema_version: 1, fields: [{ key: 'name', label: 'Name', type: 'string', description: '', required: false, options: [] }], values: { name: value }, secrets: {} })
let wrapper: VueWrapper
beforeEach(() => { vi.resetAllMocks(); vi.mocked(service.getPluginSettings).mockResolvedValue(schema()) })
afterEach(() => wrapper?.unmount())

it('retains edits made during a save and submits them on the next save', async () => {
  let resolveSave!: (value: PluginSettingsSchema) => void
  vi.mocked(service.updatePluginSettings).mockReturnValueOnce(new Promise(resolve => { resolveSave = resolve }))
  wrapper = mount(PluginSettingsPanel, { props: { pluginId: 'demo' } })
  await flushPromises()
  await wrapper.get('input').setValue('first edit')
  await wrapper.get('.form-actions button').trigger('click')
  expect(service.updatePluginSettings).toHaveBeenLastCalledWith('demo', 1, { name: 'first edit' })
  await wrapper.get('input').setValue('second edit')
  resolveSave(schema('first edit'))
  await flushPromises()
  expect(wrapper.find('.unsaved-hint').exists()).toBe(true)
  expect(wrapper.get('.form-actions button').attributes('disabled')).toBeUndefined()
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('second edit')
  vi.mocked(service.updatePluginSettings).mockResolvedValueOnce(schema('second edit'))
  await wrapper.get('.form-actions button').trigger('click')
  await flushPromises()
  expect(service.updatePluginSettings).toHaveBeenLastCalledWith('demo', 1, { name: 'second edit' })
  expect(wrapper.find('.unsaved-hint').exists()).toBe(false)
})

it('keeps input and permits retry after a failed save', async () => {
  vi.mocked(service.updatePluginSettings).mockRejectedValueOnce(new Error('Save failed'))
  wrapper = mount(PluginSettingsPanel, { props: { pluginId: 'demo' } })
  await flushPromises()
  await wrapper.get('input').setValue('retry me')
  await wrapper.get('.form-actions button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('Save failed')
  expect(wrapper.find('.unsaved-hint').exists()).toBe(true)
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('retry me')
  vi.mocked(service.updatePluginSettings).mockResolvedValueOnce(schema('retry me'))
  await wrapper.get('.form-actions button').trigger('click')
  await flushPromises()
  expect(service.updatePluginSettings).toHaveBeenCalledTimes(2)
  expect(wrapper.find('.unsaved-hint').exists()).toBe(false)
})

it('ignores a save response after switching to another plugin', async () => {
  let resolveSave!: (value: PluginSettingsSchema) => void
  vi.mocked(service.updatePluginSettings).mockReturnValueOnce(new Promise(resolve => { resolveSave = resolve }))
  wrapper = mount(PluginSettingsPanel, { props: { pluginId: 'demo' } })
  await flushPromises()
  await wrapper.get('input').setValue('old plugin')
  await wrapper.get('.form-actions button').trigger('click')
  vi.mocked(service.getPluginSettings).mockResolvedValueOnce(schema('new plugin'))
  await wrapper.setProps({ pluginId: 'other' })
  await flushPromises()
  resolveSave(schema('old plugin'))
  await flushPromises()
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('new plugin')
  expect(wrapper.emitted('saved')).toBeUndefined()
})
