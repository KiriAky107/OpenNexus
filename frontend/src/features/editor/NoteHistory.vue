<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import NoteDiff from './NoteDiff.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { listNoteChanges, getNoteChange, restoreNoteChange, type NoteChange, type NoteChangeDetail } from '@/services/noteService'
import { t } from '@/i18n'
const props = defineProps<{ noteId: string }>()
const emit = defineEmits<{ close: [] }>()
const editor = useEditorStore(), workspace = useWorkspaceStore()
const items = ref<NoteChange[]>([])
const selected = ref<NoteChangeDetail>()
const confirming = ref(false), busy = ref(false), more = ref(false)
const error = ref(''), message = ref('')
const editorSafe = computed(() => editor.currentNoteId === props.noteId && ['saved', 'idle'].includes(editor.saveStatus))
let generation = 0, detailRequest = 0
async function load(append = false) {
  const version = generation
  busy.value = true; error.value = ''
  try {
    const result = await listNoteChanges(props.noteId, append ? items.value.length : 0)
    if (version !== generation) return
    items.value = append ? [...items.value, ...result.items] : result.items
    more.value = result.items.length === 30
  } catch (cause) { if (version === generation) error.value = String(cause) }
  finally { if (version === generation) busy.value = false }
}
async function inspect(change: NoteChange) {
  const version = generation, request = ++detailRequest
  selected.value = undefined; confirming.value = false; error.value = ''
  try { const value = await getNoteChange(props.noteId, change.change_id); if (version === generation && request === detailRequest) selected.value = value }
  catch (cause) { if (version === generation && request === detailRequest) error.value = String(cause) }
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
  <AppDialog :label="t('笔记修改历史', 'Note change history')" @close="emit('close')">
    <div class="modal note-history">
      <div class="inline-actions"><h2>{{ t('笔记修改历史', 'Note change history') }}</h2><button class="button-secondary" @click="emit('close')">{{ t('关闭', 'Close') }}</button></div>
      <p class="subtle">{{ t('显示已成功保存的 AI 写入与用户恢复。新建笔记可查看差异；恢复内容不会删除笔记。', 'Shows successful AI writes and user restores. Creation diffs are available; restoring content does not delete notes.') }}</p>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <p v-if="message" role="status">{{ message }}</p>
      <p v-if="busy" role="status">{{ t('正在读取或保存…', 'Loading or saving…') }}</p>
      <p v-if="!items.length && !busy">{{ t('尚无已保存的修改记录。', 'No saved changes yet.') }}</p>
      <ol class="history-list">
        <li v-for="item in items" :key="item.change_id"><button class="button-secondary history-entry" :disabled="busy" :aria-pressed="selected?.change_id === item.change_id" @click="inspect(item)"><strong>{{ source(item.origin) }}</strong><time>{{ new Date(item.applied_at).toLocaleString() }}</time><span>{{ item.file_path }}</span></button></li>
      </ol>
      <button v-if="more" class="button-secondary" :disabled="busy" @click="load(true)">{{ t('加载更早记录', 'Load older changes') }}</button>
      <section v-if="selected" class="history-detail">
        <p>{{ t('来源', 'Source') }}: {{ selected.origin }}</p>
        <NoteDiff :diff="selected.diff" />
        <details v-if="selected.before_metadata || selected.after_metadata" class="ui-disclosure"><summary>{{ t('标题、标签与修订', 'Title, tags and revisions') }}</summary><pre>{{ selected.before_metadata }}
→ {{ selected.after_metadata }}</pre></details>
        <p v-if="!selected.can_restore" role="status">{{ selected.restore_reason === 'created' ? t('这是新建笔记记录，没有可恢复的写入前正文。', 'This creation record has no previous content to restore.') : t('笔记此后已修改、移动或删除，不能覆盖后续内容。', 'The note has since changed, moved or been deleted. Later content cannot be overwritten.') }}</p>
        <p v-else-if="!editorSafe" role="status">{{ t('请先保存或处理编辑器中的修改，再恢复历史。', 'Save or resolve editor changes before restoring history.') }}</p>
        <button v-else-if="!confirming" class="button-secondary" :disabled="busy" @click="confirming = true">{{ t('恢复到此次写入前', 'Restore content before this change') }}</button>
        <div v-else>
          <p>{{ t('确认将当前笔记恢复到此次写入前？恢复将保存为新版本。', 'Restore this note to its content before this change? A new revision will be saved.') }}</p>
          <div class="inline-actions"><button class="button-primary" :disabled="busy" @click="restore">{{ t('确认恢复', 'Confirm restore') }}</button><button class="button-secondary" :disabled="busy" @click="confirming = false">{{ t('取消', 'Cancel') }}</button></div>
        </div>
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
