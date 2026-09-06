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
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

function secretSchema(configured = false): PluginSettingsSchema {
  return { ...schema(), fields: [{ key: 'token', label: 'Token', type: 'secret', description: '', required: false, options: [] }], secrets: { token: { configured } } }
}

it('preserves new secret input during a pending save and allows saving it next', async () => {
  vi.mocked(service.getPluginSettings).mockResolvedValue(secretSchema())
  let finish!: (value: Awaited<ReturnType<typeof service.putPluginSecret>>) => void
  vi.mocked(service.putPluginSecret).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  wrapper = mount(PluginSettingsPanel, { props: { pluginId: 'demo' } })
  await flushPromises()
  await wrapper.get('input[type="password"]').setValue('first-fixture-value')
  await wrapper.get('.secret-row button').trigger('click')
  await wrapper.get('input[type="password"]').setValue('second-fixture-value')
  await wrapper.get('.secret-row button').trigger('click')
  expect(service.putPluginSecret).toHaveBeenCalledTimes(1)
  finish({ plugin_id: 'demo', key: 'token', configured: true })
  await flushPromises()
  expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('second-fixture-value')
  vi.mocked(service.putPluginSecret).mockResolvedValueOnce({ plugin_id: 'demo', key: 'token', configured: true })
  await wrapper.get('.secret-row button').trigger('click')
  await flushPromises()
  expect(service.putPluginSecret).toHaveBeenLastCalledWith('demo', 'token', 'second-fixture-value')
  expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
})

it('retains a secret draft on failure and allows retry', async () => {
  vi.mocked(service.getPluginSettings).mockResolvedValue(secretSchema())
  vi.mocked(service.putPluginSecret).mockRejectedValueOnce(new Error('Save failed'))
  wrapper = mount(PluginSettingsPanel, { props: { pluginId: 'demo' } })
  await flushPromises()
  await wrapper.get('input[type="password"]').setValue('retry-fixture-value')
  await wrapper.get('.secret-row button').trigger('click')
  await flushPromises()
  expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('retry-fixture-value')
  expect(wrapper.get('.secret-row button').attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).toContain('Save failed')
})

it.each(['save', 'delete'] as const)('ignores old secret %s responses after switching plugins', async action => {
  vi.mocked(service.getPluginSettings).mockResolvedValue(secretSchema(true))
  let finish!: () => void
  vi.mocked(service.putPluginSecret).mockReturnValue(new Promise(resolve => { finish = () => resolve({ plugin_id: 'demo', key: 'token', configured: true }) }))
  if (action === 'delete') {

    vi.mocked(service.deletePluginSecret).mockReturnValue(new Promise(resolve => { finish = () => resolve({ plugin_id: 'demo', key: 'token', configured: false }) }))
  }
  wrapper = mount(PluginSettingsPanel, { props: { pluginId: 'demo' } })
  await flushPromises()
  await wrapper.get('input[type="password"]').setValue('old-fixture-value')
  await wrapper.get(action === 'save' ? '.secret-row button' : '.secret-row .danger').trigger('click')
  if (action === 'delete') {
    await wrapper.get('.action-dialog').trigger('submit')
    await flushPromises()
  }
  vi.mocked(service.getPluginSettings).mockResolvedValue(secretSchema(false))
  await wrapper.setProps({ pluginId: 'other' })
  await flushPromises()
  await wrapper.get('input[type="password"]').setValue('new-fixture-value')
  finish()
  await flushPromises()
  expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('new-fixture-value')
  expect(wrapper.find('.secret-status').classes()).toContain('not-configured')
  expect(wrapper.emitted('saved')).toBeUndefined()
  vi.restoreAllMocks()
})

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
