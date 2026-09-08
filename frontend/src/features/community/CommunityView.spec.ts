// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
const service = vi.hoisted(() => ({ discover: vi.fn(), review: vi.fn(), confirm: vi.fn(), save: vi.fn() }))
vi.mock('@/services/platform/desktop', () => ({ isDesktop: () => true }))
vi.mock('@/services/extensionTrustService', () => ({ reviewTrust: service.review, confirmTrust: service.confirm }))
vi.mock('@/services/communityService', () => ({ loadSources: () => [], saveSources: service.save, discoverSource: service.discover, cachedCatalog: vi.fn(), fetchCatalog: vi.fn(), installRelease: vi.fn() }))
import CommunityView from './CommunityView.vue'
beforeEach(() => { vi.clearAllMocks(); localStorage.clear() })
it('requires explicit confirmation and does not save when Host rejects it', async () => {
  service.discover.mockResolvedValue({ source_id: 'catalog', keys: [{ key_id: 'key', namespace: 'examples', public_key: 'public', revoked: false }] })
  service.review.mockResolvedValue([{ review_id: 'review', fingerprint: 'visible-digest', previous: null, proposed: { namespace: 'examples', key_id: 'key' } }])
  const wrapper = mount(CommunityView, { global: { stubs: { AppDialog: { template: '<section><slot /></section>' } } } })
  await wrapper.get('input').setValue('https://catalog.example')
  await wrapper.findAll('button').find(b => b.text() === '检查来源与公钥')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('visible-digest')
  expect(service.confirm).not.toHaveBeenCalled()
  expect(service.save).not.toHaveBeenCalled()
  service.confirm.mockRejectedValueOnce(new Error('确认已过期'))
  await wrapper.findAll('button').find(b => b.text() === '确认来源设置')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('确认已过期')
  expect(service.save).not.toHaveBeenCalled()
  wrapper.unmount()
})
