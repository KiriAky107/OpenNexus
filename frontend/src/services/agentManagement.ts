import api from './apiClient'
export interface AgentConfig {
  name: string; role: string; instructions: string; provider_id: string; model: string
  tools: string[]; token_budget: number | null; max_steps: number; enabled: boolean
}
export interface AgentDefinition { id: string; revision: number; config: AgentConfig; origin?: 'manual' | 'chat' | 'benchmark' }
export interface CollaborationMember {
  member_id: string; agent_id: string; input: string; depends_on: string[]
  definition: AgentDefinition; status: string; run_id: string | null; output?: string; error?: string
}
export interface Collaboration {
  id: string; revision: number; title: string; status: string; token_usage: number; tool_calls: number
  members: CollaborationMember[]; error?: string
  plan: { token_budget: number; max_concurrency: number; max_tool_calls: number; timeout_seconds: number }
  budget_request: { request_id: string; token_usage: number; token_budget: number } | null
}
export interface AgentChange {
  id: string; revision: number; agent_id: string; status: string; action: 'delete' | 'update'
  before: AgentConfig; config: AgentConfig | null
}
export const listDefinitions = () => api.get<{items: AgentDefinition[]}>('/api/agent/definitions')
export const createDefinition = (config: AgentConfig) => api.post<AgentDefinition>('/api/agent/definitions', config)
export const updateDefinition = (definition: AgentDefinition, config: AgentConfig) => api.put<AgentDefinition>(`/api/agent/definitions/${definition.id}`, { config, expected_revision: definition.revision })
export const deleteDefinition = (definition: AgentDefinition) => api.delete(`/api/agent/definitions/${definition.id}`, { params: { expected_revision: definition.revision } })
export const listCollaborations = () => api.get<{items: Collaboration[]}>('/api/agent/collaborations')
export const getCollaboration = (id: string) => api.get<Collaboration>(`/api/agent/collaborations/${id}`)
export const reviewCollaboration = (group: Collaboration, decision: 'approve' | 'reject') => api.post<Collaboration>(`/api/agent/collaborations/${group.id}/review`, { expected_revision: group.revision, decision })
export const cancelCollaboration = (id: string, memberId?: string) => api.post(`/api/agent/collaborations/${id}/cancel`, undefined, { params: { member_id: memberId } })
export const getChange = (id: string) => api.get<AgentChange>(`/api/agent/changes/${id}`)
export const reviewChange = (change: AgentChange, decision: 'approve' | 'reject') => api.post<AgentChange>(`/api/agent/changes/${change.id}/review`, { expected_revision: change.revision, decision })
