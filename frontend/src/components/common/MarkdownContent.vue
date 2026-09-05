<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { useThemeStore } from '@/stores/theme'

const props = defineProps<{ source: string }>()
const themeStore = useThemeStore()
const html = ref('')
let renderVersion = 0

const diagramTheme = computed<'light' | 'dark'>(() => (themeStore.isDark ? 'dark' : 'light'))

// 主题切换需要重渲染：Mermaid SVG 的配色在渲染时烘焙，无法靠 CSS 变量事后调整。
watch([() => props.source, diagramTheme], async ([source, theme]) => {
  const version = ++renderVersion
  const result = await renderMarkdown(source, { theme })
  if (version === renderVersion) html.value = result
}, { immediate: true })
</script>

<template>
  <div class="markdown-content" v-html="html" />
</template>

<style>
.markdown-content { white-space: normal; user-select: text; }
.markdown-content p, .markdown-content ul, .markdown-content ol, .markdown-content pre, .markdown-content blockquote { margin: .65em 0; }
.markdown-content h1, .markdown-content h2, .markdown-content h3 { margin: 1em 0 .5em; line-height: var(--line-height-tight); }
.markdown-content ul { padding-left: 1.5em; list-style: disc; }
.markdown-content ol { padding-left: 1.5em; list-style: decimal; }
.markdown-content li::marker { color: var(--color-markdown-marker); font-weight: 700; }
.markdown-content .shiki { overflow: auto; margin: .85em 0; padding: 16px; border: 1px solid var(--color-code-border); border-radius: 6px; background: var(--color-code-background) !important; color: var(--color-code-text); font-family: var(--font-ui-mono); font-size: .875em; line-height: 1.45; tab-size: 4; }
.markdown-content code { padding: .1em .3em; border-radius: var(--radius-sm); background: var(--color-background-tertiary); font-family: var(--font-ui-mono); }
.markdown-content .shiki code { display: block; min-width: max-content; padding: 0; background: transparent; font: inherit; }
.markdown-content .shiki .line { display: block; min-height: 1.45em; }
.markdown-content blockquote { padding-left: 1em; border-left: 3px solid var(--color-accent-primary); color: var(--color-text-secondary); }
.markdown-content table { width: 100%; margin: .65em 0; border-collapse: collapse; }
.markdown-content th, .markdown-content td { padding: .45em .65em; border: 1px solid var(--color-markdown-grid); text-align: left; }
.markdown-content th { background: var(--color-markdown-table-header); font-weight: 700; }
.markdown-content img { max-width: 100%; }
.markdown-content hr { margin: 1em 0; border: 0; border-top: 1px solid var(--color-border-default); }
[data-code-theme='github-light'] .markdown-content .shiki,
[data-code-theme='github-light'] .markdown-content .shiki span {
  color: var(--shiki-light) !important;
  font-style: var(--shiki-light-font-style) !important;
  font-weight: var(--shiki-light-font-weight) !important;
  text-decoration: var(--shiki-light-text-decoration) !important;
}
[data-code-theme='github-dark'] .markdown-content .shiki,
[data-code-theme='github-dark'] .markdown-content .shiki span {
  color: var(--shiki-dark) !important;
  font-style: var(--shiki-dark-font-style) !important;
  font-weight: var(--shiki-dark-font-weight) !important;
  text-decoration: var(--shiki-dark-text-decoration) !important;
}
.markdown-content .markdown-mermaid {
  overflow: auto;
  margin: .85em 0;
  padding: 16px;
  border: 1px solid var(--color-border-default);
  border-radius: 6px;
  background: var(--color-surface-primary);
  text-align: center;
}
.markdown-content .markdown-mermaid svg {
  max-width: 100%;
  height: auto;
}
.markdown-content pre.mermaid-error {
  padding: 12px 16px;
  border: 1px solid var(--color-error);
  border-radius: 6px;
  background: var(--color-error-soft);
  color: var(--color-error);
  white-space: pre-wrap;
  font-family: var(--font-ui-mono);
  font-size: .875em;
}
</style>
