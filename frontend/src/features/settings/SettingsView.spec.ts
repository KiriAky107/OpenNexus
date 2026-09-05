// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from './SettingsView.vue'
import { useProviderStore } from '@/stores/provider'
import { useSettingsStore } from '@/stores/settings'

beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
it('shows provider status and enables testing only after a successful enable', async () => {
  const store = useProviderStore()
  store.providers = [{ provider_id: 'p1', name: 'Example', provider_type: 'openai_compatible', enabled: false, default_model: '', capabilities: {}, has_credential: false }]
  vi.spyOn(store, 'loadProviders').mockResolvedValue()
  vi.spyOn(store, 'loadPresets').mockResolvedValue()
  vi.spyOn(store, 'refreshEnabledModels').mockResolvedValue()
  vi.spyOn(useSettingsStore(), 'loadDiagnostics').mockResolvedValue()
  const update = vi.spyOn(store, 'updateProvider').mockImplementation(async (_id, data) => {
    store.providers[0] = { ...store.providers[0]!, ...data }
    return store.providers[0]!
  })
  const wrapper = mount(SettingsView, { global: { stubs: { UsageCard: true, LocalModelSettings: true, ModelRoutingSettings: true, ProviderLogo: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '模型提供商')!.trigger('click')
  expect(wrapper.text()).toContain('已停用')
  const test = () => wrapper.findAll('button').find(button => button.text() === '测试')!
  expect(test().attributes('disabled')).toBeDefined()
  await wrapper.findAll('button').find(button => button.text() === '启用')!.trigger('click')
  await flushPromises()
  expect(update).toHaveBeenCalledWith('p1', { enabled: true })
  expect(wrapper.text()).toContain('已启用')
  expect(test().attributes('disabled')).toBeUndefined()
  update.mockRejectedValueOnce(new Error('保存失败'))
  await wrapper.findAll('button').find(button => button.text() === '停用')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('已启用')
  expect(wrapper.text()).toContain('保存失败')
  wrapper.unmount()
})
