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
  async downloadAudio(id: string, signal?: AbortSignal): Promise<Blob> {
    const path = `/api/media/attachments/${encodeURIComponent(id)}`
    const info = await apiClient.get<{ size: number; content_type: string }>(`${path}/info`, { signal })
    if (!Number.isSafeInteger(info.size) || info.size < 1 || info.size > 200 * 1024 * 1024) {
      throw new Error(t('音频大小无效。', 'Invalid audio size.'))
    }
    if (info.size <= 64 * 1024 * 1024) return (await apiClient.get<Response>(path, { signal })).blob()
    const chunks: Blob[] = []
    const chunkSize = 4 * 1024 * 1024
    for (let offset = 0; offset < info.size; offset += chunkSize) {
      const length = Math.min(chunkSize, info.size - offset)
      const response = await apiClient.get<Response>(`${path}/chunks?offset=${offset}&length=${length}`, { signal })
      const chunk = await response.blob()
      if (chunk.size !== length) throw new Error(t('音频分块读取不完整，请重试。', 'Incomplete audio chunk; retry playback.'))
      chunks.push(chunk)
    }
    return new Blob(chunks, { type: info.content_type })
  },
  impact: (id: string) => apiClient.get<{message:string;retained_note_ids:string[]}>(`/api/media/attachments/${encodeURIComponent(id)}/cleanup-impact`),
  purge: (id: string) => apiClient.delete(`/api/media/attachments/${encodeURIComponent(id)}`),
  async upload(file: File, idempotencyKey?: string) {
    if (isDesktop()) {
      if (file.size > 64 * 1024 * 1024) {
        const uploadId = idempotencyKey ?? crypto.randomUUID()
        const chunkSize = 4 * 1024 * 1024
        let offset = 0
        while (offset < file.size) {
          const chunk = file.slice(offset, Math.min(offset + chunkSize, file.size))
          const result = await apiClient.postBinary<{ attachment_id: string; next_offset: number; complete: boolean }>(
            `/api/media/attachments/chunks?filename=${encodeURIComponent(file.name)}&upload_id=${encodeURIComponent(uploadId)}&offset=${offset}&total=${file.size}`,
            chunk, { 'Content-Type': 'application/octet-stream' },
          )
          if (result.complete) return { attachment_id: result.attachment_id }
          if (!Number.isSafeInteger(result.next_offset) || result.next_offset <= offset || result.next_offset > file.size) {
            throw new Error(t('分块上传进度无效，请重试。', 'Invalid chunk upload progress; retry the upload.'))
          }
          offset = result.next_offset
        }
        throw new Error(t('上传未完成，请重试。', 'Upload did not finish; retry.'))
      }
      return apiClient.postBinary<{ attachment_id: string }>(
        `/api/media/attachments?filename=${encodeURIComponent(file.name)}`, file,
        {'Content-Type': 'application/octet-stream', ...(idempotencyKey ? {'Idempotency-Key': idempotencyKey} : {})},
      )
    }
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
