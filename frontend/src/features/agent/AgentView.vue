<script setup lang="ts">
import AppDialog from '@/components/common/AppDialog.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStore } from '@/stores/agent'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import TraceTimeline from './TraceTimeline.vue'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import type { AgentEvent } from '@/contracts'
import { localizeDetails, permissionLabel, runStatusLabel, toolLabel } from './labels'
import ToolOption from './ToolOption.vue'
import { useCitationNavigation } from '@/composables/useCitationNavigation'
import { localeTag, t } from '@/i18n'

const route = useRoute()
const router = useRouter()
const agentStore = useAgentStore()
const providerStore = useProviderStore()
const skillStore = useSkillStore()
const { openCitation } = useCitationNavigation()
const pageError = ref('')
const form = reactive({
  input: '', provider_id: '', model: '', skill_id: '', max_steps: 10,
  tool_timeout_seconds: 30, run_timeout_seconds: 300, limit_token_budget: false, token_budget: 8000,
  allow_network: false, max_concurrent_tools: 1, allowed_tools: [] as string[],
})

const models = computed(() => providerStore.modelsByProvider[form.provider_id] ?? [])
const isNewRun = computed(() => !route.params.runId)
const toolQuery = ref('')
const visibleTools = computed(() => agentStore.tools.filter(tool => `${tool.name} ${tool.description}`.toLowerCase().includes(toolQuery.value.trim().toLowerCase())))
const output = computed(() => agentStore.activeRun?.output || [...agentStore.events].reverse().find(event => event.event === 'RunCompleted')?.data.output || '')
const currentActivity = computed(() => {
  const event = [...agentStore.events].reverse().find(event => ['ToolCall', 'ModelCallStarted', 'PermissionRequired', 'RunCompleted', 'RunFailed', 'RunCancelled'].includes(event.event))
  if (!event) return t('等待开始', 'Waiting to start')
  if (event.event === 'ToolCall') return `${t('正在调用', 'Using')} ${toolLabel(String(event.data.name || ''))}`
  if (event.event === 'ModelCallStarted') return t('模型正在处理任务', 'The model is working on the task')
  if (event.event === 'PermissionRequired') return t('等待您确认操作权限', 'Waiting for your permission')
  return eventText(event) || t('运行已结束，请检查结果', 'Run ended; review the result')
})

onMounted(async () => {
  try {
    await Promise.all([providerStore.loadProviders(), skillStore.loadSkills(), agentStore.loadTools()])
    form.provider_id = providerStore.defaultProviderId
  } catch (error) { pageError.value = error instanceof Error ? error.message : t('智能体配置加载失败', 'Failed to load agent configuration') }
})

watch(() => route.params.runId, async (runId) => {
  if (typeof runId !== 'string') return
  try { await agentStore.loadRun(runId) } catch (error) { pageError.value = error instanceof Error ? error.message : t('运行记录加载失败', 'Failed to load run') }
}, { immediate: true })

watch(() => form.provider_id, async (providerId) => {
  form.model = providerStore.providers.find(p => p.provider_id === providerId)?.default_model ?? ''
  if (!providerId) return
  try { await providerStore.loadModels(providerId) }
  catch (error) { if (form.provider_id === providerId) pageError.value = error instanceof Error ? error.message : t('模型列表加载失败，请手动填写模型 ID。', 'Unable to load models. Enter a model ID manually.') }
})

function toggleTool(name: string) {
  const index = form.allowed_tools.indexOf(name)
  if (index >= 0) form.allowed_tools.splice(index, 1)
  else form.allowed_tools.push(name)
}

async function createRun() {
  pageError.value = ''
  try {
    if (!form.provider_id || !form.model.trim()) throw new Error(t('请选择提供商并填写模型 ID。', 'Select a provider and enter a model ID.'))
    const run = await agentStore.createRun({
      input: form.input, provider_id: form.provider_id, model: form.model,
      skill_id: form.skill_id || undefined, allowed_tools: form.allowed_tools,
      max_steps: form.max_steps, tool_timeout_seconds: form.tool_timeout_seconds,
      run_timeout_seconds: form.run_timeout_seconds, token_budget: form.limit_token_budget ? form.token_budget : null,
      allow_network: form.allow_network, max_concurrent_tools: form.max_concurrent_tools,
    })
    await router.replace({ name: 'agent', params: { runId: run.run_id } })
  } catch (error) { pageError.value = error instanceof Error ? error.message : t('运行创建失败', 'Failed to create run') }
}

function eventText(event: AgentEvent) {
  if (event.event === 'RunCompleted') return t('任务已成功完成。', 'The task completed successfully.')
  if (event.event === 'RunCancelled') return t('任务已取消。', 'The task was cancelled.')
  const text = event.data.text ?? event.data.message ?? event.data.code
  if (text) return String(text)
  return ''
}

/** Trace 里点引用 → 打开对应笔记块。失败原因要让用户看到，不能静默。 */
async function handleOpenCitation(data: Record<string, unknown>) {
  pageError.value = ''
  try {
    await openCitation(data)
  } catch (error) {
    pageError.value = error instanceof Error ? error.message : t('引用定位失败', 'Failed to open citation')
  }
}
</script>

