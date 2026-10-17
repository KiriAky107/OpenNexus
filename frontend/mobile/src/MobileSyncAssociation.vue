<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { t } from '../../src/i18n'
import AppDialog from '../../src/components/common/AppDialog.vue'
import SyncInitialPreview from '../../src/features/settings/SyncInitialPreview.vue'
import MobileSyncCycle from './MobileSyncCycle.vue'
import { syncError } from '../../src/features/settings/syncPresentation'
import type { SyncRemoteVault } from '../../src/features/settings/syncVaults'
import { mobileSyncAssociation } from './native'
import { createAssociationSession } from './syncAssociationSession'
import type { AuthSelection } from './syncAuthTypes'
import type { BindingIntent, BindingRequest } from './syncAssociationTypes'

const props = defineProps<{ selection: AuthSelection; authenticated: boolean; remoteVault?: SyncRemoteVault; syncEnabled?: boolean }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const session = createAssociationSession(mobileSyncAssociation), state = session.state
const cycleBusy = ref(false), cycle = ref<{ handleBack(): boolean }>()
const cycleTarget = computed(() => state.status?.binding && props.syncEnabled && props.authenticated ? {
  expected: props.selection, vault_id: state.status.vault_id, binding_id: state.status.binding.id,
} : null)
const frozen = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const confirmation = ref<{ kind: 'bind' | 'retry'; request: BindingRequest } | { kind: 'resume'; receipt: BindingIntent } | null>(null)
const reviewed = computed(() => confirmation.value?.kind === 'resume' ? confirmation.value.receipt.request : confirmation.value?.request)
const mayResume = computed(() => {
  const original = state.receipt?.request.selection, current = props.selection
  return props.authenticated && state.receipt?.phase === 'prepared' && original?.revision === current.revision
    && original.operation_id === current.operation_id && original.generation === current.generation
    && original.endpoint === current.endpoint && original.account === current.account
})
watch(() => state.busy || cycleBusy.value, value => emit('busy', value), { flush: 'sync' })
watch(() => [props.selection.revision, props.selection.operation_id, props.selection.generation,
  props.selection.endpoint, props.selection.account, props.remoteVault?.id] as const, () => {
  confirmation.value = null; session.context(props.selection, props.remoteVault?.id ?? ''); void session.status()
}, { immediate: true })
function close() { if (!confirmation.value) return false; confirmation.value = null; return true }
function handleBack() { return close() || cycle.value?.handleBack() || false }
function review() { const request = session.freeze(); if (request) confirmation.value = { kind: 'bind', request } }
async function confirm() {
  const action = confirmation.value; if (!action || state.busy) return
  confirmation.value = null
  const ok = action.kind === 'resume' ? await session.resume(action.receipt)
    : action.kind === 'retry' ? await session.retryMissing(action.request) : await session.confirm(action.request)
  if (ok) await session.status()
}
function feedback(code: string) {
  if (code === 'SYNC_PREVIEW_CHANGED') return t('本机文件或远端快照已变化，请重新预览。', 'Local files or the remote snapshot changed. Review a new preview.')
  if (code === 'SYNC_BINDING_INTENT_CHANGED') return t('原尝试版本已变化，请先读取原编号回执。', 'The original attempt revision changed. Read its original receipt first.')
  return syncError(code)
}
function hidden() { if (document.hidden) { confirmation.value = null; session.clear() } }
onMounted(() => document.addEventListener('visibilitychange', hidden))
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', hidden); session.dispose(); emit('busy', false) })
defineExpose({ handleBack })
</script>

