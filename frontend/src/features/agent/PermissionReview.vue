<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { getPermissionPreview, respondToPermission } from '@/services/agentService'
import type { NoteWritePreview } from '@/services/noteService'
import { useWorkspaceStore } from '@/stores/workspace'
import NoteDiff from '@/features/editor/NoteDiff.vue'
import { t } from '@/i18n'
const props = defineProps<{ runId: string; requestId: string; call: Record<string, unknown>; allowSession?: boolean }>()
const emit = defineEmits<{ resolved: [] }>()
const workspace = useWorkspaceStore()
const isNote = computed(() => ['notes.create', 'notes.update', 'notes.patch_markdown'].includes(String(props.call.name)))
const isSource = computed(() => props.call.name === 'experiments.files.write')
const needsPreview = computed(() => isNote.value || isSource.value)
const onceOnly = computed(() => isSource.value || ['experiments.run', 'experiments.import'].includes(String(props.call.name)))
const preview = ref<NoteWritePreview>()
const busy = ref(false)
const error = ref('')
let generation = 0
async function load() {
  const version = ++generation
  preview.value = undefined; error.value = ''; busy.value = true
  try { const value = await getPermissionPreview(props.runId, props.requestId); if (version === generation) preview.value = value }
  catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
watch(() => [props.runId, props.requestId, workspace.vaultId], () => {
  generation++; preview.value = undefined; error.value = ''; busy.value = false
  if (needsPreview.value) void load()
}, { immediate: true })
onBeforeUnmount(() => { generation++ })
async function decide(decision: 'allow_once' | 'allow_session' | 'deny') {
  if (busy.value || (decision !== 'deny' && needsPreview.value && !preview.value)) return
  const version = generation
  busy.value = true; error.value = ''
  try {
    if (needsPreview.value && decision !== 'deny') await respondToPermission(props.runId, props.requestId, decision, preview.value!.token)
    else await respondToPermission(props.runId, props.requestId, decision)
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
    <template v-if="preview">
      <p><strong>{{ isSource ? (preview.overwrite ? t('替换实验文件', 'Replace experiment file') : t('创建实验文件', 'Create experiment file')) : preview.operation === 'create' ? t('创建笔记', 'Create note') : preview.operation === 'append' ? t('追加内容', 'Append content') : t('替换内容', 'Replace content') }}</strong> · {{ preview.file_path }}</p>
      <p v-if="isSource">{{ t('文件大小', 'File size') }}: {{ preview.before_bytes }} → {{ preview.after_bytes }} B · {{ t('仅保存文件；运行与成果导入需要另行确认。', 'Saves the file only; running and importing outputs require separate confirmation.') }}</p>
      <p v-if="preview.metadata.title !== null">{{ t('标题', 'Title') }}: {{ preview.metadata.title }}</p>
      <p v-if="preview.metadata.tags !== null">{{ t('标签', 'Tags') }}: {{ preview.metadata.tags.join(', ') || t('无', 'None') }}</p>
      <NoteDiff :diff="preview.diff" />
    </template>
    <p v-if="busy" role="status">{{ t('正在核对操作…', 'Checking operation…') }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <button v-if="needsPreview" class="button-secondary" :disabled="busy" @click="load">{{ t('重新预览', 'Refresh preview') }}</button>
    <details class="ui-disclosure permission-review"><summary>{{ t('查看原始参数', 'Review raw arguments') }}</summary><pre>{{ JSON.stringify(call, null, 2) }}</pre></details>
    <div class="inline-actions">
      <button class="button-primary" :disabled="busy || (needsPreview && !preview)" @click="decide('allow_once')">{{ t('允许本次', 'Allow once') }}</button>
      <button v-if="allowSession && !isNote && !onceOnly" class="button-secondary" :disabled="busy" @click="decide('allow_session')">{{ t('本次会话允许', 'Allow for session') }}</button>
      <button class="button-danger" :disabled="busy" @click="decide('deny')">{{ t('拒绝', 'Deny') }}</button>
    </div>
  </div>
</template>

<style scoped>
.permission-review-content { display: grid; gap: var(--space-sm); min-width: 0; overflow-wrap: anywhere; }
pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
</style>
