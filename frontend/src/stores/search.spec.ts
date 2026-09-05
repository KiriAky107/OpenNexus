import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSearchStore } from './search'
import * as service from '@/services/searchService'
vi.mock('@/services/searchService', () => ({ search: vi.fn(), getHistory: vi.fn(), clearHistory: vi.fn() }))
beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(service.getHistory).mockReset().mockResolvedValue({ queries: ['saved'] })
  vi.mocked(service.clearHistory).mockReset().mockResolvedValue({ queries: [] })
  vi.mocked(service.search).mockReset().mockResolvedValue({ results: [], total: 0, mode: 'hybrid' })
})
it('loads application history after recreation and clears through the backend', async () => {
  await useSearchStore().loadHistory()
  setActivePinia(createPinia())
  const store = useSearchStore()
  await store.loadHistory()
  expect(store.recentQueries).toEqual(['saved'])
  await store.clearHistory()
  expect(service.clearHistory).toHaveBeenCalledOnce()
  expect(store.recentQueries).toEqual([])
})
it('retains history and reports a failed delete', async () => {
  const store = useSearchStore()
  await store.loadHistory()
  vi.mocked(service.clearHistory).mockRejectedValue(new Error('offline'))
  await store.clearHistory()
  expect(store.recentQueries).toEqual(['saved'])
  expect(store.historyError).toBeTruthy()
})
it('ignores stale search responses and reloads server history', async () => {
  let finish!: (value: Awaited<ReturnType<typeof service.search>>) => void
  vi.mocked(service.search).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const store = useSearchStore()
  const first = store.doSearch({ query: 'old' })
  await store.doSearch({ query: 'new' })
  finish({ results: [], total: 99, mode: 'hybrid' })
  await first
  expect(store.total).toBe(0)
  expect(store.query).toBe('new')
  expect(store.recentQueries).toEqual(['saved'])
})
