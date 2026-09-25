<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useAgentStore } from '@/stores/agent'
import { useProviderStore } from '@/stores/provider'
import { listDefinitions, createDefinition, updateDefinition, deleteDefinition, listCollaborations,
  type AgentConfig, type AgentDefinition, type Collaboration } from '@/services/agentManagement'
import api from '@/services/apiClient'
import RunActivity from './RunActivity.vue'
import CollaborationCard from './CollaborationCard.vue'
import { t } from '@/i18n'
const workspace = useWorkspaceStore()
const agentStore = useAgentStore()
const providers = useProviderStore()
const definitions = ref<AgentDefinition[]>([])
const groups = ref<Collaboration[]>([])
const error = ref('')
const busy = ref(false)
const editing = ref<AgentDefinition | null>(null)
const showForm = ref(false)
const confirm = ref<'save' | 'delete' | null>(null)
const fresh = (): AgentConfig => ({ name: '', role: '', instructions: '', provider_id: providers.defaultProviderId,
  model: providers.providers.find(item => item.provider_id === providers.defaultProviderId)?.default_model || '', tools: [], token_budget: null, max_steps: 10, enabled: true })
const form = ref<AgentConfig>(fresh())
const selected = ref('')
const task = ref('')
const launched = ref('')
const operationId = ref(crypto.randomUUID())
const members = ref([{ member_id: 'member1', agent_id: '', input: '', dependencies: '' }])
const title = ref('')
const concurrency = ref(2)
const budget = ref(24000)
let generation = 0
let scopeGeneration = 0
async function load() {
  const version = ++generation
  try {
    const [agents, collaborations] = await Promise.all([listDefinitions(), listCollaborations()])
    if (version !== generation) return
    definitions.value = agents.items; groups.value = collaborations.items; error.value = ''
  } catch (cause) { if (version === generation) error.value = String(cause) }
}
onMounted(load)
watch(() => workspace.vaultId, () => { scopeGeneration++; generation++; definitions.value = []; groups.value = []; launched.value = ''; showForm.value = false; confirm.value = null; busy.value = false; error.value = ''; selected.value = ''; task.value = ''; editing.value = null; form.value = fresh(); title.value = ''; members.value = [{ member_id: 'member1', agent_id: '', input: '', dependencies: '' }]; void load() })
onBeforeUnmount(() => { generation++; scopeGeneration++ })
watch([selected, task], () => { operationId.value = crypto.randomUUID() })
async function action(operation: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  const version = scopeGeneration
  try { await operation(); if (version === scopeGeneration) await load() }
  catch (cause) { if (version === scopeGeneration) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === scopeGeneration) busy.value = false }
}
function edit(definition?: AgentDefinition) {
  editing.value = definition || null
  form.value = definition ? structuredClone(JSON.parse(JSON.stringify(definition.config))) : fresh()
  showForm.value = true
}
async function save() {
  const version = scopeGeneration
  await action(async () => {
    if (editing.value) await updateDefinition(editing.value, form.value)
    else await createDefinition(form.value)
    if (version === scopeGeneration) { confirm.value = null; showForm.value = false }
  })
}
async function remove() {
  if (!editing.value) return
  const version = scopeGeneration
  await action(async () => { await deleteDefinition(editing.value!); if (version === scopeGeneration) { confirm.value = null; showForm.value = false } })
}
async function start() {
  const definition = definitions.value.find(item => item.id === selected.value)
  if (!definition) return
  const version = scopeGeneration
  await action(async () => {
    const run = await api.post<{run_id: string}>(`/api/agent/definitions/${definition.id}/runs`, { input: task.value,
      expected_revision: definition.revision, operation_id: operationId.value })
    if (version === scopeGeneration) launched.value = run.run_id
  })
}
async function plan() {
  await action(async () => {
    await api.post('/api/agent/collaborations', { title: title.value, max_concurrency: concurrency.value, token_budget: budget.value,
      members: members.value.map(member => ({ member_id: member.member_id, agent_id: member.agent_id, input: member.input,
        depends_on: member.dependencies.split(',').map(value => value.trim()).filter(Boolean) })) })
  })
}
</script>

