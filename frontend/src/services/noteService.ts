import apiClient from './apiClient'
import type { ApiNote, ApiNoteSummary, OperationResponse, PageMeta } from '@/contracts'

export async function listNotes(params?: {
  folder?: string
  tag?: string
  limit?: number
  offset?: number
}): Promise<{ items: ApiNoteSummary[]; page: PageMeta }> {
  return apiClient.get('/api/notes', { params })
}

export async function getNote(noteId: string): Promise<ApiNote> {
  return apiClient.get(`/api/notes/${noteId}`)
}

export async function createNote(data: {
  title: string
  folder?: string
  markdown?: string
  tags?: string[]
}): Promise<ApiNote> {
  return apiClient.post('/api/notes', data)
}

export async function updateNote(
  noteId: string,
  data: { title?: string; markdown?: string; tags?: string[]; expected_content_hash?: string }
): Promise<ApiNote> {
  return apiClient.patch(`/api/notes/${noteId}`, data)
}

export async function deleteNote(noteId: string, expectedContentHash?: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/notes/${noteId}`, { params: { expected_content_hash: expectedContentHash } })
}

export async function moveNote(noteId: string, folder: string, expectedContentHash?: string): Promise<ApiNote> {
  return apiClient.post(`/api/notes/${noteId}/move`, { folder, ...(expectedContentHash ? { expected_content_hash: expectedContentHash } : {}) })
}

export async function renameNote(noteId: string, fileName: string, expectedContentHash?: string): Promise<ApiNote> {
  return apiClient.post(`/api/notes/${noteId}/rename`, { file_name: fileName, ...(expectedContentHash ? { expected_content_hash: expectedContentHash } : {}) })
}

export interface NoteDiff {
  lines: { kind: '+' | '-'; line: number; text: string }[]
  added_chars: number
  removed_chars: number
  before_chars: number
  after_chars: number
  truncated: boolean
}
export interface NoteWritePreview {
  kind?: 'experiment_file'
  file_id?: string | null
  overwrite?: boolean
  before_bytes?: number
  after_bytes?: number
  token: string
  file_path: string
  note_id: string | null
  operation: 'create' | 'append' | 'replace'
  metadata: { title: string | null; tags: string[] | null }
  diff: NoteDiff
}
export interface NoteChange {
  change_id: string
  note_id: string | null
  file_path: string | null
  requested_target?: string
  origin: string
  before_hash: string | null
  after_hash: string | null
  created_at: string
  applied_at: string | null
  status?: 'pending' | 'applied' | 'failed'
  failure_code?: string | null
}
export interface NoteChangeDetail extends NoteChange {
  diff: NoteDiff | null
  can_restore: boolean
  restore_reason: '' | 'created' | 'changed' | 'unconfirmed'
  reconciliation_reason?: string
  before_metadata: string | null
  after_metadata: string | null
}
export const listNoteChanges = (noteId: string, offset = 0) => apiClient.get<{ items: NoteChange[] }>(`/api/notes/${noteId}/changes`, { params: { limit: 30, offset } })
export const getNoteChange = (noteId: string, changeId: string) => apiClient.get<NoteChangeDetail>(`/api/notes/${noteId}/changes/${changeId}`)
export const listPendingNoteChanges = (offset = 0) => apiClient.get<{ items: NoteChange[] }>('/api/note-changes', { params: { limit: 30, offset } })
export const inspectPendingNoteChange = (changeId: string) => apiClient.get<NoteChangeDetail>(`/api/note-changes/${changeId}`)
export const reconcileNoteChange = (changeId: string) => apiClient.post<NoteChangeDetail>(`/api/note-changes/${changeId}/reconcile`, {})
export const restoreNoteChange = (change: NoteChange) => apiClient.post<ApiNote>(`/api/notes/${change.note_id}/changes/${change.change_id}/restore`, { expected_content_hash: change.after_hash, confirm: true })
