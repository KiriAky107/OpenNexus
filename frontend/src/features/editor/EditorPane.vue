<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import EditorScrollButtons from './EditorScrollButtons.vue'
const SourceMarkdownEditor = defineAsyncComponent(() => import('./SourceMarkdownEditor.vue'))
const VisualMarkdownEditor = defineAsyncComponent(() => import('./VisualMarkdownEditor.vue'))

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const themeStore = useThemeStore()
const container = ref<HTMLElement | null>(null)
</script>

<template>
  <div ref="container" class="editor-scroll-pane">
  <VisualMarkdownEditor v-if="editorStore.mode === 'wysiwyg'" :key="`${editorStore.currentFilePath ?? 'empty'}:${editorStore.contentRevision}:${themeStore.resolvedCodeBlockTheme}:${settingsStore.language}`"
    :initial-content="editorStore.content" />
  <SourceMarkdownEditor v-else :key="`${editorStore.currentFilePath ?? 'empty'}:${editorStore.contentRevision}`" :initial-content="editorStore.content" />
  <EditorScrollButtons :container="container" :content="editorStore.content" />
  </div>
</template>

<style scoped>
.editor-scroll-pane { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
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
.editor-pane.source { font-family: var(--font-editor-mono); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); }
</style>
