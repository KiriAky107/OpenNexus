<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'

const editorStore = useEditorStore()
</script>

<template>
  <textarea
    class="editor-pane"
    :value="editorStore.content"
    spellcheck="false"
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
}
</style>
