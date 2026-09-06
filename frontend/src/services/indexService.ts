import apiClient from './apiClient'
import type { ApiIndexJob, ApiIndexStatus, IndexStatus } from '@/contracts'

function toIndexStatus(status: ApiIndexStatus): IndexStatus {
  return {
    running_jobs: status.running_jobs,
    active_searches: status.active_searches,
    completed_searches: status.completed_searches,
    failed_searches: status.failed_searches,
    cancelled_searches: status.cancelled_searches,
    vector_refresh_required: status.vector_refresh_required ?? false,
    status: status.status === 'idle' ? 'idle' : status.status === 'failed' ? 'error' : 'indexing',
    pending_jobs: status.pending_jobs,
    total_notes: status.total_notes ?? null,
    total_blocks: status.total_blocks ?? null,
    last_indexed_at: status.last_completed_at ?? undefined,
    error: status.error_message ?? undefined,
  }
}

export async function getIndexStatus(): Promise<IndexStatus> {
  return toIndexStatus(await apiClient.get<ApiIndexStatus>('/api/index/status', { timeoutMs: 10000 }))
}

export async function rebuildIndex(scope: 'full' | 'fts' | 'vector' = 'full'): Promise<ApiIndexJob> {
  const apiScope = scope === 'full' ? 'all' : scope === 'fts' ? 'notes' : 'vectors'
  return apiClient.post<ApiIndexJob>('/api/index/rebuild', { scope: apiScope })
}

export async function getIndexJob(jobId: string): Promise<ApiIndexJob> {
  return apiClient.get(`/api/index/jobs/${jobId}`)
}
