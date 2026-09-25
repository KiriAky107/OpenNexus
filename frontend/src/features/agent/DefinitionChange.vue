<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getChange, reviewChange, type AgentChange } from '@/services/agentManagement'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'
const props = defineProps<{ id: string }>()
const workspace = useWorkspaceStore()
const change = ref<AgentChange>()
const error = ref('')
const busy = ref(false)
let version = 0
watch(() => [props.id, workspace.vaultId], async () => {
  const current = ++version; change.value = undefined; busy.value = false; error.value = ''
  try { const result = await getChange(props.id); if (current === version) change.value = result }
  catch (cause) { if (current === version) error.value = String(cause) }
}, { immediate: true })
onBeforeUnmount(() => { version++ })
const fields = computed(() => change.value?.config ? Object.keys(change.value.config).filter(key => JSON.stringify(change.value!.before[key as keyof AgentChange['before']]) !== JSON.stringify(change.value!.config![key as keyof AgentChange['before']])) : [])
async function decide(decision: 'approve' | 'reject') {
  if (!change.value || busy.value) return
  busy.value = true
  const current = version
  try { const result = await reviewChange(change.value, decision); if (current === version) change.value = result }
  catch (cause) { if (current === version) error.value = String(cause) }
  finally { if (current === version) busy.value = false }
}
</script>
<template>
  <section class="panel change-card">
    <strong>{{ change?.action === 'delete' ? t('删除智能体配置', 'Delete Agent definition') : t('修改智能体配置', 'Update Agent definition') }} · {{ change?.before.name }}</strong>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <dl v-if="change?.config"><template v-for="field in fields" :key="field"><dt>{{ field }}</dt><dd>{{ change.before[field as keyof AgentChange['before']] }} → {{ change.config[field as keyof AgentChange['before']] }}</dd></template></dl>
    <p v-else>{{ t('删除后保留既有运行记录。', 'Existing run history will be retained.') }}</p>
    <div v-if="change?.status === 'pending'" class="inline-actions"><button class="button-primary" :disabled="busy" @click="decide('approve')">{{ t('确认变更', 'Approve change') }}</button><button class="button-secondary" :disabled="busy" @click="decide('reject')">{{ t('拒绝', 'Reject') }}</button></div>
    <p v-else-if="change">{{ change.status === 'approved' ? t('变更已确认', 'Change approved') : t('变更已拒绝', 'Change rejected') }}</p>
  </section>
</template>
<style scoped>.change-card { display: grid; gap: var(--space-sm); overflow-wrap: anywhere; } dd { white-space: pre-wrap; margin-bottom: var(--space-sm); }</style>
