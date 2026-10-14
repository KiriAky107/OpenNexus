// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SearchView from './SearchView.vue'
import { useSearchStore } from '@/stores/search'
import type { SearchResult } from '@/contracts'

const openCitation = vi.hoisted(() => vi.fn())
vi.mock('@/composables/useCitationNavigation', () => ({ useCitationNavigation: () => ({ openCitation }) }))
vi.mock('@/services/searchService', () => ({
  search: vi.fn(),
  getHistory: vi.fn().mockResolvedValue({ queries: [] }),
  clearHistory: vi.fn().mockResolvedValue({ queries: [] }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  openCitation.mockReset()
})

it('clears a failed result navigation when a new search starts', async () => {
  const search = useSearchStore()
  const result: SearchResult = {
    note_id: 'old-note', note_title: 'Old result', file_path: '/old.md',
    block_id: 'old-block', heading_path: 'Old heading', snippet: 'Old snippet',
    score: 1, match_type: 'fts',
  }
  search.query = 'old query'
  search.results = [result]
  search.total = 1
  openCitation.mockRejectedValueOnce(new Error('The old result is no longer available.'))
  const doSearch = vi.spyOn(search, 'doSearch').mockResolvedValue()
  const wrapper = mount(SearchView)

  await wrapper.get('.result-card').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('old result')

  search.query = 'new query'
  await wrapper.get('form').trigger('submit')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(doSearch).toHaveBeenCalledWith({ query: 'new query', mode: 'hybrid', folder: undefined, tag: undefined })
})
