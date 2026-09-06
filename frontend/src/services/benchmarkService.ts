import { apiClient } from './apiClient'
interface RunWire { run_id: string; kind: 'rag' | 'agent'; dataset_id: string; status: string; progress: number | null; config_snapshot: Record<string, unknown>; error_code: string | null }
export interface BenchmarkRun { id: string; kind: 'rag' | 'agent'; datasetId: string; status: string; progress: number | null; agentId?: string; errorCode: string | null }
const map = (r: RunWire): BenchmarkRun => ({ id: r.run_id, kind: r.kind, datasetId: r.dataset_id, status: r.status, progress: r.progress, agentId: r.config_snapshot.active_agent_run_id as string | undefined, errorCode: r.error_code })
export const benchmarkService = {
  async datasets(kind: 'rag' | 'agent') {
    const r = await apiClient.get<{ items: { dataset_id: string; description: string; case_count: number }[] }>('/api/benchmarks/datasets', { params: { kind } })
    return r.items.map(d => ({ id: d.dataset_id, description: d.description, cases: d.case_count }))
  },
  async list() { return (await apiClient.get<{ items: RunWire[] }>('/api/benchmarks/runs')).items.map(map) },
  async start(kind: 'rag' | 'agent', body: object) { return map(await apiClient.post<RunWire>(`/api/benchmarks/${kind}/runs`, body)) },
  cancel(id: string) { return apiClient.post(`/api/benchmarks/runs/${encodeURIComponent(id)}/cancel`) },
  report(id: string) { return apiClient.get<{ metrics: Record<string, unknown>; cases: unknown[]; config_snapshot: Record<string, unknown> }>(`/api/benchmarks/runs/${encodeURIComponent(id)}/report`) },
}
