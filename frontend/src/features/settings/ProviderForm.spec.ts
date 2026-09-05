// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProviderConfig, ProviderPreset } from '@/contracts'
import * as service from '@/services/providerService'
import ProviderForm from './ProviderForm.vue'
import ProviderPresetSelector from './ProviderPresetSelector.vue'
import RequestJsonEditor from './RequestJsonEditor.vue'
import { apiClient } from '@/services/apiClient'

vi.mock('@/services/providerService', () => ({ listProviderPresets: vi.fn(), getCredentialStatus: vi.fn(), putCredential: vi.fn(), createProvider: vi.fn(), updateProvider: vi.fn() }))
vi.mock('@/services/apiClient', () => ({ apiClient: { post: vi.fn() } }))
const presets: ProviderPreset[] = [
  { preset_id: 'deepseek', name: 'DeepSeek', provider_type: 'openai_compatible', base_url: 'https://deepseek.example.test', default_credential_id: 'shared-deepseek', requires_credential: true, logo_id: 'deepseek' },
  { preset_id: 'qwen', name: '通义千问', provider_type: 'openai_compatible', base_url: 'https://qwen.example.test', default_credential_id: 'shared-qwen', requires_credential: true, logo_id: 'qwen' },
]
const existing: ProviderConfig = { provider_id: 'p1', provider_type: 'openai_compatible', name: 'DeepSeek', base_url: presets[0].base_url, default_model: 'old-model', enabled: true, credential_id: 'old-shared-key', has_credential: true, capabilities: {} }
const wrappers: ReturnType<typeof mount>[] = []
async function render(provider?: ProviderConfig) {
  const wrapper = mount(ProviderForm, { props: { provider, models: [{ model_id: 'old-model', name: 'Old', capabilities: {} }] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(service.listProviderPresets).mockResolvedValue(presets)
  vi.mocked(service.getCredentialStatus).mockResolvedValue(true)
  vi.mocked(service.putCredential).mockResolvedValue()
  vi.mocked(service.createProvider).mockResolvedValue(existing)
  vi.mocked(service.updateProvider).mockResolvedValue(existing)
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('ProviderForm', () => {
  it('saves model-scoped context settings and restores them on edit', async () => {
    const policy = {model:'old-model',context_window:65536,output_reserve:8192,threshold:0.8,mode:'detect' as const,prompt:'保留已确认事实'}
    const wrapper = await render({...existing,context_policies:[policy]})
    expect(wrapper.get('textarea').element.value).toBe(policy.prompt)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.updateProvider).toHaveBeenCalledWith('p1', expect.objectContaining({context_policies:[policy]}))
  })

  it('does not reuse another endpoint context settings', async () => {
    const wrapper = await render({...existing,context_policies:[{model:'old-model',context_window:65536,output_reserve:8192,threshold:0.8,mode:'detect',prompt:'摘要'}]})
    await wrapper.get('[data-field="base-url"]').setValue('https://new.example.test/v1')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.updateProvider).toHaveBeenCalledWith('p1', expect.objectContaining({context_policies:[]}))
  })
  it('invalidates a pending inference result when JSON becomes invalid', async () => {
    const wrapper = await render(existing)
    let finish!: (value: {message: string}) => void
    vi.mocked(apiClient.post).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const probe = wrapper.findAll('button').find(button => button.text() === '发送测试推理请求')!
    await probe.trigger('click')
    expect(apiClient.post).toHaveBeenCalledWith('/api/providers/request-probe', expect.objectContaining({stream:true}))
    wrapper.getComponent(RequestJsonEditor).vm.$emit('valid', false)
    await flushPromises()
    finish({message:'旧配置验证通过'})
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧配置验证通过')
    expect(probe.attributes('disabled')).toBeDefined()
  })

  it('filters compact preset chips and resolves bundled logos', async () => {
    const wrapper = await render()
    await wrapper.get('#provider-search').setValue('通义')
    expect(wrapper.find('[data-preset="deepseek"]').exists()).toBe(false)
    expect(wrapper.find('[data-preset="qwen"]').exists()).toBe(true)
    expect(wrapper.get('[data-preset="qwen"] img').attributes('src')).not.toMatch(/^https?:/)
  })

  it('clears the secret, old model and credential on preset and custom selection', async () => {
    const wrapper = await render(existing)
    await wrapper.get('input[type="password"]').setValue('draft-secret')
    await wrapper.get('[data-preset="qwen"]').trigger('click')
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('[data-field="model"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.findAll('datalist option')).toHaveLength(0)
    await wrapper.get('form').trigger('submit')
    expect(service.updateProvider).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请输入 API Key')
    await wrapper.get('input[type="password"]').setValue('new-secret')
    wrapper.getComponent(ProviderPresetSelector).vm.$emit('update:modelValue', '')
    await flushPromises()
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
  })

  it('allocates different credential IDs for two new providers using the same preset', async () => {
    for (let i = 0; i < 2; i++) {
      const wrapper = await render()
      await wrapper.get('[data-preset="deepseek"]').trigger('click')
      await wrapper.get('input[type="password"]').setValue(`test-key-${i}`)
      await wrapper.get('form').trigger('submit')
      await flushPromises()
    }
    const ids = vi.mocked(service.putCredential).mock.calls.map(call => call[0])
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)
    ids.forEach(id => expect(id).toMatch(/^provider-key-[0-9a-f-]{36}$/))
    vi.mocked(service.createProvider).mock.calls.forEach(([data], index) => {
      expect(data.credential_id).toBe(ids[index])
      expect(JSON.stringify(data)).not.toContain('test-key')
      expect(JSON.stringify(data)).not.toContain('shared-deepseek')
    })
  })

  it('accepts a custom API key and clears it after a failed credential save', async () => {
    const wrapper = await render()
    await wrapper.get('[data-field="name"]').setValue('Custom')
    await wrapper.get('[data-field="base-url"]').setValue('https://custom.example.test/v1')
    await wrapper.get('input[type="password"]').setValue('custom-test-key')
    vi.mocked(service.putCredential).mockRejectedValueOnce(new Error('credential store unavailable'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('credential store unavailable')
    expect(service.createProvider).not.toHaveBeenCalled()
    await wrapper.get('input[type="password"]').setValue('custom-test-key')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.createProvider).toHaveBeenCalledWith(expect.objectContaining({ name: 'Custom', credential_id: expect.stringMatching(/^provider-key-/) }))
  })

  it('preserves its own existing key when untouched and rotates shared legacy references when replacing a key', async () => {
    const untouched = await render(existing)
    await untouched.get('form').trigger('submit')
    await flushPromises()
    expect(service.updateProvider).toHaveBeenLastCalledWith('p1', expect.objectContaining({ credential_id: 'old-shared-key', default_model: 'old-model' }))
    const rotated = await render(existing)
    await rotated.get('input[type="password"]').setValue('replacement-test-key')
    await rotated.get('form').trigger('submit')
    await flushPromises()
    expect(service.putCredential).toHaveBeenCalledWith(expect.stringMatching(/^provider-key-/), 'replacement-test-key')
    expect(service.updateProvider).toHaveBeenLastCalledWith('p1', expect.objectContaining({ credential_id: vi.mocked(service.putCredential).mock.calls[0][0] }))
  })

  it('persists edited protocols and unlinks the previous credential and model', async () => {
    const wrapper = await render(existing)
    await wrapper.get('[data-field="protocol"]').setValue('openai_responses')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.updateProvider).toHaveBeenCalledWith('p1', expect.objectContaining({ provider_type: 'openai_responses', default_model: '', credential_id: null }))
  })

  it('does not let a late credential status reuse a key after switching presets', async () => {
    let resolveStatus!: (configured: boolean) => void
    vi.mocked(service.getCredentialStatus).mockReturnValue(new Promise(resolve => { resolveStatus = resolve }))
    const wrapper = await render(existing)
    await wrapper.get('[data-preset="qwen"]').trigger('click')
    resolveStatus(true)
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    expect(service.updateProvider).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请输入 API Key')
  })

  it('clears secrets on close and unmount, and stops a pending credential save from creating a provider', async () => {
    let finish!: () => void
    vi.mocked(service.putCredential).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const wrapper = await render()
    await wrapper.get('[data-preset="deepseek"]').trigger('click')
    await wrapper.get('input[type="password"]').setValue('pending-test-key')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('[aria-label="关闭提供商表单"]').trigger('click')
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
    finish()
    await flushPromises()
    expect(service.createProvider).not.toHaveBeenCalled()
    const reopened = await render()
    expect((reopened.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
  })

  it('clears a secret after provider save failure and keeps only the successfully saved reference for retry', async () => {
    const wrapper = await render()
    await wrapper.get('[data-preset="deepseek"]').trigger('click')
    await wrapper.get('input[type="password"]').setValue('retry-test-key')
    vi.mocked(service.createProvider).mockRejectedValueOnce(new Error('provider save failed'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('provider save failed')
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.putCredential).toHaveBeenCalledTimes(1)
    expect(service.createProvider).toHaveBeenCalledTimes(2)
  })
})