<template>
  <section class="association" :aria-label="t('知识库关联', 'Vault association')" :aria-busy="state.busy">
    <h4>{{ t('知识库关联', 'Vault association') }}</h4>
    <p v-if="state.error" role="alert">{{ feedback(state.error) }}</p>
    <button class="button-secondary" :disabled="state.busy" @click="session.status">{{ t('读取本机关联', 'Read local association') }}</button>
    <template v-if="state.status">
      <p v-if="state.status.another_connection">{{ t('本机知识库已关联其他连接，请先选回原连接。', 'This local vault belongs to another connection. Select its original connection first.') }}</p>
      <p v-else-if="state.status.binding">{{ t('已关联远端库', 'Associated remote vault') }} · {{ state.status.binding.remote_vault }}</p>
      <p v-else>{{ t('尚未关联远端库。', 'No remote vault is associated yet.') }}</p>
      <button v-if="state.status.original_operation_id" class="button-secondary" :disabled="state.busy" @click="session.readOriginal(state.status.original_operation_id!)">{{ t('读取此关联原回执', 'Read this association’s original receipt') }}</button>
      <template v-if="!state.status.binding && !state.status.another_connection && authenticated && remoteVault">
        <p>{{ remoteVault.name }}</p>
        <button class="button-secondary" :disabled="state.busy || !!state.originalId" @click="session.preview(0)">{{ t('预览合并', 'Preview merge') }}</button>
        <SyncInitialPreview v-if="state.preview" :items="state.preview.items" :total="state.preview.total" :offset="state.preview.offset" :limit="state.preview.limit" :busy="state.busy || !!state.originalId" @page="session.preview" @confirm="review" />
      </template>
      <button class="button-secondary" :disabled="state.busy" @click="session.pending()">{{ t('读取待核对关联', 'Read unconfirmed associations') }}</button>
      <section v-if="state.pending" class="originals" :aria-label="t('待核对关联', 'Unconfirmed associations')">
        <p v-if="!state.pending.items.length">{{ t('没有待核对的本机关联记录。', 'There are no unconfirmed local association records.') }}</p>
        <article v-for="item in state.pending.items" :key="item.request.operation_id"><code>{{ item.request.operation_id }}</code><p>{{ item.request.remote_vault }}</p><button class="button-secondary" :disabled="state.busy" @click="session.readOriginal(item.request.operation_id)">{{ t('只读查询原编号', 'Read original ID') }}</button></article>
        <button v-if="state.pending.next_after" class="button-secondary" :disabled="state.busy" @click="session.pending(state.pending.next_after)">{{ t('下一页', 'Next') }}</button>
      </section>
      <section v-if="state.originalId" class="original-result" :aria-label="t('关联原回执', 'Original association receipt')">
        <code>{{ state.originalId }}</code>
        <p>{{ t('先读取原结果。不会自动重发、重新关联或启动笔记传输。', 'Read the original result first. No request is resent, association replaced or note transfer started automatically.') }}</p>
        <p v-if="state.receipt">{{ state.receipt.phase === 'bound' ? t('原关联已提交', 'Original association committed') : t('原关联尚待完成', 'Original association is unfinished') }} · {{ state.receipt.attempt_revision }}</p>
        <p v-if="state.originalMissing">{{ t('未找到原编号记录。', 'No record exists for the original ID.') }}</p>
        <div class="actions"><button class="button-secondary" :disabled="state.busy" @click="session.readOriginal()">{{ t('只读查询原编号', 'Read original ID') }}</button>
          <button v-if="mayResume && state.receipt && state.originalChecked" class="button-secondary" :disabled="state.busy" @click="confirmation = { kind: 'resume', receipt: frozen(state.receipt) }">{{ t('审核并继续原关联', 'Review and resume original association') }}</button>
          <button v-if="state.originalMissing && state.original && authenticated" class="button-secondary" :disabled="state.busy" @click="confirmation = { kind: 'retry', request: frozen(state.original) }">{{ t('审核原编号与原内容重试', 'Review original ID and content for retry') }}</button>
          <button class="button-secondary" :disabled="state.busy || !state.originalChecked" @click="session.keepOriginal">{{ t('保留原记录并继续', 'Keep original record and continue') }}</button></div>
      </section>
    </template>
    <MobileSyncCycle v-if="cycleTarget" ref="cycle" :target="cycleTarget" @busy="cycleBusy = $event" />
    <AppDialog v-if="confirmation && reviewed" :label="t('审核知识库关联', 'Review vault association')" @close="close"><section class="modal-card review">
      <h3>{{ t('审核知识库关联', 'Review vault association') }}</h3>
      <p>{{ t('相同路径采用远端文件身份，内容不同的文件保留为冲突。本次确认只建立关联，笔记传输由单独的同步操作启动。', 'Matching paths adopt remote file identities, while differing contents remain conflicts. This confirmation establishes an association; a separate sync action starts note transfer.') }}</p>
      <dl><dt>{{ t('服务', 'Service') }}</dt><dd>{{ reviewed.selection.endpoint }}</dd><dt>{{ t('账户', 'Account') }}</dt><dd>{{ reviewed.selection.account }}</dd><dt>{{ t('远端库', 'Remote vault') }}</dt><dd>{{ reviewed.remote_vault }}</dd><dt>{{ t('原编号', 'Original ID') }}</dt><dd><code>{{ reviewed.operation_id }}</code></dd><dt>{{ t('预览摘要', 'Preview digest') }}</dt><dd><code>{{ reviewed.preview_fingerprint }}</code></dd></dl>
      <div class="actions"><button class="button-secondary" @click="close">{{ t('取消', 'Cancel') }}</button><button class="button-primary" :disabled="state.busy" @click="confirm">{{ t('确认此关联', 'Confirm this association') }}</button></div>
    </section></AppDialog>
  </section>
</template>

<style scoped>
.association,.originals,.original-result,.review { display:grid; gap:var(--space-sm); min-width:0; }
h3,h4,p { margin:0; }
.originals article,.original-result { padding:var(--space-md); border:1px solid var(--color-border-default); border-radius:var(--radius-md); background:var(--color-background-secondary); }
.originals article { display:grid; gap:var(--space-sm); }
p,code,dd { overflow-wrap:anywhere; }
.actions { display:flex; flex-wrap:wrap; gap:8px; }
button { min-height:44px; max-width:100%; white-space:normal; }
.review { padding:var(--space-lg); width:min(560px,100%); }
dl { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:8px; margin:0; } dd { margin:0; }
</style>
