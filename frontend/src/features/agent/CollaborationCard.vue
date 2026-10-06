<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { getCollaboration, reviewCollaboration, cancelCollaboration, type Collaboration } from '@/services/agentManagement'
import BudgetConfirmation from './BudgetConfirmation.vue'
import RunActivity from './RunActivity.vue'
import { statusLabel } from '@/utils/statusLabels'
import { t } from '@/i18n'
const props = defineProps<{ id: string }>()
const workspace = useWorkspaceStore()
const group = ref<Collaboration>()
const error = ref('')
const busy = ref(false)
const groupError = computed(() => group.value?.status === 'cancelled' && group.value.error === 'CancelledError'
  ? '' : group.value?.error)
let generation = 0
let refreshId = 0
let timer: ReturnType<typeof setTimeout> | undefined
const budgetEvents = computed(() => group.value?.budget_request ? [{ event: 'BudgetRequired' as const, sequence: group.value.revision,
  timestamp: '', run_id: props.id, data: group.value.budget_request }] : [])
async function refresh(version = generation) {
  const request = ++refreshId
  clearTimeout(timer)
  try {
    const result = await getCollaboration(props.id)
    if (version !== generation || request !== refreshId) return
    group.value = result; error.value = ''
    if (['running', 'waiting_budget'].includes(result.status)) timer = setTimeout(() => void refresh(version), 2000)
  } catch (cause) { if (version === generation && request === refreshId) error.value = cause instanceof Error ? cause.message : String(cause) }
}
watch(() => [props.id, workspace.vaultId], () => { generation++; group.value = undefined; busy.value = false; error.value = ''; void refresh() }, { immediate: true })
onBeforeUnmount(() => { generation++; clearTimeout(timer) })
async function act(operation: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  const version = generation
  try { await operation(); if (version === generation) await refresh(version) }
  catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
</script>

<template>
  <section class="panel collaboration-card">
    <header class="inline-actions"><strong>{{ group?.title || t('协作任务', 'Collaboration') }}</strong><span class="badge" :class="{ success: group?.status === 'completed', error: group?.status === 'failed', warning: ['awaiting_confirmation', 'waiting_budget'].includes(group?.status || ''), info: group?.status === 'running' }">{{ statusLabel(group?.status) }}</span>
      <button class="button-secondary" @click="refresh()">{{ t('刷新', 'Refresh') }}</button></header>
    <p v-if="error || groupError" class="error-banner" role="alert">{{ error || groupError }}</p>
    <template v-if="group">
      <p>{{ t('完成', 'Completed') }} {{ group.members.filter(member => member.status === 'completed').length }} / {{ group.members.length }} · Token {{ group.token_usage }} / {{ group.plan.token_budget }}</p>
      <div v-if="group.status === 'awaiting_confirmation'" class="inline-actions">
        <span>{{ t('确认分工后才会开始执行', 'Execution starts only after you approve this plan') }}</span>
        <button class="button-primary" :disabled="busy" @click="act(() => reviewCollaboration(group!, 'approve'))">{{ t('确认分工并开始', 'Approve plan and start') }}</button>
        <button class="button-secondary" :disabled="busy" @click="act(() => reviewCollaboration(group!, 'reject'))">{{ t('拒绝', 'Reject') }}</button>
      </div>
      <button v-if="['running', 'waiting_budget'].includes(group.status)" class="button-danger" :disabled="busy" @click="act(() => cancelCollaboration(id))">{{ t('停止整个协作', 'Stop collaboration') }}</button>
      <BudgetConfirmation :run-id="id" :collaboration-id="id" :status="group.status === 'waiting_budget' ? 'waiting_budget' : undefined" :events="budgetEvents" @resolved="refresh()" />
      <details v-for="member in group.members" :key="member.member_id" class="member ui-disclosure" :open="group.status === 'awaiting_confirmation' || ['waiting_permission', 'waiting_budget'].includes(member.status)">
        <summary><span>{{ member.definition.config.name }}</span><span class="badge" :class="{ success: member.status === 'completed', error: member.status === 'failed', warning: ['waiting_permission', 'waiting_budget'].includes(member.status), info: member.status === 'running' }">{{ member.status === 'waiting_budget' ? t('等待继续确认', 'Waiting for continuation') : statusLabel(member.status) }}</span></summary>
        <p>{{ member.input }}</p>
        <p v-if="member.depends_on.length">{{ t('依赖', 'Depends on') }}: {{ member.depends_on.map(id => group!.members.find(item => item.member_id === id)?.definition.config.name || id).join('、') }}</p>
        <p class="subtle">{{ member.definition.config.model }} · {{ member.definition.config.tools.join(', ') || t('无工具', 'No tools') }}</p>
        <button v-if="['pending', 'queued', 'running', 'waiting_permission'].includes(member.status) && group.status !== 'awaiting_confirmation'" class="button-secondary" :disabled="busy" @click="act(() => cancelCollaboration(id, member.member_id))">{{ t('停止该成员', 'Stop member') }}</button>
        <RunActivity v-if="member.run_id" :run-id="member.run_id" />
      </details>
    </template>
  </section>
</template>

<style scoped>
.collaboration-card { display: grid; gap: var(--space-sm); margin-block: var(--space-sm); min-width: 0; overflow-wrap: anywhere; color: var(--color-text-primary); }
.collaboration-card > header strong { min-width: 0; overflow-wrap: anywhere; }
.member { min-width: 0; background: var(--color-surface-secondary); }
.member > summary { justify-content: flex-start; flex-wrap: wrap; }
.member > summary::after { margin-left: auto; }
.member p { margin-block: var(--space-sm); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
