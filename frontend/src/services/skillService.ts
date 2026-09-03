import apiClient from './apiClient'
import type { ApiSkill, OperationResponse, Skill } from '@/contracts'

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

export async function installSkill(packagePath: string): Promise<Skill> {
  return toSkill(await apiClient.post<ApiSkill>('/api/skills/install', { package_path: packagePath }))
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
