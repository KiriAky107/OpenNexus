import type { ToolCall } from '@/contracts'
import { statusLabel } from '@/utils/statusLabels'
import { t } from '@/i18n'
export function toolTitle(name: string) {
  return ({ 'agent.define': t('创建智能体配置', 'Create Agent definition'), 'agent.start': t('启动智能体任务', 'Start Agent task'),
    'agent.create': t('创建并启动临时任务', 'Create and start task'), 'agent.collaborate': t('规划协作分工', 'Plan collaboration'),
    'agent.search_tools': t('查找可用工具', 'Discover available tools'), 'agent.list': t('查找智能体', 'Find Agents'),
    'agent.inspect': t('读取智能体配置', 'Read Agent definition'), 'agent.status': t('查询任务状态', 'Read task status'),
    'agent.propose_update': t('提出配置变更', 'Propose configuration change'), 'agent.propose_delete': t('提出删除请求', 'Propose deletion'),
    'rag.search': t('检索知识库', 'Search knowledge base') } as Record<string, string>)[name] || name
}
export function toolSummary(call: ToolCall) {
  let result: Record<string, unknown> = {}
  try { const parsed = JSON.parse(call.result || '{}'); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) result = parsed } catch { /* unfinished result */ }
  const name = result.name || result.title || (result.config as Record<string, unknown> | undefined)?.name
  return [toolTitle(call.name), statusLabel(call.status), typeof name === 'string' ? name : '', call.error_message || ''].filter(Boolean).join(' ')
}
