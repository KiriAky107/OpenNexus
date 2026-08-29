import apiClient from './apiClient'
import type { Note, NoteBlock } from '@/contracts'

export async function listNotes(params?: {
  folder?: string
  tag?: string
  limit?: number
  offset?: number
}): Promise<{ items: Note[]; total: number }> {
  return apiClient.get('/api/notes', { params })
}

export async function getNote(noteId: string): Promise<{ note: Note; blocks: NoteBlock[] }> {
  return apiClient.get(`/api/notes/${noteId}`)
}

export async function createNote(data: {
  title: string
  folder_path?: string
  content?: string
}): Promise<Note> {
  return apiClient.post('/api/notes', data)
}

export async function updateNote(
  noteId: string,
  data: { title?: string; content?: string; tags?: string[] }
): Promise<Note> {
  return apiClient.patch(`/api/notes/${noteId}`, data)
}

export async function deleteNote(noteId: string): Promise<void> {
  return apiClient.delete(`/api/notes/${noteId}`)
}

export async function moveNote(noteId: string, target_folder: string): Promise<Note> {
  return apiClient.post(`/api/notes/${noteId}/move`, { target_folder })
}
