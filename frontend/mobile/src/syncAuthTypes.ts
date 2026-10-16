/** Native metadata only. Sessions and access/refresh tokens have no IPC type. */
import type { SyncRemoteVault } from '../../src/features/settings/syncVaults'
export interface AuthSelection { revision: number; endpoint: string; account: string; operation_id: string; generation: number }
export interface AuthIntent {
  operation_id: string; endpoint: string; account: string; device_name: string
  kind: 'login' | 'refresh' | 'logout' | 'local_disconnect'
  phase: 'login_pending' | 'store_pending' | 'active' | 'refresh_pending' | 'logout_pending' | 'erase_pending' | 'disconnected' | 'unknown'
  device_id: string | null; generation: number; server_logout_confirmed: boolean; previous_operation: string | null
}
export interface AuthRejection { status: number; code: string }
export interface AuthState {
  intent: AuthIntent | null; authenticated: boolean; expired: boolean; credential_change_required: boolean
  expires_at: number | null; rejected: AuthRejection | null
}
export interface AuthProfile { intent: AuthIntent; rejected: AuthRejection | null; selected: boolean }
export interface AuthProfiles { items: AuthProfile[]; total: number; offset: number; limit: number; selection: AuthSelection | null }
export interface AuthChoice { operation_id: string; expected_revision: number | null; selection: AuthSelection }
export interface AuthVaults { selection: AuthSelection; items: SyncRemoteVault[]; total: number; offset: number; limit: number }
export interface AuthExisting {
  operation_id: string; previous_operation: string; endpoint: string; account: string; expected: AuthSelection
}
export type AuthRequest =
  | { action: 'current' | 'cancel' }
  | { action: 'profiles'; offset: number; limit: number }
  | { action: 'vaults'; expected: AuthSelection; offset: number; limit: number }
  | { action: 'operation'; endpoint: string; account: string; operation_id: string }
  | { action: 'choice_receipt'; operation_id: string }
  | { action: 'choose'; operation_id: string; target_operation: string; generation: number; expected_revision: number | null }
  | { action: 'login'; operation_id: string; endpoint: string; account: string; device_name: string; password: string; expected: AuthSelection | null }
  | ({ action: 'refresh' | 'logout' | 'disconnect' } & AuthExisting)
  | { action: 'resume_erasure'; operation_id: string; endpoint: string; account: string; expected: AuthSelection }
export type AuthResponse =
  | { kind: 'current'; selection: AuthSelection | null; state: AuthState }
  | { kind: 'profiles'; profiles: AuthProfiles }
  | { kind: 'vaults'; page: AuthVaults }
  | { kind: 'operation'; state: AuthState }
  | { kind: 'choice'; choice: AuthChoice | null }
  | { kind: 'cancelled' }
export type NativeAuth = (request: AuthRequest) => Promise<AuthResponse>
