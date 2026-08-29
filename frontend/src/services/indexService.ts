import apiClient from './apiClient'
import type { IndexStatus } from '@/contracts'

export async function getIndexStatus(): Promise<IndexStatus> {
  try {
    return await apiClient.get('/api/index/status')
  } catch {
    return mockIndexStatus
  }
}

export async function rebuildIndex(scope: 'full' | 'fts' | 'vector' = 'full'): Promise<{ job_id: string }> {
  return apiClient.post('/api/index/rebuild', { scope })
}

export async function getIndexJob(jobId: string): Promise<{
  job_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  total: number
  error?: string
}> {
  return apiClient.get(`/api/index/jobs/${jobId}`)
}

export const mockIndexStatus: IndexStatus = {
  status: 'idle',
  pending_jobs: 0,
  total_notes: 42,
  total_blocks: 318,
  fts_enabled: true,
  vector_enabled: true,
  embedding_model: 'bge-m3',
  reranker_model: 'bge-reranker-base',
  last_indexed_at: new Date().toISOString(),
}
