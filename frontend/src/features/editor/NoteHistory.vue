<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import NoteDiff from './NoteDiff.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { listNoteChanges, getNoteChange, restoreNoteChange, listPendingNoteChanges, inspectPendingNoteChange, reconcileNoteChange, type NoteChange, type NoteChangeDetail } from '@/services/noteService'
import { t } from '@/i18n'
const props = defineProps<{ noteId?: string }>()
const emit = defineEmits<{ close: [] }>()
const editor = useEditorStore(), workspace = useWorkspaceStore()
const items = ref<NoteChange[]>([])
const selected = ref<NoteChangeDetail>()
const confirming = ref(false), busy = ref(false), more = ref(false)
const error = ref(''), message = ref('')
const editorSafe = computed(() => !!selected.value?.note_id && editor.currentNoteId === selected.value.note_id && ['saved', 'idle'].includes(editor.saveStatus))
const title = computed(() => props.noteId ? t('笔记修改历史', 'Note change history') : t('待核对写入', 'Writes awaiting reconciliation'))
let generation = 0, detailRequest = 0
async function load(append = false) {
  const version = generation
  busy.value = true; error.value = ''
  try {
    const offset = append ? items.value.length : 0
    const result = await (props.noteId ? listNoteChanges(props.noteId, offset) : listPendingNoteChanges(offset))
    if (version !== generation) return
    items.value = append ? [...items.value, ...result.items] : result.items
    more.value = result.items.length === 30
  } catch (cause) { if (version === generation) error.value = String(cause) }
  finally { if (version === generation) busy.value = false }
}
async function inspect(change: NoteChange) {
  const version = generation, request = ++detailRequest
  selected.value = undefined; confirming.value = false; error.value = ''
  try { const value = await (props.noteId ? getNoteChange(props.noteId, change.change_id) : inspectPendingNoteChange(change.change_id)); if (version === generation && request === detailRequest) selected.value = value }
  catch (cause) { if (version === generation && request === detailRequest) error.value = String(cause) }
}
function status(change: NoteChange) {
  return change.status === 'pending' ? t('结果待核对', 'Result awaiting reconciliation') : change.status === 'failed' ? t('已确认未提交', 'Confirmed not committed') : t('已确认保存', 'Confirmed saved')
}
async function reconcile() {
  if (selected.value?.status !== 'pending' || busy.value) return
  const version = generation, request = ++detailRequest, changeId = selected.value.change_id
  busy.value = true; error.value = ''; message.value = ''
  try {
    const value = await reconcileNoteChange(changeId)
    if (version !== generation || request !== detailRequest) return
    selected.value = value
    message.value = value.status === 'applied'
      ? t('持久回执已确认该次提交，历史已补齐；未重新写入笔记。', 'A durable receipt confirms this commit. History is complete; the note was not written again.')
      : value.status === 'failed' ? t('已确认未提交。', 'Confirmed not committed.')
      : value.reconciliation_reason === 'legacy_evidence_missing'
        ? t('旧记录缺少计划内容或持久证据，仍无法确定结果。请核对原始运行和笔记，系统不会重放此写入。', 'This legacy record lacks planned content or durable evidence. Review the original run and note; the write will not be replayed.')
        : t('目前没有匹配的已提交回执，仍无法确定结果。回执缺失不能证明未提交，可在 Host 可用后再次核对。', 'No matching committed receipt is available. A missing receipt does not prove no commit; check again when the Host is available.')
    await load()
  } catch (cause) { if (version === generation && request === detailRequest) error.value = String(cause) }
  finally { if (version === generation) busy.value = false }
}
async function restore() {
  if (!selected.value?.can_restore || !confirming.value || !editorSafe.value || busy.value) return
  const version = generation, change = selected.value
  busy.value = true; error.value = ''; message.value = ''
  try {
    await restoreNoteChange(change)
    if (version !== generation) return
    // Preserve edits typed during the request: the editor's external-change
    // check reloads clean buffers and marks dirty buffers as conflicted.
    await editor.checkExternalFile()
    if (version !== generation) return
    selected.value = undefined; confirming.value = false
    message.value = t('已恢复，并保存了一条新版本；可打开该记录撤销本次恢复。', 'Restored and saved as a new revision. Open that record to undo this restore.')
    await load()
  } catch (cause) { if (version === generation) { error.value = String(cause); confirming.value = false; selected.value = undefined } }
  finally { if (version === generation) busy.value = false }
}
function source(origin: string) { return origin.startsWith('restore:') ? t('用户恢复', 'User restore') : origin.startsWith('agent:') ? t('AI 写入', 'AI write') : origin }
watch(() => [props.noteId, workspace.vaultId], () => {
  generation++; detailRequest++; items.value = []; selected.value = undefined; confirming.value = false; message.value = ''
  void load()
}, { immediate: true })
onBeforeUnmount(() => { generation++; detailRequest++ })
</script>

