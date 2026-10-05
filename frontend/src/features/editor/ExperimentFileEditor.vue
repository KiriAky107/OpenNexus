<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { t } from '@/i18n'
import { computed, ref } from 'vue'
import { parseCsvPreview } from './csvPreview'
import { useExperimentPane } from '@/composables/useExperimentPane'

const editor = useEditorStore()
const experimentPane = useExperimentPane()
const tablePreview = ref(false)
const isCurrentDocument = editor.captureDocument()
const lineEnding = editor.content.includes('\r\n') ? '\r\n' : '\n'
const displayContent = computed(() => editor.content.replace(/\r\n?/g, '\n'))
let composing = false
const isCsv = computed(() => editor.currentFilePath?.toLowerCase().endsWith('.csv') ?? false)
const csvPreview = computed(() => isCsv.value ? parseCsvPreview(editor.content) : null)
function update(event: Event) {
  if (!isCurrentDocument()) return
  const value = (event.target as HTMLTextAreaElement).value.replace(/\r\n?/g, '\n').replace(/\n/g, lineEnding)
  if (value !== editor.content) editor.updateContent(value)
  if (composing || (event as InputEvent).isComposing) editor.cancelPendingAutoSave()
  else if (['dirty', 'save_failed'].includes(editor.saveStatus)) editor.scheduleAutoSave()
}
function startComposition() {
  composing = true
  if (isCurrentDocument()) editor.cancelPendingAutoSave()
}
function endComposition(event: CompositionEvent) {
  composing = false
  update(event)
}
</script>

<template>
  <section class="experiment-editor" :aria-label="editor.currentFilePath ?? 'Experiment file'">
    <header class="experiment-editor__header">
      <span>{{ editor.currentFilePath?.split('/').at(-1) }}</span>
      <div class="experiment-editor__actions">
        <button type="button" :aria-expanded="experimentPane.visible.value" aria-controls="experiment-panel" @click="experimentPane.open">{{ t('运行与成果', 'Runs and outputs') }}</button>
        <button v-if="isCsv" type="button" :aria-pressed="tablePreview" @click="tablePreview = !tablePreview">{{ tablePreview ? t('编辑文本', 'Edit text') : t('表格预览', 'Table preview') }}</button>
        <span>{{ editor.saveStatus === 'saving' ? t('保存中…', 'Saving…') : editor.saveStatus === 'saved' ? t('已保存', 'Saved') : editor.saveStatus === 'dirty' ? t('未保存', 'Unsaved') : '' }}</span>
      </div>
    </header>
    <div v-if="tablePreview && csvPreview" class="experiment-editor__table-wrap" role="region" :aria-label="t('CSV 表格预览', 'CSV table preview')" tabindex="0">
      <p v-if="csvPreview.invalid" class="experiment-editor__notice" role="alert">{{ t('CSV 引号格式不正确，无法显示表格预览。', 'CSV quoting is malformed; the table preview is unavailable.') }}</p>
      <template v-else>
        <p v-if="csvPreview.truncated" class="experiment-editor__notice" role="status">{{ t('预览已限制为前 200 行、40 列、256K 字符和每格 4096 个字符；原文件内容未截断。', 'Preview is limited to 200 rows, 40 columns, 256K characters and 4096 characters per cell. The source file is unchanged.') }}</p>
        <table v-if="csvPreview.rows.length" class="experiment-editor__table"><tbody>
          <tr v-for="(row, rowIndex) in csvPreview.rows" :key="rowIndex">
            <td v-for="(cell, columnIndex) in row" :key="columnIndex">{{ cell }}</td>
          </tr>
        </tbody></table>
        <p v-else>{{ t('文件没有可预览的行。', 'No rows to preview.') }}</p>
      </template>
    </div>
    <textarea
      v-else
      class="experiment-editor__source"
      :value="displayContent"
      :disabled="['conflict', 'external_changed'].includes(editor.saveStatus)"
      :spellcheck="false"
      :aria-label="t('实验文件源码', 'Experiment source')"
      @input="update"
      @compositionstart="startComposition"
      @compositionend="endComposition"
    />
  </section>
</template>

<style scoped>
.experiment-editor { display:flex; flex-direction:column; flex:1; min-height:0; background:var(--color-background-primary); color:var(--color-text-primary); }
.experiment-editor__header { display:flex; justify-content:space-between; gap:var(--space-md); padding:var(--space-sm) var(--space-xl); border-bottom:1px solid var(--color-border-subtle); color:var(--color-text-secondary); font-size:var(--font-size-sm); }
.experiment-editor__actions { display:flex; align-items:center; gap:var(--space-md); }
.experiment-editor__actions button { padding:4px 8px; border:1px solid var(--color-border-default); border-radius:var(--radius-sm); background:var(--color-background-secondary); color:var(--color-text-primary); }
.experiment-editor__table-wrap { flex:1; min-height:0; overflow:auto; padding:var(--space-md); }
.experiment-editor__notice { position:sticky; top:0; z-index:1; padding:var(--space-sm); background:var(--color-background-secondary); color:var(--color-text-secondary); }
.experiment-editor__table { border-collapse:collapse; font-size:var(--font-size-sm); }
.experiment-editor__table td { max-width:420px; padding:6px 10px; border:1px solid var(--color-border-default); white-space:pre-wrap; overflow-wrap:anywhere; }
.experiment-editor__source { box-sizing:border-box; flex:1; width:100%; min-height:0; resize:none; border:0; outline:0; padding:var(--space-xl); background:transparent; color:inherit; font-family:var(--font-editor-mono); font-size:var(--font-editor-size); line-height:var(--font-editor-line-height); tab-size:4; white-space:pre; overflow:auto; }
.experiment-editor__source:disabled { opacity:.72; }
</style>