<template>
  <section class="feature-page agent-page">
    <header class="feature-header"><div><h1>{{ isNewRun ? t('智能体', 'Agent') : t('任务进展', 'Task progress') }}</h1><p>{{ t('描述目标，选择模型与技能，检查执行结果。', 'Describe a goal, choose a model and skill, then review the result.') }}</p></div>
      <button v-if="!isNewRun" class="button-secondary" @click="router.push({ name: 'agent' })">{{ t('新建运行', 'New run') }}</button></header>
    <div v-if="pageError || agentStore.error || providerStore.error" class="error-banner">{{ pageError || agentStore.error || providerStore.error }}</div>
    <form v-if="isNewRun" class="panel run-form" @submit.prevent="createRun">
      <div class="field"><label>{{ t('任务', 'Task') }}</label><textarea v-model="form.input" class="textarea" required :placeholder="t('描述希望智能体完成的任务', 'Describe the task for the agent')" /></div>
      <div class="form-grid">
        <div class="field"><label>{{ t('模型提供商', 'Model provider') }}</label><select v-model="form.provider_id" class="select"><option v-for="p in providerStore.enabledProviders" :key="p.provider_id" :value="p.provider_id">{{ p.name }}</option></select></div>
        <div class="field"><label>{{ t('模型', 'Model') }}</label><input v-model="form.model" class="input" list="agent-models" :placeholder="t('填写模型 ID', 'Enter model ID')" required /><datalist id="agent-models"><option v-for="m in models" :key="m.model_id" :value="m.model_id">{{ m.name }}</option></datalist></div>
        <div class="field"><label>{{ t('技能', 'Skill') }}</label><select v-model="form.skill_id" class="select"><option value="">{{ t('不使用技能', 'No skill') }}</option><optgroup :label="t('已安装 Skill', 'Installed Skills')"><option v-for="s in skillStore.readySkills" :key="s.skill_id" :value="s.skill_id">{{ s.name }}</option></optgroup><optgroup :label="t('当前库的用户 Skill', 'User Skills in this Vault')"><option v-for="s in skillStore.readyUserSkills" :key="s.skill_id" :value="s.skill_id">{{ s.data.name }}</option></optgroup></select></div>
      </div>
      <details class="ui-disclosure"><summary>{{ t('执行边界与高级设置', 'Execution limits and advanced settings') }}</summary><div class="form-grid">
        <div class="field"><label>{{ t('最大步骤', 'Maximum steps') }}</label><input v-model.number="form.max_steps" class="input" type="number" min="1" max="100" /></div>
        <div class="field"><label>{{ t('工具超时（秒）', 'Tool timeout (seconds)') }}</label><input v-model.number="form.tool_timeout_seconds" class="input" type="number" min="1" /></div>
        <div class="field"><label>{{ t('运行超时（秒）', 'Run timeout (seconds)') }}</label><input v-model.number="form.run_timeout_seconds" class="input" type="number" min="1" /></div>
        <div class="field budget-field"><label><input v-model="form.limit_token_budget" type="checkbox" />{{ t('限制令牌消耗', 'Limit token usage') }}</label><input v-if="form.limit_token_budget" v-model.number="form.token_budget" class="input" type="number" min="1" :aria-label="t('令牌上限', 'Token limit')" /><small v-else class="subtle">{{ t('默认不限制；仍可随时取消运行。', 'Unlimited by default; the run can still be cancelled at any time.') }}</small></div>
        <div class="field"><label>{{ t('最大并发工具', 'Maximum concurrent tools') }}</label><input v-model.number="form.max_concurrent_tools" class="input" type="number" min="1" /></div>
      </div></details>
      <details class="ui-disclosure"><summary>{{ t('工具与权限', 'Tools and permissions') }} · {{ t(`已选 ${form.allowed_tools.length} 项`, `${form.allowed_tools.length} selected`) }}</summary><p class="subtle">{{ t('留空时使用所选 Skill 的工具范围；未选 Skill 时需要手动选择工具。权限确认仍然生效。', 'Leave empty to use the selected Skill’s tools, or select tools manually without a Skill. Permission checks still apply.') }}</p><input v-model="toolQuery" class="input" :placeholder="t('搜索工具名称或用途', 'Search tools by name or purpose')" :aria-label="t('筛选工具', 'Filter tools')" /><div class="tool-grid"><ToolOption v-for="tool in visibleTools" :key="tool.name" :name="tool.name" :description="tool.description" :selected="form.allowed_tools.includes(tool.name)" @toggle="toggleTool" /></div></details>
      <label class="network"><input v-model="form.allow_network" type="checkbox" /> {{ t('允许本次运行调用网络工具', 'Allow network tools for this run') }}</label>
      <div class="inline-actions"><button class="button-primary" :disabled="agentStore.isCreating || !form.input.trim() || !form.provider_id || !form.model.trim()">{{ agentStore.isCreating ? t('创建中…', 'Creating…') : t('创建并运行', 'Create and run') }}</button></div>
    </form>

    <div v-else class="trace-layout">
      <div class="panel run-summary">
        <div>
          <span class="badge" :class="{
            success: agentStore.activeRun?.status === 'completed',
            error: agentStore.activeRun?.status === 'failed',
            warning: agentStore.activeRun?.status === 'waiting_permission',
            info: agentStore.activeRun?.status === 'running' || agentStore.activeRun?.status === 'queued',
          }">{{ runStatusLabel(agentStore.activeRun?.status) }}</span>
          <h2>{{ agentStore.activeRun?.input || t('智能体任务', 'Agent task') }}</h2>
          <p class="activity" role="status">{{ currentActivity }}</p>
          <p v-if="agentStore.activeRun" class="run-meta">
            <span>{{ t('步骤', 'Step') }} {{ agentStore.currentStep }} / {{ agentStore.activeRun.max_steps }}</span>
            <span>·</span>
            <span>Token: {{ agentStore.activeRun.token_usage?.total_tokens ?? 0 }}</span>
            <span v-if="agentStore.activeRun.started_at">·</span>
            <span v-if="agentStore.activeRun.started_at">{{ t('开始', 'Started') }}: {{ new Date(agentStore.activeRun.started_at).toLocaleString(localeTag()) }}</span>
          </p>
        </div>
        <div class="inline-actions">
          <span v-if="agentStore.connectionState === 'reconnecting'">{{ t('正在恢复连接…', 'Reconnecting…') }}</span>
          <button v-if="agentStore.connectionState === 'disconnected'" class="button-secondary" @click="agentStore.reconnect()">{{ t('恢复连接', 'Reconnect') }}</button>
          <button v-if="agentStore.isRunning" class="button-danger" @click="agentStore.cancelRun(agentStore.activeRunId!)">{{ t('取消运行', 'Cancel run') }}</button>
          <button class="button-secondary" @click="agentStore.loadRun(agentStore.activeRunId!)">{{ t('重新加载', 'Reload') }}</button>
        </div>
      </div>
      <section v-if="output" class="panel agent-result"><h2>{{ t('执行结果', 'Result') }}</h2><MarkdownContent :source="String(output)" /></section>
      <p v-if="agentStore.activeRun?.error" class="error-banner" role="alert">{{ agentStore.activeRun.error }}</p>
      <details class="ui-disclosure trace-disclosure"><summary>{{ t('执行详情与来源', 'Execution details and sources') }} · {{ agentStore.events.length }}</summary><TraceTimeline
        :events="agentStore.events"
        :run-status="agentStore.activeRun?.status"
        @open-citation="handleOpenCitation"
      /></details>
    </div>

    <AppDialog v-if="agentStore.permissionRequest" :label="t('权限确认', 'Permission confirmation')" :dismissible="false">
      <div class="modal"><span class="badge warning">{{ t('权限确认', 'Permission Confirmation') }}</span><h2>{{ toolLabel(agentStore.permissionRequest.tool_name) }}</h2><p>{{ agentStore.permissionRequest.impact }}</p><p class="subtle">{{ t('所需权限：', 'Required permission: ') }}{{ permissionLabel(agentStore.permissionRequest.permission) }} ({{ agentStore.permissionRequest.permission }})</p><pre>{{ JSON.stringify(localizeDetails(agentStore.permissionRequest.parameters), null, 2) }}</pre><div class="inline-actions permission-actions"><button class="button-primary" @click="agentStore.respondPermission('allow', 'once')">{{ t('仅本次允许', 'Allow once') }}</button><button class="button-secondary" @click="agentStore.respondPermission('allow', 'session')">{{ t('本次会话允许', 'Allow for session') }}</button><button class="button-danger" @click="agentStore.respondPermission('deny')">{{ t('拒绝', 'Deny') }}</button></div></div>
    </AppDialog>
  </section>
</template>

<style scoped>
.agent-page > * { width: min(100%, 1080px); margin-inline: auto; }
.run-form { display: grid; gap: var(--space-xl); }
.budget-field {
  min-height: 78px;
  align-content: center;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-surface-secondary);
}
.budget-field > label { color: var(--color-text-primary); }
.budget-field > .input { background: var(--color-surface-primary); }
.tool-grid { display: grid; align-items: start; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-sm); }
.network { display: flex; gap: var(--space-sm); }
.trace-layout { display: grid; gap: var(--space-lg); }
.run-summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
}
.run-summary h2 {
  margin-top: var(--space-sm);
  font-size: var(--font-size-lg);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.run-meta {
  display: flex;
  gap: var(--space-sm);
  margin-top: var(--space-xs);
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.permission-actions { margin-top: var(--space-lg); }
.activity { color: var(--color-text-secondary); margin-top: 12px; }
.tool-grid { max-height: 360px; overflow: auto; margin-top: 12px; }
.agent-result h2 { margin-bottom: 16px; font-size: var(--font-size-lg); }
.run-meta { flex-wrap: wrap; }
@media(max-width:720px) { .run-summary { flex-direction: column; } }
</style>
