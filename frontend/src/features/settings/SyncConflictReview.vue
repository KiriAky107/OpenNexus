<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { SyncConflict, SyncConflictReview } from '@/contracts/sync'
import { hostInvoke } from '@/services/platform/desktop'
import { useEditorStore } from '@/stores/editor'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { t } from '@/i18n'
import SyncConflictContent from './SyncConflictContent.vue'
const props = defineProps<{ vaultId: string; bindingId: string; conflict: SyncConflict; disabled: boolean }>()
const emit = defineEmits<{ resolved: [] }>()
const editor = useEditorStore(), { actionDialog, resolveAction, askConfirm } = useActionDialog()
const review = ref<SyncConflictReview>(), busy = ref(false), error = ref(''), destination = ref('')
let generation = 0
const identity = computed(() => JSON.stringify([props.vaultId, props.bindingId, props.conflict.sequence, props.conflict.current_hash ?? props.conflict.local_hash, props.conflict.local_path, props.conflict.remote]))
watch(identity, () => { generation++; review.value = undefined; error.value = ''; destination.value = ''; busy.value = false })
onBeforeUnmount(() => { generation++ })
const unsaved = computed(() => {
  const current = editor.currentFilePath?.replace(/^\//, '').toLowerCase()
  return current && [props.conflict.local_path, props.conflict.remote.path, ...(review.value?.related.map(item => item.path) ?? [])].some(path => path.toLowerCase() === current)
    && ['dirty', 'saving', 'save_failed', 'conflict', 'external_changed'].includes(editor.saveStatus)
})
const blocked = computed(() => busy.value || props.disabled || Boolean(unsaved.value))
async function load() {
  if (blocked.value) return
  const version = ++generation
  busy.value = true; error.value = ''; review.value = undefined
  try {
    const value = await hostInvoke<SyncConflictReview>('sync_conflict_review', { vaultId: props.vaultId, bindingId: props.bindingId, sequence: props.conflict.sequence })
    if (version !== generation) return
    if (value.vault_id !== props.vaultId || value.binding_id !== props.bindingId || value.sequence !== props.conflict.sequence || value.local_path !== props.conflict.local_path)
      throw new Error(t('冲突已变化，请重新读取。', 'The conflict changed. Read it again.'))
    review.value = value
  } catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
async function resolve(choice: 'local' | 'remote' | 'copy') {
  const snapshot = review.value, target = choice === 'copy' ? destination.value : '', version = generation
  if (!snapshot || blocked.value || (choice === 'copy' && !target)) return
  busy.value = true; error.value = ''
  try {
    if (!(await askConfirm(t(`按已审核的正文解决 ${snapshot.local_path} 的冲突？采用远端会替换当前本地内容。`, `Resolve ${snapshot.local_path} using the reviewed content? Using remote replaces the local content.`)))) return
    if (version !== generation || unsaved.value || props.disabled || review.value !== snapshot) return
    await hostInvoke('sync_resolve', { request: { vault_id: snapshot.vault_id, binding_id: snapshot.binding_id, sequence: snapshot.sequence, choice, destination: target, expected: snapshot.local.hash, fingerprint: snapshot.fingerprint } })
    if (version === generation) { review.value = undefined; emit('resolved') }
  } catch (cause) {
    if (version === generation) { review.value = undefined; error.value = cause instanceof Error ? cause.message : String(cause) }
  } finally { if (version === generation) busy.value = false }
}

</script>
<template>
  <div class="sync-conflict-review">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <p v-if="unsaved" role="alert">{{ t('相关文件有未保存或待处理的编辑。请先保存、解决编辑冲突或关闭该文件，再审核同步冲突。', 'A related file has unsaved or unresolved edits. Save it, resolve the editor conflict, or close the file before reviewing this sync conflict.') }}</p>
    <button :disabled="blocked" @click="load">{{ t('读取正文与差异', 'Read content and changes') }}</button>
    <p v-if="busy" role="status">{{ t('正在核对冲突…', 'Checking conflict…') }}</p><p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <template v-if="review">
      <SyncConflictContent :review="review" />
      <p>{{ t('应用时会重新核对正文、远端修订与当前知识库。审核后文件变化，需要重新读取。', 'Applying rechecks the content, remote revision and current vault. Changes after review require reading it again.') }}</p>
      <div class="inline-actions"><button :disabled="blocked" @click="resolve('local')">{{ t('保留本地', 'Keep local') }}</button><button :disabled="blocked" @click="resolve('remote')">{{ t('采用远端', 'Use remote') }}</button></div>
      <p v-if="conflict.local_path.startsWith('opennexus-records/')">{{ t('设置副本保存为 attachments 下的文本文件，供查看和恢复，不会自动应用。', 'Save a settings copy as a text file under attachments for inspection and recovery; it is not applied automatically.') }}</p>
      <p v-if="review.related.some(item => item.content.exists)">{{ t(`另存副本只保留 ${review.local_path} 的当前内容。其他路径如需保留，请先另存再重新审核。`, `Saving a copy preserves only the current content of ${review.local_path}. Save the other paths separately if needed, then review again.`) }}</p>
      <label>{{ t('副本相对路径', 'Relative copy path') }}<input v-model="destination" :disabled="blocked" :placeholder="conflict.local_path.startsWith('opennexus-records/') ? 'attachments/settings-copy.txt' : 'conflicts/note-copy.md'" /></label><button :disabled="blocked || !destination" @click="resolve('copy')">{{ t('另存本地副本并采用远端', 'Save local copy and use remote') }}</button>
    </template>
  </div>
</template>
<style scoped>
.sync-conflict-review { display: grid; gap: var(--space-sm); min-width: 0; }
button, input { min-height: 38px; padding: 8px 12px; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); color: var(--color-text-primary); font: inherit; }
button { cursor: pointer; justify-self: start; }
button:hover:not(:disabled) { background: var(--color-background-hover); }
button:disabled { cursor: default; opacity: .55; }
input { min-width: 0; width: 100%; box-sizing: border-box; }
label { display: grid; gap: var(--space-xs); }
</style>
