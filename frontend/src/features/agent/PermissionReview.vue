<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { getPermissionPreview, respondToPermission, type PermissionPreview } from '@/services/agentService'
import type { NoteWritePreview } from '@/services/noteService'
import { request, type AgentExperimentReview, type NativeAgentReview } from '@/services/experimentService'
import { useWorkspaceStore } from '@/stores/workspace'
import NoteDiff from '@/features/editor/NoteDiff.vue'
import ExperimentReview from './ExperimentReview.vue'
import { t } from '@/i18n'
const props = defineProps<{ runId: string; requestId: string; call: Record<string, unknown>; allowSession?: boolean }>()
const emit = defineEmits<{ resolved: [] }>()
const workspace = useWorkspaceStore()
const isNote = computed(() => ['notes.create', 'notes.update', 'notes.patch_markdown'].includes(String(props.call.name)))
const isSource = computed(() => props.call.name === 'experiments.files.write')
const isAction = computed(() => ['experiments.run', 'experiments.import'].includes(String(props.call.name)))
const needsPreview = computed(() => isNote.value || isSource.value || isAction.value)
const onceOnly = computed(() => isSource.value || isAction.value)
const preview = ref<PermissionPreview>()
const actionPreview = computed(() => preview.value?.kind === 'experiment_run' || preview.value?.kind === 'experiment_import' ? preview.value as AgentExperimentReview : undefined)
const writePreview = computed(() => preview.value && !actionPreview.value ? preview.value as NoteWritePreview : undefined)
const actionReady = ref(false)
const busy = ref(false)
const error = ref('')
let generation = 0
async function load() {
  const version = ++generation
  const run = props.runId, ticket = props.requestId, vault = workspace.vaultId
  preview.value = undefined; actionReady.value = false; error.value = ''; busy.value = true
  try {
    const value = await getPermissionPreview(run, ticket)
    if (version !== generation) return
    if (isAction.value) {
      if ((value.kind !== 'experiment_run' && value.kind !== 'experiment_import')
        || value.kind !== (props.call.name === 'experiments.run' ? 'experiment_run' : 'experiment_import')
        || value.vault_id !== vault || value.context.agent_run_id !== run || value.context.request_id !== ticket)
        throw new Error(t('实验请求已变化，请重新审核。', 'The experiment request changed. Review it again.'))
    } else if (value.kind === 'experiment_run' || value.kind === 'experiment_import') {
      throw new Error(t('操作预览不匹配。', 'The operation preview does not match.'))
    }
    preview.value = value
  }
  catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
watch(() => [props.runId, props.requestId, workspace.vaultId, JSON.stringify(props.call)], () => {
  generation++; preview.value = undefined; actionReady.value = false; error.value = ''; busy.value = false
  if (needsPreview.value) void load()
}, { immediate: true })
onBeforeUnmount(() => { generation++ })
async function decide(decision: 'allow_once' | 'allow_session' | 'deny') {
  if (busy.value || (decision !== 'deny' && needsPreview.value && !preview.value)
    || (decision !== 'deny' && isAction.value && !actionReady.value)
    || (decision === 'allow_session' && onceOnly.value)) return
  const version = generation
  const run = props.runId, ticket = props.requestId, vault = workspace.vaultId, original = preview.value, call = JSON.stringify(props.call)
  busy.value = true; error.value = ''
  try {
    const review = actionPreview.value
    if (review && decision === 'allow_once') {
      const native = await request<NativeAgentReview>(review.vault_id, {
        kind: review.kind === 'experiment_run' ? 'confirm_agent_run' : 'confirm_agent_import',
        operation_id: review.operation_id, fingerprint: review.fingerprint,
      })
      if (version !== generation || workspace.vaultId !== vault || preview.value !== original
        || props.runId !== run || props.requestId !== ticket || JSON.stringify(props.call) !== call) return
      if (native.kind !== review.kind || native.vault_id !== review.vault_id
        || native.operation_id !== review.operation_id || native.fingerprint !== review.fingerprint
        || native.context.agent_run_id !== review.context.agent_run_id
        || native.context.request_id !== review.context.request_id
        || native.context.tool_call_id !== review.context.tool_call_id)
        throw new Error(t('原生确认与当前请求不匹配，请重新审核。', 'Native confirmation does not match this request. Review it again.'))
      if (native.record.state === 'rejected' || native.record.state === 'cancelled') decision = 'deny'
      else if (native.record.state !== 'approved')
        throw new Error(t('实验请求已结束或发生变化，请重新核对记录。', 'The experiment request finished or changed. Check its record again.'))
    }
    if (needsPreview.value && decision !== 'deny') await respondToPermission(run, ticket, decision, original!.token)
    else await respondToPermission(run, ticket, decision)
    if (version === generation) emit('resolved')
  } catch (cause) {
    if (version === generation) {
      error.value = cause instanceof Error ? cause.message : String(cause)
      preview.value = undefined
    }
  } finally { if (version === generation) busy.value = false }
}
</script>

<template>
  <div class="permission-review-content">
    <template v-if="writePreview">
      <p><strong>{{ isSource ? (writePreview.overwrite ? t('替换实验文件', 'Replace experiment file') : t('创建实验文件', 'Create experiment file')) : writePreview.operation === 'create' ? t('创建笔记', 'Create note') : writePreview.operation === 'append' ? t('追加内容', 'Append content') : t('替换内容', 'Replace content') }}</strong> · {{ writePreview.file_path }}</p>
      <p v-if="isSource">{{ t('文件大小', 'File size') }}: {{ writePreview.before_bytes }} → {{ writePreview.after_bytes }} B · {{ t('仅保存文件；运行与成果导入需要另行确认。', 'Saves the file only; running and importing outputs require separate confirmation.') }}</p>
      <p v-if="writePreview.metadata.title !== null">{{ t('标题', 'Title') }}: {{ writePreview.metadata.title }}</p>
      <p v-if="writePreview.metadata.tags !== null">{{ t('标签', 'Tags') }}: {{ writePreview.metadata.tags.join(', ') || t('无', 'None') }}</p>
      <NoteDiff :diff="writePreview.diff" />
    </template>
    <ExperimentReview v-if="actionPreview" :review="actionPreview" @ready="actionReady = $event" />
    <p v-if="busy" role="status">{{ t('正在核对操作…', 'Checking operation…') }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <button v-if="needsPreview" class="button-secondary" :disabled="busy" @click="load">{{ t('重新预览', 'Refresh preview') }}</button>
    <details class="ui-disclosure permission-review"><summary>{{ t('查看原始参数', 'Review raw arguments') }}</summary><pre>{{ JSON.stringify(call, null, 2) }}</pre></details>
    <div class="inline-actions">
      <button class="button-primary" :disabled="busy || (needsPreview && !preview) || (isAction && !actionReady)" @click="decide('allow_once')">{{ t('允许本次', 'Allow once') }}</button>
      <button v-if="allowSession && !isNote && !onceOnly" class="button-secondary" :disabled="busy" @click="decide('allow_session')">{{ t('本次会话允许', 'Allow for session') }}</button>
      <button class="button-danger" :disabled="busy" @click="decide('deny')">{{ t('拒绝', 'Deny') }}</button>
    </div>
  </div>
</template>

<style scoped>
.permission-review-content { display: grid; gap: var(--space-sm); min-width: 0; overflow-wrap: anywhere; }
pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
</style>
