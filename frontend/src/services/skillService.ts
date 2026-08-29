import apiClient from './apiClient'
import type { Skill } from '@/contracts'

export async function listSkills(): Promise<Skill[]> {
  try {
    return await apiClient.get('/api/skills')
  } catch {
    return mockSkills
  }
}

export async function getSkill(skillId: string): Promise<Skill> {
  return apiClient.get(`/api/skills/${skillId}`)
}

export async function installSkill(skillId: string): Promise<Skill> {
  return apiClient.post('/api/skills/install', { skill_id: skillId })
}

export async function enableSkill(skillId: string): Promise<Skill> {
  return apiClient.post(`/api/skills/${skillId}/enable`)
}

export async function disableSkill(skillId: string): Promise<Skill> {
  return apiClient.post(`/api/skills/${skillId}/disable`)
}

export async function uninstallSkill(skillId: string): Promise<void> {
  return apiClient.delete(`/api/skills/${skillId}`)
}

export const mockSkills: Skill[] = [
  {
    skill_id: 'exam-review',
    name: '期末复习助手',
    version: '1.0.0',
    description: '根据课程笔记生成复习要点和练习题，帮助高效备考',
    icon: '📚',
    author: '知笔知己团队',
    permissions: ['notes.search', 'notes.read', 'tasks.create'],
    tools: ['notes.search', 'notes.read', 'tasks.create'],
    retrieval_config: { top_k: 10, rerank: true, citation: true },
    model_requirements: { capabilities: ['chat', 'tool_calling'] },
    status: 'ready',
    enabled: true,
  },
  {
    skill_id: 'meeting-summary',
    name: '会议纪要生成',
    version: '1.1.0',
    description: '从音频或文本中提取会议要点、行动项和待办任务',
    icon: '📝',
    author: '知笔知己团队',
    permissions: ['notes.search', 'notes.write', 'tasks.write', 'attachments.read'],
    tools: ['notes.search', 'notes.create', 'tasks.create', 'attachments.read'],
    retrieval_config: { top_k: 5, rerank: false, citation: true },
    model_requirements: { capabilities: ['chat', 'tool_calling', 'structured_output'] },
    status: 'ready',
    enabled: true,
  },
  {
    skill_id: 'code-explainer',
    name: '代码解读助手',
    version: '0.9.0',
    description: '分析代码片段，解释功能、复杂度和优化建议',
    icon: '💻',
    author: '社区贡献',
    permissions: ['notes.search', 'notes.read'],
    tools: ['notes.search', 'notes.read', 'rag.search'],
    retrieval_config: { top_k: 8, rerank: true, citation: true },
    model_requirements: { capabilities: ['chat', 'tool_calling'] },
    status: 'installed',
    enabled: false,
  },
  {
    skill_id: 'research-assistant',
    name: '文献研究助手',
    version: '1.2.0',
    description: '自动整理文献笔记，生成研究综述和引用关系图',
    icon: '🔬',
    author: '社区贡献',
    permissions: ['notes.search', 'notes.read', 'notes.write'],
    tools: ['notes.search', 'notes.read', 'notes.create', 'rag.search'],
    retrieval_config: { top_k: 15, rerank: true, citation: true },
    model_requirements: { capabilities: ['chat', 'tool_calling', 'reasoning'] },
    status: 'dependency_missing',
    enabled: false,
    missing_dependencies: ['文献引用插件', '知识图谱插件'],
  },
  {
    skill_id: 'language-tutor',
    name: '语言学习助手',
    version: '0.5.0',
    description: '基于你的学习笔记生成语言练习和记忆卡片',
    icon: '🌍',
    author: '社区贡献',
    permissions: ['notes.search', 'notes.read', 'tasks.create'],
    tools: ['notes.search', 'notes.read', 'tasks.create'],
    retrieval_config: { top_k: 6, rerank: false, citation: false },
    model_requirements: { capabilities: ['chat'] },
    status: 'ready',
    enabled: true,
  },
]
