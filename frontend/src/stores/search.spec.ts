// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSearchStore } from './search'
import { search } from '@/services/searchService'

vi.mock('@/services/searchService', () => ({ search: vi.fn() }))
beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.mocked(search).mockReset().mockResolvedValue({ results: [], total: 0, mode: 'hybrid' })
})

it('persists real queries across store recreation, reorders duplicates and clears history', async () => {
  const store = useSearchStore()
  expect(store.recentQueries).toEqual([])
  await store.doSearch({ query: ' first ' })
  await store.doSearch({ query: 'second' })
  await store.doSearch({ query: 'first' })
  setActivePinia(createPinia())
  const restored = useSearchStore()
  expect(restored.recentQueries).toEqual(['first', 'second'])
  restored.clearHistory()
  setActivePinia(createPinia())
  expect(useSearchStore().recentQueries).toEqual([])
})

it('ignores corrupt storage and does not let a stale request overwrite the latest search', async () => {
  localStorage.setItem('notes-agent.search-history.v1', '{bad')
  let finish!: (value: Awaited<ReturnType<typeof search>>) => void
  vi.mocked(search).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const store = useSearchStore()
  const first = store.doSearch({ query: 'old' })
  await store.doSearch({ query: 'new' })
  finish({ results: [], total: 99, mode: 'hybrid' })
  await first
  expect(store.total).toBe(0)
  expect(store.query).toBe('new')
  expect(store.recentQueries).toEqual(['new', 'old'])
})
