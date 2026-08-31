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
  data: { title?: string; markdown?: string; tags?: string[] }
): Promise<ApiNote> {
  return apiClient.patch(`/api/notes/${noteId}`, data)
}

export async function deleteNote(noteId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/notes/${noteId}`)
}

export async function moveNote(noteId: string, folder: string): Promise<ApiNote> {
  return apiClient.post(`/api/notes/${noteId}/move`, { folder })
}

export async function renameNote(noteId: string, fileName: string): Promise<ApiNote> {
  return apiClient.post(`/api/notes/${noteId}/rename`, { file_name: fileName })
}
