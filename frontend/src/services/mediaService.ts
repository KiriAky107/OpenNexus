import { apiClient, resolveApiUrl } from './apiClient'
import { t } from '@/i18n'
import { isDesktop } from './platform/desktop'

export interface Segment { segment_id: string; start_time: number; end_time: number; text: string; speaker: string | null; language?: string }
export interface MediaJob {
  filename?: string | null
  job_id: string; attachment_id: string; status: 'queued' | 'running' | 'processing' | 'completed' | 'failed' | 'cancelled'
  text: string | null; original_text: string | null; segments: Segment[]; speaker_names: Record<string, string>
  revision: number; created_at: string; progress: number | null; error_code: string | null; error_message: string | null
  warnings: string[]; source: string | null; fallback_reason: string | null; local_only: boolean
}
export interface MediaArtifact { note_id: string; title: string; file_path: string }
export interface MediaArtifacts { transcript: MediaArtifact | null; knowledge_note: MediaArtifact | null }
export const mediaService = {
  getArtifacts: (id: string) => apiClient.get<MediaArtifacts>(`/api/media/transcriptions/${encodeURIComponent(id)}/artifacts`),
  list: () => apiClient.get<{ items: MediaJob[] }>('/api/media/transcriptions'),
  get: (id: string) => apiClient.get<MediaJob>(`/api/media/transcriptions/${encodeURIComponent(id)}`),
  create: (body: unknown) => apiClient.post<MediaJob>('/api/media/transcriptions', body),
  cancel: (id: string) => apiClient.post<MediaJob>(`/api/media/transcriptions/${encodeURIComponent(id)}/cancel`),
  retry: (id: string) => apiClient.post<MediaJob>(`/api/media/transcriptions/${encodeURIComponent(id)}/retry`),
  match: (attachment_id: string, reference_attachment_id: string, local_only: boolean) => apiClient.post<{score:number;source:string;fallback_reason:string|null}>('/api/media/speaker-matches', {attachment_id,reference_attachment_id,local_only}),
  save: (job: MediaJob) => apiClient.patch<MediaJob>(`/api/media/transcriptions/${encodeURIComponent(job.job_id)}`, {
    revision: job.revision, text: job.text, segments: job.segments, speaker_names: job.speaker_names,
  }),
  revisions: (id: string) => apiClient.get<{items: MediaJob[]}>(`/api/media/transcriptions/${encodeURIComponent(id)}/revisions`),
  note: (id: string, title: string, update_existing = false) => apiClient.post<{note_id: string; title: string}>(`/api/media/transcriptions/${encodeURIComponent(id)}/notes`, { title, update_existing }),
  artifacts: (id: string, body: {title: string; knowledge_title?: string; provider_id: string; model: string; update_existing?: boolean}) =>
    apiClient.post<{transcript: MediaArtifact; knowledge_note: MediaArtifact}>(
      `/api/media/transcriptions/${encodeURIComponent(id)}/artifacts`, body, { timeoutMs: 300_000 },
    ),
  audio: (id: string) => resolveApiUrl(`/api/media/attachments/${encodeURIComponent(id)}`),
  impact: (id: string) => apiClient.get<{message:string;retained_note_ids:string[]}>(`/api/media/attachments/${encodeURIComponent(id)}/cleanup-impact`),
  purge: (id: string) => apiClient.delete(`/api/media/attachments/${encodeURIComponent(id)}`),
  async upload(file: File, idempotencyKey?: string) {
    if (isDesktop()) return apiClient.postBinary<{ attachment_id: string }>(
      `/api/media/attachments?filename=${encodeURIComponent(file.name)}`, file,
      {'Content-Type': 'application/octet-stream', ...(idempotencyKey ? {'Idempotency-Key': idempotencyKey} : {})},
    )
    const response = await fetch(resolveApiUrl(`/api/media/attachments?filename=${encodeURIComponent(file.name)}`), {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream', ...(idempotencyKey ? {'Idempotency-Key': idempotencyKey} : {})}, body: file,
    })
  if (!response.ok) throw new Error((await response.json())?.error?.message || t('附件上传失败', 'Attachment upload failed'))
    return await response.json() as {attachment_id: string}
  },
}

// 保留一个身份，直到输入/选项发生变化，包括丢失 HTTP 响应。有效负载保留在内存中；持久上传/作业归后端所有。
export function createMediaSubmission() {
  let pending: {file: File; options: string; uploadKey: string; jobKey: string; attachmentId?: string} | null = null
  return {
    reset() { pending = null },
    async submit(file: File, options: Record<string, unknown>) {
      const serialized = JSON.stringify(options)
      if (!pending || pending.file !== file || pending.options !== serialized) {
        pending = {file, options: serialized, uploadKey: crypto.randomUUID(), jobKey: crypto.randomUUID()}
      }
      const current = pending
      if (!current.attachmentId) current.attachmentId = (await mediaService.upload(file, current.uploadKey)).attachment_id
      return mediaService.create({...JSON.parse(current.options), attachment_id: current.attachmentId, idempotency_key: current.jobKey})
    },
  }
}
