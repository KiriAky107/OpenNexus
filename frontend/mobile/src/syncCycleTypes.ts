/** Typed native metadata. Transport, credentials and lifecycle leases stay native. */
import type { SyncActivity, SyncConflictReview } from '../../src/contracts/sync'
import type { AuthSelection } from './syncAuthTypes'
import type { MobileBinding } from './syncAssociationTypes'

export interface SyncTarget { expected: AuthSelection; vault_id: string; binding_id: string }
export interface RemoteRevision {
  vault_id: string; sequence: number; file_id: string; base_revision: number; path: string
  operation: 'put' | 'delete'; hash: string | null; size: number; operation_id: string
}
export interface CycleConflict {
  sequence: number; file_id: string; local_path: string; recorded_local_hash: string; remote: RemoteRevision
}
export interface SyncAttempt {
  operation_id: string; path: string; attempts: number; outcome: 'running' | 'interrupted' | 'failed' | 'succeeded'
  error: string | null; state: string
}
export interface CycleStatus {
  vault_id: string; binding: MobileBinding; paused: boolean; incoming_boundary: number | null
  pending_outbox: number; queued_jobs: number; open_conflicts: number; pending_decisions: number
  attempts: SyncAttempt[]; conflicts: CycleConflict[]; next_after: number | null; last_cycle_success: number | null
}
export interface SyncDecision {
  sequence: number; choice: 'local' | 'remote' | 'copy'; destination: string; expected_hash: string; fingerprint: string | null
  operation_id: string; rename_id: string; copy_id: string; retire_id: string; restore_id: string; state: 'pending' | 'completed'
}
export interface Resolution {
  target: SyncTarget; sequence: number; choice: 'local' | 'remote' | 'copy'; destination: string; expected_hash: string; fingerprint: string
}
export type CycleRequest =
  | { action: 'status'; target: SyncTarget; after: number; limit: number }
  | { action: 'now'; target: SyncTarget }
  | { action: 'review' | 'decision'; target: SyncTarget; sequence: number }
  | ({ action: 'resolve' } & Resolution)
export type CycleResult =
  | { kind: 'status'; status: CycleStatus }
  | { kind: 'review'; review: SyncConflictReview }
  | { kind: 'decision'; decision: SyncDecision | null }
export interface CycleObservation {
  target: SyncTarget; invocation_id: string; generation: number; activity: SyncActivity; error: string | null
}
export type CycleSnapshot = { state: 'inactive' } | { state: 'known'; observation: CycleObservation }
export type CycleCommand =
  | { action: 'run'; request: CycleRequest; invocation_id?: string }
  | { action: 'activity'; target: SyncTarget; invocation_id: string }
  | { action: 'stop'; target: SyncTarget; invocation_id: string }
export type CycleReply =
  | { kind: 'result'; response: CycleResult }
  | { kind: 'activity'; snapshot: CycleSnapshot }
  | { kind: 'stopped'; state: 'cancelled' | 'finished' | 'inactive' }
export type NativeCycle = (request: CycleCommand) => Promise<CycleReply>
