<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import type { AgentEvent, AgentRunStatus } from '@/contracts'
import { cancelAgentRun, extendAgentBudget } from '@/services/agentService'
import { t } from '@/i18n'
import api from '@/services/apiClient'
import { cancelCollaboration } from '@/services/agentManagement'

const props = defineProps<{ runId: string; collaborationId?: string; status?: AgentRunStatus; events: AgentEvent[] }>()
const emit = defineEmits<{ resolved: [] }>()
const dismissed = ref('')
const resolved = ref('')
const busy = ref(false)
const error = ref('')
const additional = ref(8000)
let generation = 0
watch(() => [props.runId, props.collaborationId, props.status], () => { generation++; busy.value = false; error.value = '' })
onBeforeUnmount(() => { generation++ })
const pending = computed(() => {
  if (props.status !== 'waiting_budget') return undefined
  const event = [...props.events].reverse().find(item => item.run_id === props.runId && ['BudgetRequired', 'BudgetResolved'].includes(item.event))
  return event?.event === 'BudgetRequired' ? event : undefined
})
const requestId = computed(() => String(pending.value?.data.request_id || ''))
const visible = computed(() => !!requestId.value && requestId.value !== dismissed.value && requestId.value !== resolved.value)
const valid = computed(() => Number.isInteger(additional.value) && additional.value >= 1 && additional.value <= 1000000)
watch(requestId, () => { error.value = ''; additional.value = 8000 })
function dismiss() { if (!busy.value) dismissed.value = requestId.value }
async function decide(continueRun: boolean) {
  if (busy.value || !requestId.value || (continueRun && !valid.value)) return
  const id = requestId.value
  const runId = props.runId
  const version = generation
  busy.value = true
  error.value = ''
  try {
    if (props.collaborationId) {
      if (continueRun) await api.post(`/api/agent/collaborations/${props.collaborationId}/budget/${id}`, { additional_tokens: additional.value })
      else await cancelCollaboration(props.collaborationId)
    } else if (continueRun) await extendAgentBudget(runId, id, additional.value)
    else await cancelAgentRun(runId)
    if (version === generation) { resolved.value = id; emit('resolved') }
  } catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
</script>

<template>
  <section v-if="pending && requestId !== resolved" class="panel budget-notice" role="status">
    <p>{{ t('Token 预算已达到上限，任务已暂停，等待你决定是否继续。', 'The token budget has been reached. The task is paused pending your decision.') }}</p>
    <button class="button-secondary" @click="dismissed = ''">{{ t('处理预算确认', 'Review budget request') }}</button>
  </section>
  <AppDialog v-if="visible" :label="t('是否继续智能体任务？', 'Continue this agent task?')" :dismissible="!busy" @close="dismiss">
    <div class="modal budget-modal">
      <h2>{{ t('Token 预算已达到上限', 'Token budget reached') }}</h2>
      <p>{{ t('任务尚未完成。追加预算后，将从当前暂停点继续，不重新执行已完成的操作。', 'The task is unfinished. Additional budget resumes the paused task without replaying completed operations.') }}</p>
      <p>{{ t('已使用 / 原预算：', 'Used / previous budget: ') }}{{ pending?.data.token_usage }} / {{ pending?.data.token_budget }}</p>
      <p v-if="pending?.data.member_name">{{ t('达到成员预算：', 'Member budget reached: ') }}{{ pending.data.member_name }} · {{ pending.data.member_usage }} / {{ pending.data.member_budget }}</p>
      <p v-if="collaborationId" class="subtle">{{ t('此次追加同时用于整体预算及已达到上限的成员，所有成员仍受整体预算约束。', 'This addition applies to the group and exhausted members; all members remain subject to the group budget.') }}</p>
      <p v-if="pending?.data.estimated" class="subtle">{{ t('提供商未返回完整用量，上述用量包含字符估算，不代表实际账单。', 'The provider did not return complete usage. These counts include character-based estimates, not billable usage.') }}</p>
      <label for="agent-extra-budget">{{ t('追加 Token 预算', 'Additional token budget') }}</label>
      <input id="agent-extra-budget" v-model.number="additional" class="input" type="number" min="1" max="1000000" step="1" :disabled="busy" />
      <p>{{ t('新预算：', 'New budget: ') }}{{ valid ? Math.max(Number(pending?.data.token_usage || 0), Number(pending?.data.token_budget || 0)) + additional : '—' }}</p>
      <p class="subtle">{{ t('继续可能产生额外费用。用量按模型响应结算，单次响应可能超出预算；此操作不会提升提供商的上下文或配额限制。', 'Continuing may incur additional charges. Usage is accounted per response and may overshoot the budget; this does not increase provider context or quota limits.') }}</p>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <div class="inline-actions">
        <button class="button-primary" :disabled="busy || !valid" @click="decide(true)">{{ t('追加预算并继续', 'Add budget and continue') }}</button>
        <button class="button-danger" :disabled="busy" @click="decide(false)">{{ t('停止并保留已有结果', 'Stop and keep existing results') }}</button>
        <button class="button-secondary" :disabled="busy" @click="dismiss">{{ t('稍后决定', 'Decide later') }}</button>
      </div>
    </div>
  </AppDialog>
</template>

<style scoped>
.budget-notice { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-md); }
.budget-modal { display: grid; gap: var(--space-md); width: min(560px, 100%); }
</style>
