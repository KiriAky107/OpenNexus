<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { t } from '../../src/i18n'
import AppDialog from '../../src/components/common/AppDialog.vue'
import SyncActivity from '../../src/features/settings/SyncActivity.vue'
import SyncConflictContent from '../../src/features/settings/SyncConflictContent.vue'
import { syncError } from '../../src/features/settings/syncPresentation'
import { mobileOnSyncActivity, mobileSyncCycle } from './native'
import { createCycleSession } from './syncCycleSession'
import type { Resolution, SyncTarget } from './syncCycleTypes'

const props = defineProps<{ target: SyncTarget }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const session = createCycleSession(mobileSyncCycle), state = session.state
const confirmation = ref<{ kind: 'new' | 'resume'; frozen: Resolution } | null>(null), destination = ref(''), listenerError = ref(false)
const blocked = computed(() => state.busy || state.cycling || state.stopping)
const identity = computed(() => JSON.stringify([props.target.vault_id, props.target.binding_id, props.target.expected.revision,
  props.target.expected.operation_id, props.target.expected.generation, props.target.expected.endpoint, props.target.expected.account]))
function choiceLabel(value: Resolution['choice']) {
  return value === 'local' ? t('保留本地', 'Keep local') : value === 'remote' ? t('采用远端', 'Use remote') : t('另存本地副本并采用远端', 'Save local copy and use remote')
}
function outcome(value: string) {
  switch (value) {
    case 'running': return t('结果待核对', 'Outcome needs checking')
    case 'interrupted': return t('已中断，保留原尝试', 'Interrupted; original attempt retained')
    case 'failed': return t('尚未完成', 'Not completed')
    default: return t('原尝试已确认', 'Original attempt confirmed')
  }
}
function idLabel(value: string) {
  switch (value) {
    case 'operation_id': return t('原操作编号', 'Original operation ID')
    case 'rename_id': return t('原重命名编号', 'Original rename ID')
    case 'copy_id': return t('原副本编号', 'Original copy ID')
    case 'retire_id': return t('原归档编号', 'Original archive ID')
    default: return t('原恢复编号', 'Original restore ID')
  }
}
let disposed = false, unlisten: (() => void) | undefined
watch(identity, () => { confirmation.value = null; destination.value = ''; session.context(props.target); void session.readStatus() }, { immediate: true })
watch(blocked, value => emit('busy', value), { flush: 'sync' })
function close() { if (!confirmation.value) return false; confirmation.value = null; return true }
function freeze(choice: Resolution['choice']) {
  const frozen = session.freeze(choice, destination.value)
  if (frozen) confirmation.value = { kind: 'new', frozen }
}
function resume() {
  if (state.original && state.originalChecked) confirmation.value = { kind: 'resume', frozen: JSON.parse(JSON.stringify(state.original)) as Resolution }
}
async function confirm() {
  const original = confirmation.value
  if (!original || blocked.value) return
  confirmation.value = null
  if (original.kind === 'new') await session.confirm(original.frozen)
  else await session.resume(original.frozen)
}
function hidden() { if (document.hidden) { confirmation.value = null; destination.value = ''; session.clear() } }
onMounted(async () => {
  document.addEventListener('visibilitychange', hidden)
  try {
    const release = await mobileOnSyncActivity(snapshot => session.event(snapshot))
    if (disposed) release(); else unlisten = release
  } catch { if (!disposed) listenerError.value = true }
})
onBeforeUnmount(() => { disposed = true; unlisten?.(); document.removeEventListener('visibilitychange', hidden); session.dispose(); emit('busy', false) })
defineExpose({ handleBack: close })
</script>
<template>
  <section class="mobile-sync-cycle" :aria-label="t('笔记同步', 'Note synchronization')">
    <h4>{{ t('笔记同步', 'Note synchronization') }}</h4>
    <p>{{ t('仅在应用前台按本次确认同步。已有笔记、草稿与原操作记录会保留；待审核冲突单独处理。', 'Sync runs in the foreground after this explicit action. Existing notes, drafts and original operation records are retained; review conflicts separately.') }}</p>
    <p v-if="listenerError" role="status">{{ t('进度通知暂不可用，可读取本轮状态。', 'Progress notifications are unavailable. You can read this cycle’s status.') }}</p>
    <div class="actions">
      <button class="button-secondary" :disabled="blocked" @click="session.readStatus()">{{ t('只读查询队列与原记录', 'Read queue and original records') }}</button>
      <button class="button-primary" :disabled="blocked || !state.cycleChecked || state.status?.paused || !!state.original && !state.originalChecked" @click="session.now">{{ t('立即同步一轮', 'Sync one cycle now') }}</button>
      <button v-if="state.invocationId" class="button-secondary" :disabled="state.stopping" @click="session.readActivity">{{ t('只读查询本轮状态', 'Read this cycle’s status') }}</button>
      <button v-if="state.cycling" class="button-secondary" :disabled="state.stopping" @click="session.stop">{{ t('停止本轮', 'Stop this cycle') }}</button>
    </div>
    <p v-if="state.status?.paused" role="status">{{ t('此绑定已暂停，当前只能读取状态。', 'This binding is paused. Only status reads are available.') }}</p>
    <p v-if="state.cycleUnknown" role="status">{{ t('上轮结果未完整确认。先读取队列与原记录，再明确继续；不会自动重复发送新操作。', 'The previous outcome is not fully confirmed. Read the queue and original records before explicitly continuing; no new operation is sent automatically.') }}</p>
    <SyncActivity :activity="state.activity" :last-success="state.status?.last_cycle_success" :error="state.error" />
    <template v-if="state.status">
      <p>{{ t('待捕获记录', 'Uncaptured records') }} {{ state.status.pending_outbox }} · {{ t('队列任务', 'Queued jobs') }} {{ state.status.queued_jobs }} · {{ t('待审核冲突', 'Conflicts to review') }} {{ state.status.open_conflicts }} · {{ t('待完成原决策', 'Unfinished original decisions') }} {{ state.status.pending_decisions }}</p>
      <section v-if="state.status.attempts.length" class="records" :aria-label="t('原传输尝试', 'Original transfer attempts')">
        <article v-for="item in state.status.attempts" :key="item.operation_id"><p>{{ item.path }}</p><code>{{ item.operation_id }}</code><p>{{ outcome(item.outcome) }} · {{ item.attempts }}</p><p v-if="item.error">{{ syncError(item.error) }}</p></article>
      </section>
      <section class="records" :aria-label="t('待审核冲突', 'Conflicts to review')">
        <article v-for="conflict in state.status.conflicts" :key="conflict.sequence"><p>{{ conflict.local_path }} → {{ conflict.remote.path }}</p>
          <p>{{ t('冲突记录摘要，并非当前文件摘要', 'Recorded conflict hash, not the current file hash') }} · <code>{{ conflict.recorded_local_hash || '∅' }}</code></p>
          <button class="button-secondary" :disabled="blocked || !!state.original" @click="session.readReview(conflict.sequence)">{{ t('读取原决策与正文差异', 'Read original decision and content changes') }}</button>
        </article>
        <button v-if="state.status.next_after !== null" class="button-secondary" :disabled="blocked" @click="session.readStatus(state.status.next_after!)">{{ t('下一页冲突', 'Next conflict page') }}</button>
      </section>
    </template>
    <section v-if="state.review" class="review">
      <h4>{{ state.review.local_path }}</h4><SyncConflictContent :review="state.review" />
      <p>{{ t('应用前会重新核对正文和摘要。草稿不会作为已保存内容上传，也不会因同步被丢弃。', 'Content and hashes are checked again before applying. Drafts are neither uploaded as saved content nor discarded by sync.') }}</p>
      <div class="actions"><button class="button-secondary" :disabled="blocked" @click="freeze('local')">{{ t('保留本地', 'Keep local') }}</button><button class="button-secondary" :disabled="blocked" @click="freeze('remote')">{{ t('采用远端', 'Use remote') }}</button></div>
      <label>{{ t('副本相对路径', 'Relative copy path') }}<input v-model="destination" :disabled="blocked" placeholder="attachments/note-copy.md" /></label><button class="button-secondary" :disabled="blocked || !destination" @click="freeze('copy')">{{ t('另存本地副本并采用远端', 'Save local copy and use remote') }}</button>
    </section>
    <section v-if="state.original || state.decision" class="review" :aria-label="t('原冲突决策', 'Original conflict decision')">
      <h4>{{ t('原冲突决策', 'Original conflict decision') }}</h4>
      <p v-if="state.originalMissing">{{ t('未找到此原决策。可审核原内容后明确重试。', 'This original decision was not found. Review the original content before explicitly retrying.') }}</p>
      <p v-if="state.decision?.state === 'completed'">{{ t('本机原决策已完成，上传状态请另行读取队列。', 'The original local decision completed. Read the queue separately for upload status.') }}</p>
      <p v-if="state.decision?.fingerprint === null" role="alert">{{ t('旧决策没有冻结审核摘要，不能从此页面自动重建或替换。原编号已保留。', 'This legacy decision has no frozen review digest. This page cannot rebuild or replace it automatically. Its original IDs are retained.') }}</p>
      <template v-if="state.decision"><p>{{ choiceLabel(state.decision.choice) }} · {{ state.decision.destination }}</p><dl><template v-for="key in (['operation_id', 'rename_id', 'copy_id', 'retire_id', 'restore_id'] as const)" :key="key"><dt>{{ idLabel(key) }}</dt><dd><code>{{ state.decision[key] }}</code></dd></template></dl></template>
      <div class="actions"><button class="button-secondary" :disabled="blocked" @click="session.readDecision()">{{ t('只读查询原决策', 'Read original decision') }}</button>
        <button v-if="state.original && state.originalChecked && state.decision?.state !== 'completed'" class="button-secondary" :disabled="blocked" @click="resume">{{ t('审核并继续原决策', 'Review and continue original decision') }}</button>
        <button class="button-secondary" :disabled="blocked || !!state.original && !state.originalChecked" @click="session.keepOriginal">{{ t('保留原记录并返回', 'Keep original record and return') }}</button></div>
    </section>
    <AppDialog v-if="confirmation" :label="t('审核同步冲突决策', 'Review sync conflict decision')" @close="close"><section class="modal-card confirmation">
      <h3>{{ t('审核同步冲突决策', 'Review sync conflict decision') }}</h3>
      <p>{{ confirmation.kind === 'resume' ? t('只继续已读取的原决策和原内容，不替换编号或重新批准其他内容。', 'Continue only the read original decision and content, preserving IDs without approving other content.') : t('采用远端会替换已保存的本地内容。请核对本次选择、目标路径和已审核摘要。', 'Using remote replaces the saved local content. Check this choice, destination and reviewed digest.') }}</p>
      <dl><dt>{{ t('服务', 'Service') }}</dt><dd>{{ confirmation.frozen.target.expected.endpoint }}</dd><dt>{{ t('账户', 'Account') }}</dt><dd>{{ confirmation.frozen.target.expected.account }}</dd><dt>{{ t('选择', 'Choice') }}</dt><dd>{{ choiceLabel(confirmation.frozen.choice) }}</dd><dt>{{ t('副本路径', 'Copy path') }}</dt><dd>{{ confirmation.frozen.destination || '—' }}</dd><dt>{{ t('远端序号', 'Remote sequence') }}</dt><dd>{{ confirmation.frozen.sequence }}</dd><dt>{{ t('审核摘要', 'Review digest') }}</dt><dd><code>{{ confirmation.frozen.fingerprint }}</code></dd></dl>
      <div class="actions"><button class="button-secondary" @click="close">{{ t('取消', 'Cancel') }}</button><button class="button-primary" :disabled="blocked" @click="confirm">{{ t('确认此原内容', 'Confirm this original content') }}</button></div>
    </section></AppDialog>
  </section>
</template>
<style scoped>
.mobile-sync-cycle,.records,.review,.confirmation { display:grid; gap:var(--space-sm); min-width:0; }
h3,h4,p { margin:0; }
.records article,.review { padding:var(--space-md); border:1px solid var(--color-border-default); border-radius:var(--radius-md); background:var(--color-background-secondary); }
.records article { display:grid; gap:var(--space-sm); }
.actions { display:flex; flex-wrap:wrap; gap:8px; }
button,input { min-height:44px; max-width:100%; white-space:normal; }
label { display:grid; gap:var(--space-xs); }
input { width:100%; min-width:0; box-sizing:border-box; }
p,code,dd { overflow-wrap:anywhere; }
.confirmation { padding:var(--space-lg); width:min(560px,100%); }
dl { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:8px; margin:0; } dd { margin:0; }
</style>