<template>
  <section class="manager panel">
    <header class="inline-actions"><h2>{{ t('我的智能体', 'My Agents') }}</h2><button class="button-primary" @click="edit()">{{ t('新建配置', 'New definition') }}</button><button class="button-secondary" @click="load()">{{ t('刷新', 'Refresh') }}</button></header>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="!definitions.length" class="subtle">{{ t('创建可复用的智能体配置，或在 AI 对话中让模型协助创建。', 'Create a reusable Agent here or ask the model to help in chat.') }}</p>
    <div class="definition-list"><article v-for="definition in definitions" :key="definition.id" class="panel definition">
      <strong>{{ definition.config.name }}</strong><span class="badge">{{ definition.config.enabled ? t('启用', 'Enabled') : t('停用', 'Disabled') }} · r{{ definition.revision }}</span>
      <span class="badge">{{ definition.origin === 'manual' ? t('手动创建', 'Created manually') : definition.origin === 'chat' ? t('AI 对话创建', 'Created in AI chat') : definition.origin === 'benchmark' ? t('评测创建', 'Created for evaluation') : t('历史配置', 'Existing definition') }}</span>
      <small>Token: {{ definition.config.token_budget ?? t('不设上限', 'No limit') }}</small>
      <p>{{ definition.config.role }}</p><small>{{ definition.config.model }} · {{ definition.config.tools.length }} {{ t('工具', 'tools') }}</small>
      <button class="button-secondary" @click="edit(definition)">{{ t('编辑与管理', 'Edit and manage') }}</button>
    </article></div>
    <form v-if="showForm" class="panel config-form" @submit.prevent="confirm = 'save'">
      <h3>{{ editing ? t('编辑配置', 'Edit definition') : t('创建配置', 'Create definition') }}</h3>
      <label>{{ t('名称', 'Name') }}<input v-model="form.name" required maxlength="100" class="input" /></label>
      <label>{{ t('职责', 'Role') }}<input v-model="form.role" maxlength="1000" class="input" /></label>
      <label>{{ t('指令', 'Instructions') }}<textarea v-model="form.instructions" maxlength="12000" class="textarea" /></label>
      <div class="form-grid"><label>{{ t('提供商', 'Provider') }}<select v-model="form.provider_id" class="select" required><option v-for="provider in providers.enabledProviders" :key="provider.provider_id" :value="provider.provider_id">{{ provider.name }}</option></select></label>
      <label>{{ t('模型', 'Model') }}<input v-model="form.model" class="input" required /></label>
      <label><span>{{ t('设置 Token 预算上限', 'Set a token budget limit') }}</span><input type="checkbox" :checked="form.token_budget !== null" @change="form.token_budget = ($event.target as HTMLInputElement).checked ? 8000 : null" /></label>
      <label v-if="form.token_budget !== null">{{ t('Token 预算', 'Token budget') }}<input v-model.number="form.token_budget" class="input" type="number" min="1" max="1000000" required /></label>
      <label>{{ t('步骤上限', 'Step limit') }}<input v-model.number="form.max_steps" class="input" type="number" min="1" max="30" required /></label></div>
      <label><input v-model="form.enabled" type="checkbox" />{{ t('启用此配置', 'Enable this definition') }}</label>
      <p class="subtle">{{ t('手动创建默认不设 Token 上限；步骤、执行时限和提供商限制仍然有效。由聊天委托或加入协作时，仍受该次委托或协作整体预算约束。', 'Manual definitions default to no token limit. Step, timeout and provider limits still apply. Chat delegation and collaboration retain their own overall budgets.') }}</p>
      <details class="ui-disclosure"><summary>{{ t('可用工具', 'Available tools') }} · {{ form.tools.length }}/20</summary><div class="tool-options"><label v-for="tool in agentStore.tools.filter(item => item.permission !== 'network.request' && !item.name.startsWith('agent.'))" :key="tool.name"><input v-model="form.tools" type="checkbox" :value="tool.name" :disabled="form.tools.length >= 20 && !form.tools.includes(tool.name)" />{{ tool.name }} · {{ tool.description }}</label></div></details>
      <div class="inline-actions"><button class="button-primary" :disabled="busy">{{ t('审阅并保存', 'Review and save') }}</button><button type="button" class="button-secondary" @click="showForm = false">{{ t('关闭', 'Close') }}</button><button v-if="editing" type="button" class="button-danger" @click="confirm = 'delete'">{{ t('删除配置', 'Delete definition') }}</button></div>
    </form>
    <details class="ui-disclosure"><summary>{{ t('使用已有配置运行任务', 'Run a saved Agent') }}</summary><form class="config-form" @submit.prevent="start">
      <label>{{ t('智能体', 'Agent') }}<select v-model="selected" class="select" required><option value="">{{ t('请选择', 'Select') }}</option><option v-for="definition in definitions.filter(item => item.config.enabled)" :key="definition.id" :value="definition.id">{{ definition.config.name }}</option></select></label>
      <textarea v-model="task" class="textarea" required maxlength="16000" :aria-label="t('任务内容', 'Task')" />
      <button class="button-primary" :disabled="busy">{{ t('开始执行', 'Start run') }}</button>
    </form></details>
    <RunActivity v-if="launched" :run-id="launched" />
    <details class="ui-disclosure"><summary>{{ t('安排多智能体协作', 'Plan multi-Agent work') }}</summary><form class="config-form" @submit.prevent="plan">
      <label>{{ t('协作名称', 'Collaboration title') }}<input v-model="title" class="input" required maxlength="200" /></label>
      <div v-for="member in members" :key="member.member_id" class="panel config-form"><strong>{{ member.member_id }}</strong>
        <select v-model="member.agent_id" class="select" required :aria-label="`${member.member_id} Agent`"><option value="">{{ t('选择智能体', 'Choose Agent') }}</option><option v-for="definition in definitions.filter(item => item.config.enabled)" :key="definition.id" :value="definition.id">{{ definition.config.name }}</option></select>
        <textarea v-model="member.input" class="textarea" required :aria-label="`${member.member_id} ${t('任务', 'task')}`" :placeholder="t('此成员负责的任务', 'Task for this member')" />
        <label>{{ t('依赖成员编号（逗号分隔，留空表示并行）', 'Dependency IDs (comma-separated; empty for parallel)') }}<input v-model="member.dependencies" class="input" placeholder="member1" /></label>
      </div>
      <button type="button" class="button-secondary" :disabled="members.length >= 6" @click="members.push({ member_id: `member${members.length + 1}`, agent_id: '', input: '', dependencies: '' })">{{ t('添加成员', 'Add member') }}</button>
      <button v-if="members.length > 1" type="button" class="button-secondary" @click="members.pop()">{{ t('移除最后一个成员', 'Remove last member') }}</button>
      <div class="form-grid"><label>{{ t('最大并行数', 'Maximum concurrency') }}<input v-model.number="concurrency" type="number" min="1" max="3" class="input" /></label><label>{{ t('整体 Token 预算', 'Total token budget') }}<input v-model.number="budget" type="number" min="1" max="1000000" class="input" /></label></div>
      <button class="button-primary" :disabled="busy">{{ t('生成分工确认单', 'Prepare plan for review') }}</button>
    </form></details>
    <CollaborationCard v-for="group in groups" :key="group.id" :id="group.id" />
    <AppDialog v-if="confirm" :label="t('确认配置变更', 'Confirm configuration change')" @close="confirm = null"><div class="modal config-form"><h2>{{ confirm === 'delete' ? t('删除配置？', 'Delete definition?') : t('保存配置？', 'Save definition?') }}</h2>
      <p>{{ form.name }} · {{ form.model }} · {{ form.enabled ? t('启用', 'Enabled') : t('停用', 'Disabled') }}</p>
      <p>{{ t('工具范围', 'Tools') }}: {{ form.tools.join(', ') || t('无', 'None') }}</p><p>Token: {{ form.token_budget ?? t('不设上限', 'No limit') }} · {{ t('最大步骤', 'Maximum steps') }}: {{ form.max_steps }}</p>
      <details v-if="editing"><summary>{{ t('查看修改前配置', 'Review previous configuration') }}</summary><pre>{{ JSON.stringify(editing.config, null, 2) }}</pre></details>
      <p>{{ t('已启动的任务保留启动时的配置。工具执行仍按权限规则确认。', 'Existing runs retain their snapshots. Tool execution still follows permission checks.') }}</p>
      <div class="inline-actions"><button class="button-primary" :disabled="busy" @click="confirm === 'delete' ? remove() : save()">{{ t('确认', 'Confirm') }}</button><button class="button-secondary" @click="confirm = null">{{ t('取消', 'Cancel') }}</button></div>
    </div></AppDialog>
  </section>
</template>
<style scoped>
.manager, .config-form { display: grid; gap: var(--space-md); }
.definition-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); gap: var(--space-md); }
.definition { display: grid; gap: var(--space-sm); overflow-wrap: anywhere; }
.config-form label { display: grid; gap: var(--space-xs); }
.tool-options { max-height: 280px; overflow: auto; display: grid; gap: var(--space-sm); }
.tool-options label { display: block; }
pre { white-space: pre-wrap; max-height: 240px; overflow: auto; }
</style>
