<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStore } from '@/stores/agent'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'

const route = useRoute()
const router = useRouter()
const agentStore = useAgentStore()
const providerStore = useProviderStore()
const skillStore = useSkillStore()
const pageError = ref('')
const form = reactive({
  input: '', provider_id: 'mock', model: 'mock-1', skill_id: '', max_steps: 10,
  tool_timeout_seconds: 30, run_timeout_seconds: 300, token_budget: 8000,
  allow_network: false, max_concurrent_tools: 1, allowed_tools: [] as string[],
})

const models = computed(() => providerStore.modelsByProvider[form.provider_id] ?? [])
const isNewRun = computed(() => !route.params.runId)

onMounted(async () => {
  try {
    await Promise.all([providerStore.loadProviders(), skillStore.loadSkills(), agentStore.loadTools()])
    await providerStore.loadModels(form.provider_id)
  } catch (error) { pageError.value = error instanceof Error ? error.message : 'Agent 配置加载失败' }
})

watch(() => route.params.runId, async (runId) => {
  if (typeof runId !== 'string') return
  try { await agentStore.loadRun(runId) } catch (error) { pageError.value = error instanceof Error ? error.message : 'Run 加载失败' }
}, { immediate: true })

watch(() => form.provider_id, async (providerId) => {
  try { await providerStore.loadModels(providerId); form.model = models.value[0]?.model_id ?? '' } catch { /* page keeps current selection */ }
})

function toggleTool(name: string) {
  const index = form.allowed_tools.indexOf(name)
  if (index >= 0) form.allowed_tools.splice(index, 1)
  else form.allowed_tools.push(name)
}

async function createRun() {
  pageError.value = ''
  try {
    const run = await agentStore.createRun({
      input: form.input, provider_id: form.provider_id, model: form.model,
      skill_id: form.skill_id || undefined, allowed_tools: form.allowed_tools,
      max_steps: form.max_steps, tool_timeout_seconds: form.tool_timeout_seconds,
      run_timeout_seconds: form.run_timeout_seconds, token_budget: form.token_budget,
      allow_network: form.allow_network, max_concurrent_tools: form.max_concurrent_tools,
    })
    await router.replace({ name: 'agent', params: { runId: run.run_id } })
  } catch (error) { pageError.value = error instanceof Error ? error.message : 'Run 创建失败' }
}

function eventText(data: Record<string, unknown>) {
  return String(data.text ?? data.message ?? data.code ?? '')
}
</script>

