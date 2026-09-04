import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SearchResult, SearchRequest } from '@/contracts'
import * as searchService from '@/services/searchService'
import { ApiErrorClass } from '@/services/apiClient'

const VECTOR_ERROR_CODES = new Set([
  'SEMANTIC_INDEX_UNAVAILABLE',
  'VECTOR_UNAVAILABLE', 'EMBEDDING_UNAVAILABLE', 'INDEX_UNAVAILABLE',
  'MODEL_NOT_FOUND', 'MODEL_CAPABILITY_MISMATCH', 'PROVIDER_UNAVAILABLE',
])

export const useSearchStore = defineStore('search', () => {
  const query = ref('')
  const mode = ref<'fts' | 'vector' | 'hybrid'>('hybrid')
  const results = ref<SearchResult[]>([])
  const total = ref(0)
  const isSearching = ref(false)
  const selectedIndex = ref(0)
  const recentQueries = ref<string[]>([])
  const historyError = ref('')
  let searchVersion = 0
  let historyVersion = 0
  async function loadHistory() {
    const version = ++historyVersion
    try {
      const response = await searchService.getHistory()
      if (version !== historyVersion) return
      recentQueries.value = response.queries
      historyError.value = ''
    } catch { if (version === historyVersion) historyError.value = '无法读取应用搜索记录，请检查后端连接。' }
  }
  async function clearHistory() {
    const version = ++historyVersion
    try {
      await searchService.clearHistory()
      if (version !== historyVersion) return
      recentQueries.value = []; historyError.value = ''
    } catch { if (version === historyVersion) historyError.value = '清空搜索记录失败，请重试。' }
  }
  const error = ref<string | null>(null)
  const vectorUnavailable = ref(false)

  async function doSearch(request: SearchRequest) {
    request = { ...request, query: request.query.trim() }
    if (!request.query) return
    const version = ++searchVersion
    query.value = request.query
    mode.value = request.mode || 'hybrid'
    isSearching.value = true
    error.value = null
    vectorUnavailable.value = false

    try {
      const resp = await searchService.search(request)
      if (version !== searchVersion) return
      results.value = resp.results
      total.value = resp.total
      selectedIndex.value = 0
    } catch (reason) {
      if (version !== searchVersion) return
      const canFallback = mode.value !== 'fts' && reason instanceof ApiErrorClass && VECTOR_ERROR_CODES.has(reason.code)
      if (canFallback) {
        try {
          const fallback = await searchService.search({ ...request, mode: 'fts' })
          if (version !== searchVersion) return
          results.value = fallback.results
          total.value = fallback.total
          mode.value = 'fts'
          vectorUnavailable.value = true
          selectedIndex.value = 0
        } catch (fallbackError) {
          if (version !== searchVersion) return
          error.value = fallbackError instanceof Error ? fallbackError.message : '全文检索降级失败'
          results.value = []
          total.value = 0
        }
      } else {
        error.value = reason instanceof Error ? reason.message : '搜索失败'
        results.value = []
        total.value = 0
      }
    } finally {
      if (version === searchVersion) { isSearching.value = false; await loadHistory() }
    }

  }

  function clearResults() {
    searchVersion++
    isSearching.value = false
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
    historyError,
    clearHistory,
    loadHistory,
    error,
    vectorUnavailable,
    doSearch,
    clearResults,
    selectNext,
    selectPrev,
    setMode,
  }
})
