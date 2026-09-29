<script setup lang="ts">
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { t } from '@/i18n'
import { validateCanvasContent } from '@/services/workspaceDocuments'

const editor = useEditorStore()
const validationError = computed(() => {
  try { validateCanvasContent(editor.content); return '' }
  catch { return t('JSON Canvas 无效；修改完成前不会自动保存。', 'Invalid JSON Canvas; automatic save is paused.') }
})
function changed(event: Event) {
  editor.updateContent((event.target as HTMLTextAreaElement).value)
  if (validationError.value) editor.cancelPendingAutoSave()
  else editor.scheduleAutoSave()
}
</script>

<template>
  <div class="canvas-source">
    <p>{{ t('画布数据（JSON Canvas）', 'Canvas data (JSON Canvas)') }}</p>
    <p v-if="validationError" role="alert" class="validation-error">{{ validationError }}</p>
    <textarea :value="editor.content" spellcheck="false" :aria-label="t('画布 JSON 源码', 'Canvas JSON source')" @input="changed" />
  </div>
</template>

<style scoped>
.canvas-source { display: flex; flex: 1; flex-direction: column; min-height: 0; padding: var(--space-lg); background: var(--color-background-primary); }
.canvas-source p { margin: 0 0 var(--space-sm); color: var(--color-text-secondary); }
.canvas-source .validation-error { color: var(--color-error); }
.canvas-source textarea { box-sizing: border-box; flex: 1; width: 100%; min-height: 0; padding: var(--space-md); resize: none; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); outline-color: var(--color-border-focus); background: var(--color-surface-primary); color: var(--color-text-primary); font: 13px/1.6 var(--font-editor-mono); }
</style>
