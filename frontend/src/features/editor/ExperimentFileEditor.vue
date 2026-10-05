<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { t } from '@/i18n'

const editor = useEditorStore()
function update(event: Event) {
  editor.updateContent((event.target as HTMLTextAreaElement).value)
  editor.scheduleAutoSave()
}
</script>

<template>
  <section class="experiment-editor" :aria-label="editor.currentFilePath ?? 'Experiment file'">
    <header class="experiment-editor__header">
      <span>{{ editor.currentFilePath?.split('/').at(-1) }}</span>
      <span>{{ editor.saveStatus === 'saving' ? t('保存中…', 'Saving…') : editor.saveStatus === 'saved' ? t('已保存', 'Saved') : editor.saveStatus === 'dirty' ? t('未保存', 'Unsaved') : '' }}</span>
    </header>
    <textarea
      class="experiment-editor__source"
      :value="editor.content"
      :disabled="['conflict', 'external_changed'].includes(editor.saveStatus)"
      :spellcheck="false"
      :aria-label="t('实验文件源码', 'Experiment source')"
      @input="update"
    />
  </section>
</template>

<style scoped>
.experiment-editor { display:flex; flex-direction:column; flex:1; min-height:0; background:var(--color-background-primary); color:var(--color-text-primary); }
.experiment-editor__header { display:flex; justify-content:space-between; gap:var(--space-md); padding:var(--space-sm) var(--space-xl); border-bottom:1px solid var(--color-border-subtle); color:var(--color-text-secondary); font-size:var(--font-size-sm); }
.experiment-editor__source { box-sizing:border-box; flex:1; width:100%; min-height:0; resize:none; border:0; outline:0; padding:var(--space-xl); background:transparent; color:inherit; font-family:var(--font-editor-mono); font-size:var(--font-editor-size); line-height:var(--font-editor-line-height); tab-size:4; white-space:pre; overflow:auto; }
.experiment-editor__source:disabled { opacity:.72; }
</style>