<template>
  <section class="feature-page agent-page">
    <header class="feature-header"><div><h1>{{ isNewRun ? '创建 Agent Run' : 'Agent Trace' }}</h1><p>配置执行边界，并实时查看模型、工具和权限事件。</p></div>
      <button v-if="!isNewRun" class="button-secondary" @click="router.push({ name: 'agent' })">新建 Run</button></header>
    <div v-if="pageError || agentStore.error" class="error-banner">{{ pageError || agentStore.error }}</div>
    <form v-if="isNewRun" class="panel run-form" @submit.prevent="createRun">
      <div class="field"><label>任务</label><textarea v-model="form.input" class="textarea" required placeholder="描述希望 Agent 完成的任务" /></div>
      <div class="form-grid">
        <div class="field"><label>Provider</label><select v-model="form.provider_id" class="select"><option v-for="p in providerStore.enabledProviders" :key="p.provider_id" :value="p.provider_id">{{ p.name }}</option></select></div>
        <div class="field"><label>Model</label><select v-model="form.model" class="select"><option v-for="m in models" :key="m.model_id" :value="m.model_id">{{ m.name }}</option></select></div>
        <div class="field"><label>Skill</label><select v-model="form.skill_id" class="select"><option value="">不使用 Skill</option><option v-for="s in skillStore.readySkills" :key="s.skill_id" :value="s.skill_id">{{ s.name }}</option></select></div>
        <div class="field"><label>最大步骤</label><input v-model.number="form.max_steps" class="input" type="number" min="1" max="100" /></div>
        <div class="field"><label>Tool Timeout（秒）</label><input v-model.number="form.tool_timeout_seconds" class="input" type="number" min="1" /></div>
        <div class="field"><label>Run Timeout（秒）</label><input v-model.number="form.run_timeout_seconds" class="input" type="number" min="1" /></div>
        <div class="field"><label>Token Budget</label><input v-model.number="form.token_budget" class="input" type="number" min="1" /></div>
        <div class="field"><label>最大并发工具</label><input v-model.number="form.max_concurrent_tools" class="input" type="number" min="1" /></div>
      </div>
      <div class="field"><label>允许的 Tool</label><div class="tool-grid"><label v-for="tool in agentStore.tools" :key="tool.name" class="tool-option"><input type="checkbox" :checked="form.allowed_tools.includes(tool.name)" @change="toggleTool(tool.name)" /><span><strong>{{ tool.name }}</strong><small>{{ tool.description }}</small></span></label></div></div>
      <label class="network"><input v-model="form.allow_network" type="checkbox" /> 允许本次 Run 调用网络工具</label>
      <div class="inline-actions"><button class="button-primary" :disabled="agentStore.isCreating || !form.input.trim()">{{ agentStore.isCreating ? '创建中…' : '创建并运行' }}</button></div>
    </form>

    <div v-else class="trace-layout">
      <div class="panel run-summary"><div><span class="badge info">{{ agentStore.activeRun?.status }}</span><h2>{{ agentStore.activeRunId }}</h2></div><div class="inline-actions"><span>步骤 {{ agentStore.currentStep }} / {{ agentStore.activeRun?.max_steps }}</span><button v-if="agentStore.isRunning" class="button-danger" @click="agentStore.cancelRun(agentStore.activeRunId!)">取消运行</button></div></div>
      <div class="timeline">
        <article v-for="event in agentStore.events" :key="event.sequence" class="event-card item-card">
          <div class="event-head"><span class="badge" :class="{ success: event.event === 'RunCompleted', error: event.event === 'RunFailed', warning: event.event === 'PermissionRequired' }">{{ event.event }}</span><span>#{{ event.sequence }} · {{ new Date(event.timestamp).toLocaleTimeString() }}</span></div>
          <p v-if="eventText(event.data)" class="event-text">{{ eventText(event.data) }}</p>
          <pre v-if="['ToolCall', 'ToolResult', 'Citation'].includes(event.event)">{{ JSON.stringify(event.data, null, 2) }}</pre>
        </article>
        <div v-if="!agentStore.events.length" class="empty-state"><div><strong>等待 Trace</strong><p>事件连接建立后将在这里实时显示。</p></div></div>
      </div>
    </div>

    <div v-if="agentStore.permissionRequest" class="modal-backdrop">
      <div class="modal"><span class="badge warning">权限确认</span><h2>{{ agentStore.permissionRequest.tool_name }}</h2><p>{{ agentStore.permissionRequest.impact }}</p><p class="subtle">权限：{{ agentStore.permissionRequest.permission }}</p><pre>{{ JSON.stringify(agentStore.permissionRequest.parameters, null, 2) }}</pre><div class="inline-actions permission-actions"><button class="button-primary" @click="agentStore.respondPermission('allow', 'once')">仅本次允许</button><button class="button-secondary" @click="agentStore.respondPermission('allow', 'session')">本次会话允许</button><button class="button-danger" @click="agentStore.respondPermission('deny')">拒绝</button></div></div>
    </div>
  </section>
</template>

<style scoped>
.run-form { display: grid; gap: var(--space-xl); max-width: 980px; }
.tool-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-sm); }
.tool-option { display: flex; gap: var(--space-sm); padding: var(--space-sm); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
.tool-option small { display: block; color: var(--color-text-secondary); }
.network { display: flex; gap: var(--space-sm); }
.trace-layout { display: grid; gap: var(--space-lg); }
.run-summary, .event-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); }
.run-summary h2 { margin-top: var(--space-sm); font-family: var(--font-ui-mono); font-size: var(--font-size-lg); }
.timeline { display: grid; gap: var(--space-md); }
.event-head { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.event-text { margin-top: var(--space-md); white-space: pre-wrap; line-height: var(--line-height-relaxed); }
pre { margin-top: var(--space-md); max-height: 260px; overflow: auto; padding: var(--space-md); border-radius: var(--radius-md); background: var(--color-background-secondary); font-family: var(--font-ui-mono); font-size: var(--font-size-xs); white-space: pre-wrap; user-select: text; }
.permission-actions { margin-top: var(--space-lg); }
</style>
