import apiClient from './apiClient'
import type { ApiSkill, OperationResponse, Skill, UserSkill, UserSkillWriteRequest } from '@/contracts'

function toSkill(skill: ApiSkill): Skill {
  const { manifest } = skill
  return {
    skill_id: manifest.skill_id,
    name: manifest.name,
    version: manifest.version,
    description: manifest.description,
    permissions: manifest.permissions,
    tools: manifest.tools,
    retrieval_config: manifest.retrieval,
    model_requirements: { capabilities: manifest.model.required_capabilities },
    status: skill.status,
    missing_dependencies: skill.missing_dependencies,
    enabled: skill.enabled,
  }
}

export async function listSkills(): Promise<Skill[]> {
  const response = await apiClient.get<{ items: ApiSkill[] }>('/api/skills')
  return response.items.map(toSkill)
}

export async function getSkill(skillId: string): Promise<Skill> {
  return toSkill(await apiClient.get<ApiSkill>(`/api/skills/${skillId}`))
}

export async function installSkill(source: string | File): Promise<Skill> {
  const installed = typeof source === 'string'
    ? await apiClient.post<ApiSkill>('/api/skills/install', { package_path: source })
    : await apiClient.postBinary<ApiSkill>('/api/skills/install-zip', source)
  return toSkill(installed)
}

export async function enableSkill(skillId: string): Promise<Skill> {
  return toSkill(await apiClient.post<ApiSkill>(`/api/skills/${skillId}/enable`))
}

export async function disableSkill(skillId: string): Promise<Skill> {
  return toSkill(await apiClient.post<ApiSkill>(`/api/skills/${skillId}/disable`))
}

export async function uninstallSkill(skillId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/skills/${skillId}`)
}

export async function listUserSkills(): Promise<UserSkill[]> {
  const items: UserSkill[] = []
  for (let page = 0; page < 100; page += 1) {
    const response = await apiClient.get<{ items: UserSkill[]; page?: { total: number } }>('/api/user-skills', { params: { limit: 100, offset: items.length } })
    items.push(...response.items)
    if (!response.items.length || items.length >= (response.page?.total ?? items.length)) return items
  }
  throw new Error('USER_SKILL_LIST_LIMIT_EXCEEDED')
}

export async function createUserSkill(request: UserSkillWriteRequest, operationId: string = crypto.randomUUID()): Promise<UserSkill> {
  return apiClient.post('/api/user-skills', request, { headers: { 'Idempotency-Key': operationId } })
}

export async function updateUserSkill(skillId: string, request: UserSkillWriteRequest, operationId: string = crypto.randomUUID()): Promise<UserSkill> {
  return apiClient.put(`/api/user-skills/${skillId}`, request, { headers: { 'Idempotency-Key': operationId } })
}

export async function deleteUserSkill(skillId: string, revision: string, operationId: string = crypto.randomUUID()): Promise<OperationResponse> {
  return apiClient.delete(`/api/user-skills/${skillId}`, {
    params: { revision }, headers: { 'Idempotency-Key': operationId },
  })
}
