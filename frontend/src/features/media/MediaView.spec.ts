// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useProviderStore } from '@/stores/provider'
import { mediaService } from '@/services/mediaService'
import MediaView from './MediaView.vue'

const openCitation = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
vi.mock('@/composables/useCitationNavigation', () => ({ useCitationNavigation: () => ({ openCitation }) }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))
vi.mock('@/services/mediaService', () => ({
  createMediaSubmission: () => ({ submit: vi.fn(), reset: vi.fn() }),
  mediaService: { list: vi.fn(), getArtifacts: vi.fn(), artifacts: vi.fn(), audio: () => '', save: vi.fn() },
}))
const job = { job_id: 'transcription_internalhash', attachment_id: 'media_internalhash', filename: '双指针课程.mp3', status: 'completed' as const,
  text: '双指针', original_text: '双指针', segments: [], speaker_names: {}, revision: 1, created_at: '2026-09-23T00:00:00Z',
  progress: 1, error_code: null, error_message: null, warnings: [], source: 'local', fallback_reason: null, local_only: true }
const transcript = { note_id: 'note_a', title: '双指针课程 · 转录稿', file_path: '/双指针课程 · 转录稿.md' }
const knowledge = { note_id: 'note_b', title: '双指针课程 · 知识点', file_path: '/双指针课程 · 知识点.md' }

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  const providers = useProviderStore()
  providers.providers = [{ provider_id: 'local', provider_type: 'ollama', name: 'Local', default_model: 'model', enabled: true, capabilities: {chat:true}, has_credential:false }]
  providers.defaultProviderId = 'local'
  vi.spyOn(providers, 'loadProviders').mockResolvedValue(undefined)
  vi.spyOn(providers, 'loadModels').mockResolvedValue([])
  vi.mocked(mediaService.list).mockResolvedValue({ items: [job] })
  vi.mocked(mediaService.getArtifacts).mockResolvedValue({ transcript: null, knowledge_note: null })
})

it('shows readable source names and opens both persisted course artifacts', async () => {
  vi.mocked(mediaService.getArtifacts).mockResolvedValue({ transcript, knowledge_note: knowledge })
  const wrapper = mount(MediaView)
  try {
    await flushPromises()
    expect(wrapper.text()).not.toContain('internalhash')
    await wrapper.get('.job-row').trigger('click')
    await flushPromises()
    expect(wrapper.get('.import-panel').attributes('open')).toBeUndefined()
    await wrapper.findAll('.result-tabs button')[1]!.trigger('click')
    const cards = wrapper.findAll('.material-result')
    expect(cards).toHaveLength(2)
    expect(cards[0]!.text()).toContain('已保存')
    await cards[1]!.get('button').trigger('click')
    expect(openCitation).toHaveBeenCalledWith(knowledge)
  } finally { wrapper.unmount() }
})

it('retains a successfully saved transcript when knowledge extraction fails', async () => {
  const wrapper = mount(MediaView)
  try {
    await flushPromises()
    await wrapper.get('.job-row').trigger('click')
    await flushPromises()
    await wrapper.findAll('.result-tabs button')[1]!.trigger('click')
    vi.mocked(mediaService.artifacts).mockRejectedValue(new Error('Model unavailable'))
    vi.mocked(mediaService.getArtifacts).mockResolvedValue({ transcript, knowledge_note: null })
    await wrapper.get('.artifact-panel button.button-primary').trigger('click')
    await flushPromises()
    expect(wrapper.get('.artifact-panel .error-banner').text()).toContain('Model unavailable')
    expect(wrapper.findAll('.material-result')[0]!.text()).toContain('已保存')
    expect(wrapper.findAll('.material-result')[1]!.text()).toContain('尚未生成')
  } finally { wrapper.unmount() }
})
