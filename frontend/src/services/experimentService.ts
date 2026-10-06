import { hostInvoke, nativePath, type HostEntry } from './platform/desktop'
import { t } from '@/i18n'

export interface SelectedFile { file_id: string; path: string; hash: string; revision: number }
export interface Limits { wall_seconds: number; cpu_seconds: number; memory_mib: number; processes: number; disk_mib: number; output_mib: number; log_kib: number; objects: number }
export interface RunRequest { vault_id: string; operation_id: string; runtime_id: string; entry: SelectedFile; inputs: SelectedFile[]; limits: Limits }
export interface InputSummary { request: RunRequest; fingerprint: string; sizes: Record<string, number>; total_bytes: number }
export type RunState = 'awaiting_confirmation' | 'approved' | 'starting' | 'running' | 'cancel_requested' | 'rejected' | 'cancelled' | 'completed' | 'failed' | 'limited' | 'interrupted'
export type ImportState = 'awaiting_confirmation' | 'approved' | 'completed' | 'partial' | 'failed' | 'cancelled' | 'rejected'
export interface StreamLog { text: string; bytes_seen: number; retained_bytes: number; truncated: boolean; invalid_utf8: boolean; complete: boolean; read_error: boolean; displayed_bytes?: number; display_truncated?: boolean }
export interface Logs { stdout: StreamLog; stderr: StreamLog }
export interface OutputManifest { path: string; bytes: number; sha256: string; kind: 'text' | 'markdown' | 'json' | 'csv' | 'png' }
export type OutputReport = { status: 'collected'; summary: { files: OutputManifest[]; skipped: { path: string; bytes: number; reason: string }[]; total_bytes: number } } | { status: 'rejected'; error: string }
export interface RunResult { outcome: string; exit_code: number | null; elapsed_ms: number; error: string | null; user_cpu_ticks: number | null; peak_memory_bytes: number | null; final_disk_bytes: number | null; logs: Logs; outputs: OutputReport | null }
export interface RunRecord { summary: InputSummary; state: RunState; created_ms: number; updated_ms: number; approval_id: string | null; approved_ms: number | null; error: string | null; result: RunResult | null }
export interface HistoryCursor { vault_id: string; created_ms: number; operation_id: string }
export interface HistoryItem { operation_id: string; fingerprint: string; entry: SelectedFile; runtime_id: string; state: RunState; created_ms: number; error: string | null; elapsed_ms: number | null; exit_code: number | null; output_files: number | null }
export interface Page<T> { items: T[]; next_cursor: HistoryCursor | null }
export interface Status { available: boolean; runtime: { runtime_id: string; version: string } | null; limits: Limits; error: string | null; cleanup: { operation_id: string; phase: string; error: string | null } | null }
export interface CleanupReview { fingerprint: string; operation_id: string; phase: string; profile_present: boolean; temporary_objects: number; borrowed_objects: number }
export interface LiveRun { operation_id: string; elapsed_ms: number; logs: Logs }
export interface TextPreview { text: string; truncated: boolean; lines_shown: number }
export interface SourcePreview { source: SelectedFile; bytes: number; current_path: string | null; preview: TextPreview }
export type PreviewContent = { format: 'text'; preview: TextPreview } | { format: 'csv'; rows: string[][]; truncated: boolean } | { format: 'png'; width: number; height: number; original_width: number; original_height: number; content_base64: string }
export interface OutputPreview { manifest: OutputManifest; content: PreviewContent }
export interface ImportSelection { output_path: string; destination: string }
export interface ImportRequest { vault_id: string; operation_id: string; run_id: string; selections: ImportSelection[] }
export interface ImportRecord { plan: { request: ImportRequest; source: InputSummary; execution: { outcome: string; exit_code: number | null; elapsed_ms: number }; items: { output: OutputManifest; target: SelectedFile; write_id: string }[] }; fingerprint: string; state: ImportState; created_ms: number; confirmed_ms: number | null; items: { state: string; entry: HostEntry | null; error: string | null }[]; atomic_scope: 'file' }
export interface ImportHistoryItem { operation_id: string; run_id: string; entry: SelectedFile; fingerprint: string; state: ImportState; created_ms: number; files: number; committed: number }
export interface Origin { import_id: string; run_id: string; source: InputSummary; execution: { outcome: string; exit_code: number | null; elapsed_ms: number }; output: OutputManifest; imported: HostEntry; current_path: string | null; created_ms: number }
export interface AgentContext { agent_run_id: string; tool_call_id: string; request_id: string }
interface AgentReviewBinding { vault_id: string; operation_id: string; fingerprint: string; context: AgentContext }
export type NativeAgentReview = AgentReviewBinding & (
  { kind: 'experiment_run'; record: RunRecord } | { kind: 'experiment_import'; record: ImportRecord }
)
export type AgentExperimentReview = NativeAgentReview & { token: string }

