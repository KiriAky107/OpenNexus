import apiClient from './apiClient'
import type { ApiIndexJob, ApiIndexStatus, IndexStatus } from '@/contracts'

function toIndexStatus(status: ApiIndexStatus): IndexStatus {
  return {
    status: status.status === 'idle' ? 'idle' : status.status === 'failed' ? 'error' : 'indexing',
    pending_jobs: status.pending_jobs,
    total_notes: 0,
    total_blocks: 0,
    fts_enabled: true,
    vector_enabled: true,
    last_indexed_at: status.last_completed_at ?? undefined,
    error: status.error_message ?? undefined,
  }
}

export async function getIndexStatus(): Promise<IndexStatus> {
  return toIndexStatus(await apiClient.get<ApiIndexStatus>('/api/index/status'))
}

export async function rebuildIndex(scope: 'full' | 'fts' | 'vector' = 'full'): Promise<ApiIndexJob> {
  const apiScope = scope === 'full' ? 'all' : scope === 'fts' ? 'notes' : 'vectors'
  return apiClient.post<ApiIndexJob>('/api/index/rebuild', { scope: apiScope })
}

export async function getIndexJob(jobId: string): Promise<ApiIndexJob> {
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
