import type { AuthSelection } from './syncAuthTypes'

export interface MobileBinding { id: string; endpoint: string; account: string; remote_vault: string; cursor: number }
export interface BindingRequest {
  operation_id: string; local_vault: string; remote_vault: string; selection: AuthSelection; preview_fingerprint: string
}
export interface BindingIntent { request: BindingRequest; phase: 'prepared' | 'bound'; attempt_revision: number; binding: MobileBinding | null }
export interface BindingStatus { vault_id: string; binding: MobileBinding | null; original_operation_id: string | null; another_connection: boolean }
export interface BindingPreview {
  selection: AuthSelection; local_vault: string; remote_vault: string; fingerprint: string; boundary: number
  items: Array<{ path: string; action: 'upload' | 'download' | 'identical' | 'conflict' }>
  total: number; offset: number; limit: number
}
export interface BindingPage { items: BindingIntent[]; next_after: string | null }
export type AssociationRequest =
  | { action: 'status'; expected: AuthSelection }
  | { action: 'preview'; expected: AuthSelection; local_vault: string; remote_vault: string; offset: number; limit: number }
  | { action: 'bind'; request: BindingRequest }
  | { action: 'resume'; request: BindingRequest; observed_revision: number }
  | { action: 'operation'; expected: AuthSelection; operation_id: string }
  | { action: 'pending'; expected: AuthSelection; after: string | null; limit: number }
export type AssociationResponse =
  | { kind: 'status'; status: BindingStatus }
  | { kind: 'preview'; page: BindingPreview }
  | { kind: 'operation'; intent: BindingIntent | null }
  | { kind: 'pending'; page: BindingPage }
export type NativeAssociation = (request: AssociationRequest) => Promise<AssociationResponse>
