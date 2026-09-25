import { apiClient } from './apiClient'
interface RunWire { run_id: string; kind: 'rag' | 'agent'; dataset_id: string; status: string; progress: number | null; config_snapshot: Record<string, unknown>; error_code: string | null }
export interface BenchmarkRun { id: string; kind: 'rag' | 'agent'; datasetId: string; status: string; progress: number | null; agentId?: string; collaborationId?: string; errorCode: string | null }
const map = (r: RunWire): BenchmarkRun => ({ id: r.run_id, kind: r.kind, datasetId: r.dataset_id, status: r.status, progress: r.progress, agentId: r.config_snapshot.active_agent_run_id as string | undefined, collaborationId: r.config_snapshot.active_collaboration_id as string | undefined, errorCode: r.error_code })
// 保留运行配置中的 Agent Run ID，使报告页可直接进入对应 Trace 和权限处理入口。
export const benchmarkService = {
  async datasets(kind: 'rag' | 'agent') {
    const r = await apiClient.get<{ items: { dataset_id: string; description: string; case_count: number; scope: 'vault' | 'shared'; version: string }[] }>('/api/benchmarks/datasets', { params: { kind } })
    return r.items.map(d => ({ id: d.dataset_id, description: d.description, cases: d.case_count, scope: d.scope, version: d.version }))
  },
  importDataset(content: string, expectedVaultId?: string) { return apiClient.post<{ dataset_id: string; kind: 'rag' | 'agent' }>('/api/benchmarks/datasets/import', { content, expected_vault_id: expectedVaultId }) },
  exportDataset(id: string, kind: 'rag' | 'agent') { return apiClient.get<Record<string, unknown>>(`/api/benchmarks/datasets/${encodeURIComponent(id)}/export`, { params: { kind } }) },
  async list() { return (await apiClient.get<{ items: RunWire[] }>('/api/benchmarks/runs')).items.map(map) },
  async start(kind: 'rag' | 'agent', body: object) { return map(await apiClient.post<RunWire>(`/api/benchmarks/${kind}/runs`, body)) },
  cancel(id: string) { return apiClient.post(`/api/benchmarks/runs/${encodeURIComponent(id)}/cancel`) },
  report(id: string) { return apiClient.get<{ metrics: Record<string, unknown>; cases: unknown[]; config_snapshot: Record<string, unknown> }>(`/api/benchmarks/runs/${encodeURIComponent(id)}/report`) },
}
