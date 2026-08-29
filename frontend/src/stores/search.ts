import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SearchResult, SearchRequest } from '@/contracts'
import * as searchService from '@/services/searchService'

export const useSearchStore = defineStore('search', () => {
  const query = ref('')
  const mode = ref<'fts' | 'vector' | 'hybrid'>('hybrid')
  const results = ref<SearchResult[]>([])
  const total = ref(0)
  const isSearching = ref(false)
  const selectedIndex = ref(0)
  const recentQueries = ref<string[]>(['红黑树', '死锁', 'TCP三次握手'])
  const error = ref<string | null>(null)
  const vectorUnavailable = ref(false)

  async function doSearch(request: SearchRequest) {
    query.value = request.query
    mode.value = request.mode || 'hybrid'
    isSearching.value = true
    error.value = null

    try {
      const resp = await searchService.searchMock(request.query, request.mode || 'hybrid')
      results.value = resp.results
      total.value = resp.total
      selectedIndex.value = 0
    } catch (e: any) {
      error.value = e.message || '搜索失败'
      results.value = []
      total.value = 0
    } finally {
      isSearching.value = false
    }

    if (request.query && !recentQueries.value.includes(request.query)) {
      recentQueries.value.unshift(request.query)
      if (recentQueries.value.length > 10) recentQueries.value.pop()
    }
  }

  function clearResults() {
    results.value = []
    query.value = ''
    total.value = 0
    selectedIndex.value = 0
    error.value = null
  }

  function selectNext() {
    if (selectedIndex.value < results.value.length - 1) selectedIndex.value++
  }

  function selectPrev() {
    if (selectedIndex.value > 0) selectedIndex.value--
  }

  function setMode(m: 'fts' | 'vector' | 'hybrid') {
    mode.value = m
  }

  return {
    query,
    mode,
    results,
    total,
    isSearching,
    selectedIndex,
    recentQueries,
    error,
    vectorUnavailable,
    doSearch,
    clearResults,
    selectNext,
    selectPrev,
    setMode,
  }
})