// All requests keep their original vault binding, including retries after a
// dialog or network/IPC interruption. The Host checks it under its file lock.
export function request<T>(vault_id: string, action: { kind: string; [key: string]: unknown }): Promise<T> {
  return hostInvoke<T>('experiment_request', { request: { vault_id, action } })
}
export async function files(): Promise<SelectedFile[]> {
  const entries = await hostInvoke<HostEntry[]>('workspace_tree')
  return entries.filter(e => !e.deleted && !e.is_folder && /^experiments\/.+\.(py|json|csv)$/i.test(e.path))
    .map(({ file_id, path, hash, revision }) => ({ file_id, path, hash, revision }))
}
export function runRequest(vault: string, entry: string, inputs: string[], candidates: SelectedFile[], runtime: string, limits: Limits): RunRequest {
  const selected = (path: string) => {
    const file = candidates.find(f => f.path === nativePath(path))
    if (!file) throw new Error('EXPERIMENT_INPUT_CHANGED')
    return { ...file }
  }
  return { vault_id: vault, operation_id: crypto.randomUUID(), runtime_id: runtime, entry: selected(entry), inputs: inputs.map(selected), limits: { ...limits } }
}
export function defaultDestination(output: OutputManifest, run: string): string {
  const name = output.path.split('/').at(-1) ?? 'output'
  if (output.kind === 'json' || output.kind === 'csv') return `experiments/results/${run.slice(0, 8)}/${name}`
  return `实验成果/${run.slice(0, 8)}/${output.kind === 'text' ? name.replace(/\.txt$/i, '.md') : name}`
}
export function stateText(state: string): string {
  const map: Record<string, string> = {
    awaiting_confirmation: t('等待确认', 'Awaiting confirmation'), approved: t('已批准，尚未完成', 'Approved; not completed'), starting: t('启动中', 'Starting'), running: t('运行中', 'Running'), cancel_requested: t('正在停止', 'Stopping'),
    rejected: t('已拒绝', 'Rejected'), cancelled: t('已取消', 'Cancelled'), completed: t('已完成', 'Completed'), failed: t('失败', 'Failed'), limited: t('已超限停止', 'Stopped at limit'), interrupted: t('已中断', 'Interrupted'), partial: t('部分成功', 'Partially imported'),
    committed: t('已导入', 'Imported'), pending: t('尚未导入', 'Pending'),
  }
  return map[state] ?? state
}
export function errorText(error: unknown): string {
  const code = error instanceof Error ? error.message : String(error)
  const messages: Record<string, string> = {
    EXPERIMENT_INPUT_CHANGED: t('源文件或输入已经变化，请重新准备运行。', 'Source or inputs changed. Prepare a new run.'),
    EXPERIMENT_IMPORT_TARGET_CHANGED: t('目标文件已变化。请核对当前内容并准备新的导入。', 'A destination changed. Review its current content and prepare a new import.'),
    EXPERIMENT_CLEANUP_REQUIRED: t('上次运行仍有待核对的清理记录，暂时不能启动新的实验。', 'A previous run has cleanup awaiting verification. New experiments are currently blocked.'),
    EXPERIMENT_CLEANUP_OWNER_ACTIVE: t('清理资源仍由运行中的应用持有，请等待实验结束后再核对。', 'Cleanup resources are still held by a running app. Review again after the experiment finishes.'),
    EXPERIMENT_CLEANUP_JOB_ACTIVE: t('实验进程尚未全部退出，请停止运行后再核对。', 'Experiment processes have not all exited. Stop the run and review again.'),
    EXPERIMENT_CLEANUP_UNVERIFIED: t('此记录缺少可核对的原始归属信息，不能自动清理。记录已保留。', 'This record lacks verifiable original ownership. Automatic cleanup is unavailable; the record is retained.'),
    EXPERIMENT_CLEANUP_JOB_UNVERIFIED: t('暂时无法核对实验进程是否已退出，清理记录已保留。', 'Experiment process termination could not be verified. The cleanup record is retained.'),
    EXPERIMENT_CLEANUP_OBJECT_CHANGED: t('临时资源的位置、身份或权限已变化，清理已停止，记录已保留。', 'A temporary resource changed location, identity or permissions. Cleanup stopped and its record is retained.'),
    EXPERIMENT_CLEANUP_REVIEW_CHANGED: t('待清理资源已变化，请重新核对后确认。', 'The cleanup scope changed. Review it again before confirming.'),
    EXPERIMENT_CLEANUP_JOURNAL_FAILED: t('清理记录暂时无法保存，请重新核对后重试。', 'The cleanup record could not be saved. Review again and retry.'),
    EXPERIMENT_SOURCE_CLEANUP_FAILED: t('部分临时文件无法删除，请检查占用或只读限制后重新核对。清理记录已保留。', 'Some temporary files could not be removed. Check open handles or read-only restrictions and review again. The cleanup record is retained.'),
    EXPERIMENT_RUNTIME_UNAVAILABLE: t('当前应用未找到已验证的随包运行时。', 'The verified bundled runtime is unavailable.'),
    EXPERIMENT_PLATFORM_UNAVAILABLE: t('此平台暂不支持隔离实验运行。', 'Isolated experiment execution is unavailable on this platform.'),
    EXPERIMENT_RUN_BUSY: t('已有实验运行中，请等待结束或停止该运行。', 'An experiment is already running. Wait for it or stop it.'),
    VAULT_PERMISSION_CHANGED: t('知识库已切换，旧请求已停止。', 'The vault changed; the previous request was stopped.'),
    EXPERIMENT_USER_CANCELLED: t('已取消操作。', 'Action cancelled.'),
    EXPERIMENT_RECORD_FORGOTTEN: t('运行记录已清理。已导入的文件与来源摘要仍保留。', 'The run record was cleared. Imported files and provenance summaries are retained.'),
    EXPERIMENT_OPERATION_NOT_FOUND: t('运行记录不存在或已清理。', 'The run record is missing or was cleared.'),
  }
  return messages[code] ?? code
}
// Byte chunks are decoded individually, then joined as bytes. UTF-8 can cross
// chunk boundaries; the download preserves original bytes and CRLF exactly.
export async function originalBytes(vault: string, run: string, path: string, source: boolean): Promise<Uint8Array> {
  const chunks: Uint8Array[] = []
  let offset = 0, expectedSize: number | undefined
  do {
    const chunk = await request<{ bytes?: number; manifest?: OutputManifest; offset: number; next_offset: number | null; content_base64: string }>(vault, { kind: source ? 'source_read' : 'output_read', operation_id: run, path, offset, limit: 256 * 1024 })
    const size = source ? chunk.bytes : chunk.manifest?.bytes
    if (size === undefined || size < 0 || size > (source ? 2 : 16) * 1024 * 1024 || chunk.offset !== offset || (expectedSize !== undefined && size !== expectedSize)) throw new Error('EXPERIMENT_RESPONSE_INVALID')
    expectedSize = size
    const bytes = Uint8Array.from(atob(chunk.content_base64), c => c.charCodeAt(0))
    if (bytes.length > 256 * 1024 || offset + bytes.length > size || (chunk.next_offset !== null && (chunk.next_offset !== offset + bytes.length || bytes.length === 0)) || (chunk.next_offset === null && offset + bytes.length !== size)) throw new Error('EXPERIMENT_RESPONSE_INVALID')
    chunks.push(bytes); offset += bytes.length
    if (chunk.next_offset === null) break
  } while (true)
  const result = new Uint8Array(offset)
  let position = 0
  for (const part of chunks) { result.set(part, position); position += part.length }
  return result
}
