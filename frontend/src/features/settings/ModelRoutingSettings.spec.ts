// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelRoutingResponse, ProviderConfig } from '@/contracts'
import { ApiErrorClass } from '@/services/apiClient'
import * as service from '@/services/modelRoutingService'
import { listProviders } from '@/services/providerService'
import ModelRoutingSettings from './ModelRoutingSettings.vue'

vi.mock('@/services/modelRoutingService', () => ({ getModelRouting: vi.fn(), saveModelRouting: vi.fn() }))
vi.mock('@/services/providerService', () => ({ listProviders: vi.fn() }))
const providers: ProviderConfig[] = [
  { provider_id: 'p1', provider_type: 'openai_compatible', name: 'Custom API', enabled: true, default_model: 'chat-model', capabilities: {}, has_credential: true },
  { provider_id: 'p2', provider_type: 'openai_chat', name: 'OpenAI', enabled: true, default_model: '', capabilities: {}, has_credential: true },
  { provider_id: 'responses', provider_type: 'openai_responses', name: 'Responses', enabled: true, default_model: '', capabilities: {}, has_credential: true },
  { provider_id: 'anthropic', provider_type: 'anthropic_messages', name: 'Anthropic', enabled: true, default_model: '', capabilities: {}, has_credential: true },
  { provider_id: 'ollama', provider_type: 'ollama', name: 'Ollama', enabled: true, default_model: '', capabilities: {}, has_credential: false },
  { provider_id: 'disabled', provider_type: 'openai_chat', name: 'Disabled', enabled: false, default_model: '', capabilities: {}, has_credential: true },
]
const initial: ModelRoutingResponse = { config: { version: 3, embedding: null, transcription: null, speaker_matching: null }, local_backends: [
  { capability: 'embedding', status: 'placeholder', message: 'hash fallback' },
  { capability: 'transcription', status: 'not_installed', message: 'ASR not installed' },
  { capability: 'speaker_matching', status: 'not_installed', message: 'speaker not installed' },
] }
const wrappers: ReturnType<typeof mount>[] = []
async function render() {
  const wrapper = mount(ModelRoutingSettings)
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(service.getModelRouting).mockResolvedValue(structuredClone(initial))
  vi.mocked(listProviders).mockResolvedValue(providers)
  vi.mocked(service.saveModelRouting).mockImplementation(async config => ({ ...initial, config: { ...config, version: config.version + 1 } }))
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('ModelRoutingSettings', () => {
  it('loads local selections honestly, explains index rebuilds, and disables incompatible providers', async () => {
    const wrapper = await render()
    expect(wrapper.findAll('select').map(select => (select.element as HTMLSelectElement).value)).toEqual(['', '', ''])
    expect(wrapper.text()).toContain('本地支持 Bekko / Granite')
    expect(wrapper.text()).toContain('本地采用 Qwen3-ASR')
    expect(wrapper.text()).toContain('本地采用 ERes2NetV2')
    expect(wrapper.text()).toContain('重建全部')
    expect(wrapper.text()).toContain('重建完成前继续使用本地检索')
    expect(wrapper.text()).toContain('不是 OpenAI 标准接口')
    for (const id of ['responses', 'anthropic', 'ollama', 'disabled']) expect(wrapper.get(`option[value="${id}"]`).attributes()).toHaveProperty('disabled')
    expect(wrapper.get('option[value="p1"]').attributes()).not.toHaveProperty('disabled')
  })

  it('saves all three independent bindings with expected version and remote dimensions', async () => {
    const wrapper = await render()
    for (const capability of ['embedding', 'transcription', 'speaker_matching']) {
      const card = wrapper.get(`[data-capability="${capability}"]`)
      await card.get('select').setValue('p1')
      await card.get('[data-field="model"]').setValue(`${capability}-model`)
    }
    await wrapper.get('[data-field="dimensions"]').setValue('3072')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.saveModelRouting).toHaveBeenCalledWith({ version: 3,
      embedding: { provider_id: 'p1', model: 'embedding-model', endpoint: '/embeddings', dimensions: 3072 },
      transcription: { provider_id: 'p1', model: 'transcription-model', endpoint: '/audio/transcriptions' },
      speaker_matching: { provider_id: 'p1', model: 'speaker_matching-model', endpoint: '/audio/speaker-matches' },
    })
    expect(wrapper.text()).toContain('配置版本 4')
    expect(wrapper.text()).toContain('模型路由已保存')
    await wrapper.get('[data-capability="embedding"] select').setValue('')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.saveModelRouting).toHaveBeenLastCalledWith(expect.objectContaining({ version: 4, embedding: null }))
  })

  it('supports omitted dimensions and clears stale models/endpoints when switching providers', async () => {
    const wrapper = await render()
    const card = wrapper.get('[data-capability="embedding"]')
    await card.get('select').setValue('p1')
    await card.get('[data-field="model"]').setValue('custom-embedding')
    await card.get('[data-field="endpoint"]').setValue('/custom/embeddings')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.saveModelRouting).toHaveBeenCalledWith(expect.objectContaining({ embedding: { provider_id: 'p1', model: 'custom-embedding', endpoint: '/custom/embeddings', dimensions: null } }))
    await card.get('select').setValue('p2')
    expect((card.get('[data-field="model"]').element as HTMLInputElement).value).toBe('')
    expect((card.get('[data-field="endpoint"]').element as HTMLInputElement).value).toBe('/embeddings')
  })

  it('shows a loading state and does not offer a default local configuration after load failure', async () => {
    let fail!: (error: Error) => void
    vi.mocked(service.getModelRouting).mockReturnValueOnce(new Promise((_, reject) => { fail = reject }))
    const wrapper = await render()
    expect(wrapper.text()).toContain('正在加载模型路由')
    expect(wrapper.find('form').exists()).toBe(false)
    fail(new Error('offline'))
    await flushPromises()
    expect(wrapper.text()).toContain('加载失败：offline')
    expect(wrapper.find('form').exists()).toBe(false)
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('keeps unsaved input on save failure and retries without inventing a new version', async () => {
    const wrapper = await render()
    const card = wrapper.get('[data-capability="transcription"]')
    await card.get('select').setValue('p1')
    await card.get('[data-field="model"]').setValue('asr-model')
    vi.mocked(service.saveModelRouting).mockRejectedValueOnce(new Error('disk full'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('保存失败：disk full')
    expect((card.get('[data-field="model"]').element as HTMLInputElement).value).toBe('asr-model')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.saveModelRouting).toHaveBeenLastCalledWith(expect.objectContaining({ version: 3 }))
  })

  it('blocks overwrite after a conflict until explicitly reloading the latest configuration', async () => {
    const wrapper = await render()
    vi.mocked(service.saveModelRouting).mockRejectedValueOnce(new ApiErrorClass('MODEL_ROUTING_VERSION_CONFLICT', 'stale'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('配置版本冲突')
    expect(wrapper.get('button[type="submit"]').attributes()).toHaveProperty('disabled')
    await wrapper.get('form').trigger('submit')
    expect(service.saveModelRouting).toHaveBeenCalledTimes(1)
    vi.mocked(service.getModelRouting).mockResolvedValueOnce({ ...initial, config: { ...initial.config, version: 8 } })
    await wrapper.findAll('button').find(button => button.text().includes('放弃当前输入'))!.trigger('click')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.saveModelRouting).toHaveBeenLastCalledWith(expect.objectContaining({ version: 8 }))
  })

  it('prevents invalid dimensions, endpoints, and missing provider bindings from being saved', async () => {
    vi.mocked(service.getModelRouting).mockResolvedValueOnce({ ...initial, config: { ...initial.config, embedding: { provider_id: 'missing', model: 'old-model', endpoint: '/embeddings' } } })
    const wrapper = await render()
    expect(wrapper.text()).toContain('原提供商已不可用')
    await wrapper.get('form').trigger('submit')
    expect(service.saveModelRouting).not.toHaveBeenCalled()
    const card = wrapper.get('[data-capability="embedding"]')
    await card.get('select').setValue('p1')
    await card.get('[data-field="model"]').setValue('embedding-model')
    await card.get('[data-field="dimensions"]').setValue('1.5')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.text()).toContain('嵌入维度必须为 1–16384 的整数')
    await card.get('[data-field="dimensions"]').setValue('16385')
    await wrapper.get('form').trigger('submit')
    expect(service.saveModelRouting).not.toHaveBeenCalled()
    expect(card.get('[data-field="dimensions"]').attributes('max')).toBe('16384')
    await card.get('[data-field="dimensions"]').setValue('')
    await card.get('[data-field="endpoint"]').setValue('https://example.test/embeddings')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.text()).toContain('Endpoint 必须是以 / 开头的相对路径')
    expect(service.saveModelRouting).not.toHaveBeenCalled()
  })

  it('labels injected ready local backends accurately', async () => {
    vi.mocked(service.getModelRouting).mockResolvedValueOnce({ ...initial, local_backends: [{ capability: 'transcription', status: 'ready', message: 'Local ASR ready' }] })
    const wrapper = await render()
    const card = wrapper.get('[data-capability="transcription"]')
    expect(card.get('option[value=""]').text()).toBe('本地 · 已安装')
    expect(card.text()).toContain('本地后端已就绪')
    expect(card.text()).toContain('Local ASR ready')
    expect(card.text()).not.toContain('真实本地 ASR 尚未接入')
  })
})
