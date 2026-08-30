<script setup lang="ts">
import { ref, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{ source: string }>()
const html = ref('')
let renderVersion = 0

watch(() => props.source, async (source) => {
  const version = ++renderVersion
  const result = await renderMarkdown(source)
  if (version === renderVersion) html.value = result
}, { immediate: true })
</script>

<template>
  <div class="markdown-content" v-html="html" />
</template>

<style scoped>
.markdown-content { white-space: normal; user-select: text; }
.markdown-content :deep(p), .markdown-content :deep(ul), .markdown-content :deep(ol), .markdown-content :deep(pre), .markdown-content :deep(blockquote) { margin: .65em 0; }
.markdown-content :deep(h1), .markdown-content :deep(h2), .markdown-content :deep(h3) { margin: 1em 0 .5em; line-height: var(--line-height-tight); }
.markdown-content :deep(ul) { padding-left: 1.5em; list-style: disc; }
.markdown-content :deep(ol) { padding-left: 1.5em; list-style: decimal; }
.markdown-content :deep(li::marker) { color: var(--color-markdown-marker); font-weight: 700; }
.markdown-content :deep(.shiki) { overflow: auto; margin: .85em 0; padding: 16px; border: 1px solid var(--color-code-border); border-radius: 6px; background: var(--color-code-background) !important; color: var(--color-code-text); font-family: var(--font-ui-mono); font-size: .875em; line-height: 1.45; tab-size: 4; }
.markdown-content :deep(code) { padding: .1em .3em; border-radius: var(--radius-sm); background: var(--color-background-tertiary); font-family: var(--font-ui-mono); }
.markdown-content :deep(.shiki code) { display: block; min-width: max-content; padding: 0; background: transparent; font: inherit; }
.markdown-content :deep(.shiki .line) { display: block; min-height: 1.45em; }
.markdown-content :deep(blockquote) { padding-left: 1em; border-left: 3px solid var(--color-accent-primary); color: var(--color-text-secondary); }
.markdown-content :deep(table) { width: 100%; margin: .65em 0; border-collapse: collapse; }
.markdown-content :deep(th), .markdown-content :deep(td) { padding: .45em .65em; border: 1px solid var(--color-markdown-grid); text-align: left; }
.markdown-content :deep(th) { background: var(--color-markdown-table-header); font-weight: 700; }
.markdown-content :deep(img) { max-width: 100%; }
.markdown-content :deep(hr) { margin: 1em 0; border: 0; border-top: 1px solid var(--color-border-default); }
:global([data-code-theme='github-light']) .markdown-content :deep(.shiki),
:global([data-code-theme='github-light']) .markdown-content :deep(.shiki span) {
  color: var(--shiki-light) !important;
  font-style: var(--shiki-light-font-style) !important;
  font-weight: var(--shiki-light-font-weight) !important;
  text-decoration: var(--shiki-light-text-decoration) !important;
}
:global([data-code-theme='github-dark']) .markdown-content :deep(.shiki),
:global([data-code-theme='github-dark']) .markdown-content :deep(.shiki span) {
  color: var(--shiki-dark) !important;
  font-style: var(--shiki-dark-font-style) !important;
  font-weight: var(--shiki-dark-font-weight) !important;
  text-decoration: var(--shiki-dark-text-decoration) !important;
}
</style>
