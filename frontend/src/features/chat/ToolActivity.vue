<script setup lang="ts">
import { computed } from 'vue'
import type { ToolCall } from '@/contracts'
import { statusLabel } from '@/utils/statusLabels'
import RunActivity from '@/features/agent/RunActivity.vue'
import CollaborationCard from '@/features/agent/CollaborationCard.vue'
import DefinitionChange from '@/features/agent/DefinitionChange.vue'
import { t } from '@/i18n'
const props = defineProps<{ call: ToolCall }>()
const result = computed<Record<string, unknown>>(() => { try { const value = JSON.parse(props.call.result || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} } catch { return {} } })
const objectName = computed(() => { const name = result.value.name || result.value.title || (result.value.config as Record<string, unknown> | undefined)?.name; return typeof name === 'string' ? name : '' })
function identifier(key: string, prefix: string) { const id = result.value[key]; return typeof id === 'string' && new RegExp(`^${prefix}_[a-zA-Z0-9]+$`).test(id) ? id : '' }
const title = computed(() => ({ 'agent.define': t('创建智能体配置', 'Create Agent definition'), 'agent.start': t('启动智能体任务', 'Start Agent task'),
  'agent.create': t('创建并启动临时任务', 'Create and start task'), 'agent.collaborate': t('规划协作分工', 'Plan collaboration'),
  'agent.search_tools': t('查找可用工具', 'Discover available tools'), 'agent.list': t('查找智能体', 'Find Agents'),
  'agent.inspect': t('读取智能体配置', 'Read Agent definition'), 'agent.status': t('查询任务状态', 'Read task status'),
  'agent.propose_update': t('提出配置变更', 'Propose configuration change'), 'agent.propose_delete': t('提出删除请求', 'Propose deletion'),
  'rag.search': t('检索知识库', 'Search knowledge base') } as Record<string, string>)[props.call.name] || props.call.name)
</script>
<template>
  <div class="tool-activity">
    <details class="ui-disclosure tool-calls"><summary>{{ title }} <span class="badge">{{ statusLabel(call.status) }}</span> {{ objectName }}</summary>
      <p v-if="result.status">{{ t('返回状态', 'Returned status') }}: {{ statusLabel(String(result.status)) }}</p>
      <p v-if="call.error_message" role="alert">{{ call.error_message }}</p>
      <details><summary>{{ t('参数与返回详情', 'Parameters and result') }}</summary><pre>{{ JSON.stringify({ parameters: call.parameters, result }, null, 2) }}</pre></details>
    </details>
    <RunActivity v-if="identifier('run_id', 'run')" :run-id="identifier('run_id', 'run')" />
    <CollaborationCard v-if="identifier('collaboration_id', 'collaboration')" :id="identifier('collaboration_id', 'collaboration')" />
    <DefinitionChange v-if="identifier('change_id', 'change')" :id="identifier('change_id', 'change')" />
  </div>
</template>
<style scoped>.tool-activity { margin-block: var(--space-sm); min-width: 0; } pre { max-height: 240px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--font-size-xs); } summary { cursor: pointer; }</style>
