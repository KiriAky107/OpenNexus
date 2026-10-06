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
export interface SyncTransfer {
  direction: 'upload' | 'download'
  path: string
  total_bytes: number
  bytes_done: number
  resumed_bytes: number
  transferred_bytes: number
  phase: 'sending' | 'receiving' | 'verified' | 'cached' | 'committing'
}
export interface SyncActivity {
  running: boolean
  phase: 'idle' | 'handshake' | 'discover' | 'pull' | 'push' | 'complete' | 'failed' | 'cancelled'
  transfer: SyncTransfer | null
  uploaded_revisions: number
  received_revisions: number
  started_at: number | null
  finished_at: number | null
}
export interface SyncAccount {
  vault_id: string
  binding_id: string
  current_device_id: string
  checked_at: number
  vault: { id: string; name: string; sequence: number; used: number; quota: number }
  devices: Array<{ id: string; name: string; revoked: boolean }>
}
