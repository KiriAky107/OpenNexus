// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from './SettingsView.vue'
import { useProviderStore } from '@/stores/provider'
import { useSettingsStore } from '@/stores/settings'

beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
it('separates unfinished indexing from retrieval activity and retains short search totals', async () => {
  const store = useSettingsStore()
  vi.spyOn(store, 'loadDiagnostics').mockResolvedValue()
  const providers = useProviderStore()
  vi.spyOn(providers, 'loadProviders').mockResolvedValue()
  vi.spyOn(providers, 'loadPresets').mockResolvedValue()
  vi.spyOn(providers, 'refreshEnabledModels').mockResolvedValue()
  const wrapper = mount(SettingsView, { global: { stubs: { UsageCard: true, LocalModelSettings: true, ModelRoutingSettings: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '索引与模型')!.trigger('click')
  expect(wrapper.text()).toContain('未完成索引 未获取')
  store.indexStatus = { status: 'idle', pending_jobs: 1, running_jobs: 0, total_notes: 16, total_blocks: 3787, vector_refresh_required: true,
    active_searches: 0, completed_searches: 2, failed_searches: 1, cancelled_searches: 0 }
  await flushPromises()
  expect(wrapper.text()).toContain('全文可用 · 向量待重建')
  expect(wrapper.text()).toContain('未完成索引 1')
  expect(wrapper.text()).toContain('已完成 2')
  expect(wrapper.text()).toContain('失败 1')
  store.indexStatus.status = 'indexing'
  store.indexStatus.running_jobs = 1
  await flushPromises()
  expect(wrapper.text()).toContain('后台计算索引')
  expect(wrapper.findAll('button').find(button => button.text() === '重建全部')!.attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
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
