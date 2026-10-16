export interface Entry {
  file_id: string; path: string; hash: string; revision: number; deleted: boolean; is_folder: boolean; updated_at?: string
}
export interface NoteDocument extends Entry { content: string }
export interface Draft {
  id: string; file_id: string | null; path: string; base_hash: string; generation: number
  saved_generation: number; content: string; edit_session: string
}
export type OperationState = 'prepared' | 'committed' | 'rejected' | 'cancelled'
export interface SaveIntent {
  operation_id: string; draft_id: string; generation: number; file_id: string | null; path: string
  expected: string; content: string; state: OperationState; error: string | null
}
export interface MutationIntent {
  operation_id: string; kind: 'rename' | 'delete'; file_id: string; path: string
  destination: string; expected: string; state: OperationState; error: string | null
}
export interface DraftView {
  draft: Draft; current: NoteDocument | null; current_error: string | null
  pending_save: SaveIntent | null; pending_mutation: MutationIntent | null
  pending_image?: ImageIntent | null
}
export interface ImageIntent {
  operation_id: string; draft_id: string; path: string; generation: number; asset_path: string; reference: string
  hash: string; size: number; content_before: string; content_after: string; state: OperationState; error: string | null
}
export type DraftSummary = Omit<Draft, 'content' | 'edit_session' | 'base_hash'> & { bytes: number }
export interface PendingOperation {
  operation_id: string; kind: 'save' | 'rename' | 'delete' | 'image'; draft_id: string | null
  path: string; destination: string; state: OperationState; error: string | null
}
export interface Page<T> { items: T[]; total: number }
export interface NotePage extends Page<Entry> { directories: string[]; unsearched_large_notes: number }
export type MobileRequest =
  | { kind: 'list_notes'; offset: number; limit: number; query: string; directory: string }
  | { kind: 'list_drafts' | 'list_pending'; offset: number; limit: number }
  | { kind: 'open_note' | 'read_note'; file_id: string }
  | { kind: 'open_draft' | 'draft_status'; draft_id: string }
  | { kind: 'create_draft'; path: string; content?: string }
  | { kind: 'persist_draft'; draft_id: string; edit_session: string; generation: number; content: string }
  | { kind: 'prepare_save'; draft_id: string; edit_session: string; generation: number }
  | { kind: 'observe_save' | 'apply_save' | 'cancel_save' | 'observe_mutation' | 'apply_mutation' | 'cancel_mutation'; operation_id: string }
  | { kind: 'copy_draft'; draft_id: string; edit_session: string; path: string }
  | { kind: 'discard_draft'; draft_id: string; edit_session: string }
  | { kind: 'prepare_mutation'; action: 'rename' | 'delete'; file_id: string; expected: string; destination: string }
  | { kind: 'prepare_image'; draft_id: string; edit_session: string; generation: number; from: number; to: number; content_base64: string }
  | { kind: 'observe_image' | 'apply_image' | 'cancel_image'; operation_id: string }
  | { kind: 'read_image'; path: string }

export type NativeRequest = <T>(request: MobileRequest) => Promise<T>
