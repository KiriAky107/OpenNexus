<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'

const editorStore = useEditorStore()
</script>

<template>
<textarea
    class="editor-pane"
    :class="editorStore.mode"
    :value="editorStore.content"
    :spellcheck="editorStore.mode === 'wysiwyg'"
    :aria-label="editorStore.mode === 'source' ? 'Markdown 源码编辑器' : 'Markdown 写作编辑器'"
    @input="editorStore.updateContent(($event.target as HTMLTextAreaElement).value); editorStore.scheduleAutoSave()"
  />
</template>

<style scoped>
.editor-pane {
  box-sizing: border-box;
  flex: 1;
  width: 100%;
  resize: none;
  border: 0;
  outline: 0;
  padding: var(--space-xl);
  background: var(--color-background-primary);
  color: var(--color-text-primary);
  font: inherit;
  line-height: 1.7;
  user-select: text;
}
.editor-pane.wysiwyg { max-width: 920px; margin: 0 auto; padding: var(--space-3xl) clamp(var(--space-xl), 8vw, 80px); font-family: var(--font-editor-sans); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); }
.editor-pane.source { font-family: var(--font-editor-mono); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); }
</style>
