export interface SyncConflict {
  sequence: number
  local_path: string
  local_hash: string
  current_hash?: string
  current_path?: string
  remote: { path: string; operation: string }
}
export interface SyncContent {
  exists: boolean
  hash: string
  byte_size: number
  text: string | null
  preview_bytes: number
  truncated: boolean
}
export interface SyncConflictReview {
  vault_id: string
  binding_id: string
  sequence: number
  file_id: string
  local_path: string
  local_file_id: string | null
  remote: { path: string; operation: string; base_revision: number; sequence: number }
  local: SyncContent
  incoming: SyncContent
  base: SyncContent | null
  related: Array<{ path: string; file_id: string | null; content: SyncContent }>
  fingerprint: string
}
