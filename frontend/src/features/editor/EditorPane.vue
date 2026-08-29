<script setup lang="ts">
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { renderMarkdown } from '@/utils/markdown'

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const renderedContent = computed(() => renderMarkdown(editorStore.content))

function updateContent(event: Event) {
  editorStore.updateContent((event.target as HTMLTextAreaElement).value)
  editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
}
</script>

<template>
  <div v-if="editorStore.mode === 'wysiwyg'" class="writing-layout">
    <textarea class="editor-pane writing-input" :value="editorStore.content" :spellcheck="settingsStore.spellCheck"
      aria-label="Markdown 写作编辑器" @input="updateContent" />
    <article class="markdown-preview" aria-label="Markdown 实时预览" v-html="renderedContent" />
  </div>
  <textarea v-else class="editor-pane source" :value="editorStore.content" :spellcheck="false"
    aria-label="Markdown 源码编辑器" @input="updateContent" />
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
.writing-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); flex: 1; min-height: 0; }
.writing-input { border-right: 1px solid var(--color-border-default); font-family: var(--font-editor-sans); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); }
.markdown-preview { width: min(100%, var(--editor-line-width, 80ch)); overflow: auto; margin: 0 auto; padding: var(--space-3xl) var(--space-xl); font-family: var(--font-editor-sans); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); user-select: text; }
.markdown-preview :deep(h1), .markdown-preview :deep(h2), .markdown-preview :deep(h3) { margin: 1.4em 0 .6em; line-height: var(--line-height-tight); color: var(--color-text-primary); }
.markdown-preview :deep(h1:first-child), .markdown-preview :deep(h2:first-child) { margin-top: 0; }
.markdown-preview :deep(p), .markdown-preview :deep(ul), .markdown-preview :deep(ol), .markdown-preview :deep(blockquote), .markdown-preview :deep(pre), .markdown-preview :deep(table) { margin: .8em 0; }
.markdown-preview :deep(ul), .markdown-preview :deep(ol) { padding-left: 1.6em; }
.markdown-preview :deep(ul) { list-style: disc; }.markdown-preview :deep(ol) { list-style: decimal; }
.markdown-preview :deep(blockquote) { padding-left: 1em; border-left: 3px solid var(--color-accent-primary); color: var(--color-text-secondary); }
.markdown-preview :deep(code) { padding: .15em .35em; border-radius: var(--radius-sm); background: var(--color-background-tertiary); font-family: var(--font-editor-mono); }
.markdown-preview :deep(pre) { overflow: auto; padding: var(--space-lg); border-radius: var(--radius-md); background: var(--color-background-secondary); }
.markdown-preview :deep(pre code) { padding: 0; background: transparent; }
.markdown-preview :deep(table) { width: 100%; border-collapse: collapse; }.markdown-preview :deep(th), .markdown-preview :deep(td) { padding: .5em .7em; border: 1px solid var(--color-border-default); text-align: left; }
.markdown-preview :deep(img) { max-width: 100%; }.markdown-preview :deep(a) { color: var(--color-text-link); }
.markdown-preview :deep(hr) { margin: 1.5em 0; border: 0; border-top: 1px solid var(--color-border-default); }
.editor-pane.source { font-family: var(--font-editor-mono); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); }
@media (max-width: 900px) { .writing-layout { grid-template-columns: 1fr; grid-template-rows: minmax(220px, 1fr) minmax(220px, 1fr); overflow: auto; }.writing-input { min-height: 220px; border-right: 0; border-bottom: 1px solid var(--color-border-default); }.markdown-preview { min-height: 220px; } }
</style>