<template>
  <AppDialog :label="title" @close="emit('close')">
    <div class="modal note-history">
      <div class="inline-actions"><h2>{{ title }}</h2><button class="button-secondary" @click="emit('close')">{{ t('关闭', 'Close') }}</button></div>
      <p class="subtle">{{ t('核对只读取已有操作的持久回执，不重新执行写入。恢复已确认的历史仍须确认和修订检查。', 'Reconciliation reads durable receipts for existing operations without repeating writes. Restoring confirmed history requires confirmation and revision checks.') }}</p>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <p v-if="message" role="status">{{ message }}</p>
      <p v-if="busy" role="status">{{ t('正在读取或保存…', 'Loading or saving…') }}</p>
      <p v-if="!items.length && !busy">{{ props.noteId ? t('尚无修改记录。', 'No changes yet.') : t('当前知识库没有待核对写入。', 'No writes await reconciliation in this vault.') }}</p>
      <ol class="history-list">
        <li v-for="item in items" :key="item.change_id"><button class="button-secondary history-entry" :disabled="busy" :aria-pressed="selected?.change_id === item.change_id" @click="inspect(item)"><strong>{{ source(item.origin) }}</strong><span>{{ status(item) }}</span><time>{{ new Date(item.applied_at ?? item.created_at).toLocaleString() }}</time><span>{{ item.file_path ?? item.requested_target }}</span></button></li>
      </ol>
      <button v-if="more" class="button-secondary" :disabled="busy" @click="load(true)">{{ t('加载更早记录', 'Load older changes') }}</button>
      <section v-if="selected" class="history-detail">
        <p>{{ t('来源', 'Source') }}: {{ selected.origin }}</p>
        <p>{{ status(selected) }}</p>
        <template v-if="selected.status === 'pending'">
          <p>{{ t('下列差异是计划内容，尚不能作为已保存结果。', 'The following diff is the planned content, not a confirmed saved result.') }}</p>
          <button class="button-primary" :disabled="busy" @click="reconcile">{{ t('核对持久回执', 'Check durable receipt') }}</button>
        </template>
        <p v-else-if="selected.status === 'failed'">{{ t('此次操作没有提交；若仍需修改，请重新预览并确认新操作。', 'This operation did not commit. Review and confirm a new operation if the change is still needed.') }} {{ selected.failure_code }}</p>
        <NoteDiff v-if="selected.diff" :diff="selected.diff" />
        <details v-if="selected.before_metadata || selected.after_metadata" class="ui-disclosure"><summary>{{ t('标题、标签与修订', 'Title, tags and revisions') }}</summary><pre>{{ selected.before_metadata }}
→ {{ selected.after_metadata }}</pre></details>
        <p v-if="!selected.can_restore && selected.restore_reason !== 'unconfirmed'" role="status">{{ selected.restore_reason === 'created' ? t('这是新建笔记记录，没有可恢复的写入前正文。', 'This creation record has no previous content to restore.') : t('笔记此后已修改、移动或删除，不能覆盖后续内容。', 'The note has since changed, moved or been deleted. Later content cannot be overwritten.') }}</p>
        <template v-if="selected.can_restore">
        <p v-if="!editorSafe" role="status">{{ t('请先保存或处理编辑器中的修改，再恢复历史。', 'Save or resolve editor changes before restoring history.') }}</p>
        <button v-else-if="!confirming" class="button-secondary" :disabled="busy" @click="confirming = true">{{ t('恢复到此次写入前', 'Restore content before this change') }}</button>
        <div v-else>
          <p>{{ t('确认将当前笔记恢复到此次写入前？恢复将保存为新版本。', 'Restore this note to its content before this change? A new revision will be saved.') }}</p>
          <div class="inline-actions"><button class="button-primary" :disabled="busy" @click="restore">{{ t('确认恢复', 'Confirm restore') }}</button><button class="button-secondary" :disabled="busy" @click="confirming = false">{{ t('取消', 'Cancel') }}</button></div>
        </div>
        </template>
      </section>
    </div>
  </AppDialog>
</template>

<style scoped>
.note-history { width: min(800px, 100%); display: grid; gap: var(--space-sm); min-width: 0; overflow-wrap: anywhere; }
.history-list { list-style: none; padding: 0; margin: 0; max-height: 240px; overflow: auto; display: grid; gap: var(--space-xs); }
.history-entry { width: 100%; display: flex; flex-wrap: wrap; gap: var(--space-sm); text-align: start; }
.history-entry[aria-pressed=true] { outline: 2px solid var(--color-border-focus); outline-offset: -2px; }
.history-detail { min-width: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 160px; overflow: auto; user-select: text; }
</style>
