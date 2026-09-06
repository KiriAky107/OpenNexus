<script setup lang="ts">
import { defineAsyncComponent, ref, watch } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import EditorScrollButtons from './EditorScrollButtons.vue'
const VisualMarkdownEditor = defineAsyncComponent(() => import('./VisualMarkdownEditor.vue'))

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const themeStore = useThemeStore()
const sourceEditor = ref<HTMLTextAreaElement | null>(null)
const container = ref<HTMLElement | null>(null)
watch(() => editorStore.headingRequest, request => {
  const input = sourceEditor.value
  if (!request || !input || request.path !== editorStore.currentFilePath) return
  input.focus()
  input.setSelectionRange(request.offset, request.offset)
  const lines = input.value.slice(0, request.offset).split('\n').length - 1
  input.scrollTop = lines * (parseFloat(getComputedStyle(input).lineHeight) || 24)
})
function updateContent(event: Event) {
  editorStore.updateContent((event.target as HTMLTextAreaElement).value)
  editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
}
</script>

<template>
  <div ref="container" class="editor-scroll-pane">
  <VisualMarkdownEditor v-if="editorStore.mode === 'wysiwyg'" :key="`${editorStore.currentFilePath ?? 'empty'}:${editorStore.contentRevision}:${themeStore.resolvedCodeBlockTheme}:${settingsStore.language}`"
    :initial-content="editorStore.content" />
  <textarea v-else ref="sourceEditor" class="editor-pane source" :value="editorStore.content" :spellcheck="settingsStore.spellCheck"
    :lang="settingsStore.language" :aria-label="settingsStore.language === 'en' ? 'Markdown source editor' : 'Markdown 源码编辑器'" @input="updateContent" />
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
