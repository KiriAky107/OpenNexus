import apiClient from './apiClient'
import type { ApiSearchResult, PageMeta, SearchRequest, SearchResult } from '@/contracts'

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

export async function searchMock(
  query: string,
  mode: 'fts' | 'vector' | 'hybrid' = 'hybrid'
): Promise<{
  results: SearchResult[]
  total: number
  mode: 'fts' | 'vector' | 'hybrid'
}> {
  await new Promise((r) => setTimeout(r, 300))
  if (!query.trim()) return { results: [], total: 0, mode }
  const results: SearchResult[] = [
    {
      block_id: 'b1',
      note_id: 'n-rbt',
      note_title: '红黑树',
      file_path: '/数据结构/红黑树.md',
      heading_path: '数据结构 / 红黑树 / 插入操作',
      snippet: '插入后可能破坏红黑性质，需要通过变色和旋转来修复...',
      score: 0.95,
      match_type: 'hybrid',
      tags: ['数据结构', '树'],
    },
    {
      block_id: 'b2',
      note_id: 'n-rbt',
      note_title: '红黑树',
      file_path: '/数据结构/红黑树.md',
      heading_path: '数据结构 / 红黑树 / 性质',
      snippet: '红黑树是一种自平衡二叉搜索树，每个节点带有颜色属性（红或黑）...',
      score: 0.87,
      match_type: 'fts',
      tags: ['数据结构'],
    },
    {
      block_id: 'b3',
      note_id: 'n-bst',
      note_title: '二叉搜索树',
      file_path: '/数据结构/二叉搜索树.md',
      heading_path: '数据结构 / 二叉搜索树 / 基本操作',
      snippet: '二叉搜索树的插入需要先找到合适的位置，再添加新节点...',
      score: 0.72,
      match_type: 'vector',
      tags: ['数据结构', '树'],
    },
    {
      block_id: 'b4',
      note_id: 'n-deadlock',
      note_title: '死锁',
      file_path: '/操作系统/死锁.md',
      heading_path: '操作系统 / 死锁 / 必要条件',
      snippet: '死锁的四个必要条件：互斥、占有并等待、不可抢占、循环等待...',
      score: 0.45,
      match_type: 'vector',
      tags: ['操作系统'],
    },
  ]
  const filtered = results.filter(
    (r) =>
      r.note_title.includes(query) ||
      r.snippet.includes(query) ||
      r.heading_path.includes(query) ||
      query.length > 1
  )
  return { results: filtered, total: filtered.length, mode }
}
