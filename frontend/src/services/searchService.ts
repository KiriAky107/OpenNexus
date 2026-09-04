import apiClient from './apiClient'
import type { ApiSearchResult, PageMeta, SearchRequest, SearchResult } from '@/contracts'

export function getHistory() { return apiClient.get<{ queries: string[] }>('/api/search/history') }
export function clearHistory() { return apiClient.delete<{ queries: string[] }>('/api/search/history') }

export async function search(request: SearchRequest): Promise<{
  results: SearchResult[]
  total: number
  mode: SearchRequest['mode']
}> {
  const response = await apiClient.post<{
    query: string
    mode: 'fts' | 'vector' | 'hybrid'
    items: ApiSearchResult[]
    page: PageMeta
  }>('/api/search', {
    query: request.query,
    mode: request.mode ?? 'hybrid',
    folders: request.folder ? [request.folder] : [],
    note_ids: request.note_id ? [request.note_id] : [],
    tags: request.tag ? [request.tag] : [],
    limit: request.limit ?? 20,
    offset: request.offset ?? 0,
  })
  return {
    results: response.items.map((item) => ({
      block_id: item.block_id,
      note_id: item.note_id,
      note_title: item.title,
      file_path: item.file_path,
      heading_path: item.heading_path.join(' / '),
      snippet: item.snippet ?? '',
      score: item.score,
      match_type: response.mode,
    })),
    total: response.page.total,
    mode: response.mode,
  }
}
