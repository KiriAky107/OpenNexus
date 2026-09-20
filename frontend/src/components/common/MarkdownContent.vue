<script setup lang="ts">
import DiagramInteractions from './DiagramInteractions.vue'
import { computed, ref, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { useThemeStore } from '@/stores/theme'
import { useHeadingAppearanceStore } from '@/stores/headingAppearance'
const headingAppearance = useHeadingAppearanceStore()
import { useMarkdownPreferencesStore } from '@/stores/markdownPreferences'
import { navigateMarkdownHref } from '@/services/markdownLinkService'
const markdownPreferences = useMarkdownPreferencesStore()

const props = defineProps<{ source: string; citationNumbers?: number[]; citationAliases?: Record<string, number> }>()
const emit = defineEmits<{ citation: [number: number] }>()
function citationClick(event: MouseEvent) {
  if (!(event.target instanceof Element)) return
  const number = Number(event.target.closest('[data-citation-number]')?.getAttribute('data-citation-number'))
  if (props.citationNumbers?.includes(number)) { event.preventDefault(); emit('citation', number) }
  if (event.defaultPrevented || event.button !== 0) return
  const link = event.target.closest<HTMLAnchorElement>('a[href]')
  const href = link?.getAttribute('href')?.trim()
  if (!href) return
  event.preventDefault()
  void navigateMarkdownHref(href)
}
const themeStore = useThemeStore()
const html = ref('')
let renderVersion = 0

const diagramTheme = computed<'light' | 'dark'>(() => (themeStore.isDark ? 'dark' : 'light'))

// 主题切换需要重渲染：Mermaid SVG 的配色在渲染时烘焙，无法靠 CSS 变量事后调整。
watch([() => props.source, diagramTheme, () => themeStore.currentThemeId, () => JSON.stringify(markdownPreferences.normalized), () => JSON.stringify([props.citationNumbers, props.citationAliases])], async ([source, theme]) => {
  const version = ++renderVersion
  const result = await renderMarkdown(source, { theme, themeId: themeStore.currentThemeId, preferences: markdownPreferences.normalized, citationNumbers: props.citationNumbers, citationAliases: props.citationAliases })
  if (version === renderVersion) html.value = result
}, { immediate: true, flush: 'post' })
</script>

<template>
  <DiagramInteractions :data-heading-style="headingAppearance.preferences.custom ? 'custom' : undefined" :style="headingAppearance.cssVariables"><div class="markdown-content" @click="citationClick" :data-code-wrap="markdownPreferences.normalized.wrapCode" :data-line-numbers="markdownPreferences.normalized.lineNumbers" :style="{ '--markdown-code-indent': markdownPreferences.normalized.indent }" v-html="html" /></DiagramInteractions>
</template>

<style>
.markdown-content { white-space: normal; user-select: text; }
.inline-citation { display: inline; padding: 0 .15em; border: 0; background: var(--color-accent-soft); color: var(--color-text-link); border-radius: var(--radius-sm); cursor: pointer; font: inherit; }
.inline-citation:focus-visible { outline: 2px solid var(--color-border-focus); }
.markdown-content p, .markdown-content ul, .markdown-content ol, .markdown-content pre, .markdown-content blockquote { margin: .65em 0; }
.markdown-content h1, .markdown-content h2, .markdown-content h3 { margin: 1em 0 .5em; line-height: var(--line-height-tight); }
.markdown-content ul { padding-left: 1.5em; list-style: disc; }
.markdown-content ol { padding-left: 1.5em; list-style: decimal; }
.markdown-content li::marker { color: var(--color-markdown-marker); font-weight: 700; }
.markdown-content .shiki { overflow: auto; margin: .85em 0; padding: 16px; border: 1px solid var(--color-code-border); border-radius: 6px; background: var(--color-code-background) !important; color: var(--color-code-text); font-family: var(--font-ui-mono); font-size: .875em; line-height: 1.45; tab-size: 4; }
.markdown-content code { padding: .1em .3em; border-radius: var(--radius-sm); background: var(--color-background-tertiary); font-family: var(--font-ui-mono); }
.markdown-content .shiki { font-family: var(--font-editor-mono); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); }
.markdown-content :not(pre) > code { background: var(--color-code-background); color: var(--color-code-text); border: 1px solid var(--color-code-border); }
.markdown-content div.markdown-math { overflow-x: auto; padding-block: .5em; }
.markdown-content h4, .markdown-content h5, .markdown-content h6 { margin: 1em 0 .5em; font-weight: 600; }
.markdown-content input[type="checkbox"] { margin-right: .45em; accent-color: var(--color-accent-primary); }
.markdown-content blockquote { padding-left: 1em; border-left: 3px solid var(--color-accent-primary); color: var(--color-text-secondary); }
.markdown-content table { width: 100%; margin: .65em 0; border-collapse: collapse; }
.markdown-content th, .markdown-content td { padding: .45em .65em; border: 1px solid var(--color-markdown-grid); text-align: left; }
.markdown-content th { background: var(--color-markdown-table-header); font-weight: 700; }
.markdown-content :is(th, td)[align="center"] { text-align: center; }
.markdown-content :is(th, td)[align="right"] { text-align: right; }
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
