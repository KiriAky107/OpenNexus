<script setup lang="ts">
import AppDialog from '@/components/common/AppDialog.vue'
import { computed } from 'vue'
import { t } from '@/i18n'
import { getCommunityThemePreviewCss, mockCommunityThemes } from '@/services/themePackageService'
import tokensCss from '@/styles/tokens.css?raw'
import featuresCss from '@/styles/features.css?raw'
import headingsCss from '@/styles/headings.css?raw'
import markdownBehaviorCss from '@/styles/markdown-behavior.css?raw'
import calloutsCss from '@/styles/callouts.css?raw'
import { calloutTypes } from '@/utils/callouts'
import specimenHtml from './themeSpecimen.html?raw'

const props = defineProps<{ themeId: string; name?: string; css?: string }>()
const emit = defineEmits<{ (event: 'close'): void }>()
const theme = computed(() => props.name ? { name: props.name } : mockCommunityThemes.find(item => item.theme_id === props.themeId))
const previewDocument = computed(() => {
  // 导入和捆绑的 CSS 都可以在无脚本的独立文档中预览。预览永远不会安装主题或更改应用程序样式/存储。
  const doc = document.implementation.createHTMLDocument(theme.value?.name ?? '')
  doc.documentElement.dataset.theme = props.themeId
  const policy = doc.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'"
  doc.head.append(policy)
  const style = doc.createElement('style')
  style.textContent = `${tokensCss}\n${featuresCss}\n${calloutsCss}\n${headingsCss}\n${props.css ?? getCommunityThemePreviewCss(props.themeId)}\nhtml { height:100% !important; overflow-y:auto !important; overflow-x:hidden !important; overscroll-behavior:contain; } body { height:auto !important; min-height:100%; overflow:visible !important; margin:0; padding:24px; background:var(--color-background-primary); color:var(--color-text-primary); font:16px/1.6 system-ui; } article { min-width:0; } .theme-specimen { display:grid; gap:16px; margin-top:20px; } .surface-nested { padding:12px; border:1px solid var(--color-border-default); border-radius:var(--radius-md); } .specimen-markdown { overflow:auto; } .specimen-markdown code { background:var(--color-code-background, var(--color-background-secondary)); color:var(--color-code-text, var(--color-text-primary)); padding:3px 6px; border-radius:4px; } .specimen-markdown pre { padding:12px; background:var(--color-background-secondary); } .specimen-markdown blockquote { border-left:3px solid var(--color-accent-primary); padding-left:12px; } .specimen-markdown table { width:100%; border-collapse:collapse; } .specimen-markdown td,.specimen-markdown th { padding:8px; border:1px solid var(--color-border-default); } .specimen-markdown th { background:var(--color-markdown-table-header); } .specimen-chart > div { display:flex; align-items:flex-end; gap:12px; height:100px; border-bottom:1px solid var(--color-border-default); } .specimen-chart span { width:36px; } .specimen-long { overflow-wrap:anywhere; } @media(max-width:480px) { body { padding:12px; } .form-grid { grid-template-columns:minmax(0,1fr); } }`

  style.textContent += `\n${markdownBehaviorCss}\n.specimen-scroll { position:relative; min-height:110px; }`
  doc.head.append(style)
  const article = doc.createElement('article')
  article.className = 'panel'
  const header = doc.createElement('header'); header.className = 'feature-header'
  const heading = doc.createElement('h1'); heading.textContent = theme.value?.name ?? props.themeId
  header.append(heading)
  const journal = doc.createElement('section'); journal.className = 'editor-preview'; journal.style.cssText = 'padding:24px;margin:28px 0;'
  const text = doc.createElement('p'); text.textContent = t('知识的价值不只在于保存，更在于被重新发现和使用。', 'Knowledge gains value when it can be rediscovered and used.')
  const button = doc.createElement('button'); button.className = 'button-primary'; button.textContent = t('示例按钮', 'Example button')
  journal.append(text)
  const specimen = doc.createElement('template'); specimen.innerHTML = specimenHtml
  const callouts = doc.createElement('section'); callouts.className = 'specimen-callouts'
  const caption = doc.createElement('h2'); caption.textContent = t('警告框与提示框', 'Alerts and callouts'); callouts.append(caption)
  for (const type of Object.keys(calloutTypes)) {
    const block = doc.createElement('aside'); block.className = 'markdown-callout'; block.dataset.callout = type
    const title = doc.createElement('div'); title.className = 'callout-title'; title.textContent = type
    const body = doc.createElement('div'); body.className = 'callout-body'; body.textContent = t('提示正文：检查文字、边框与主题配色。', 'Callout body: check text, borders and theme colors.')
    block.append(title, body); callouts.append(block)
  }
  for (const open of [false, true]) {
    const details = doc.createElement('details'); details.className = 'markdown-callout'; details.dataset.callout = 'warning'; details.open = open
    const summary = doc.createElement('summary'); summary.className = 'callout-title'; summary.textContent = t('可折叠警告框', 'Collapsible callout')
    const body = doc.createElement('div'); body.className = 'callout-body'
    const nested = doc.createElement('aside'); nested.className = 'markdown-callout'; nested.dataset.callout = 'tip'; nested.textContent = t('嵌套提示内容', 'Nested callout content')
    body.append(nested); details.append(summary, body); callouts.append(details)
  }
  specimen.content.append(callouts)
  article.append(header, journal, button, specimen.content); doc.body.append(article)
  return '<!doctype html>' + doc.documentElement.outerHTML
})
</script>

<template>
  <AppDialog :label="t('社区主题预览', 'Community theme preview')" @close="emit('close')">
    <section class="modal theme-preview-dialog">
      <div class="preview-heading"><h2>{{ theme?.name }}</h2><button class="button-secondary" autofocus @click="emit('close')">{{ t('关闭预览', 'Close preview') }}</button></div>
      <iframe :title="`${t('主题预览', 'Theme preview')}: ${theme?.name ?? themeId}`" sandbox="" :srcdoc="previewDocument" />
      <p class="subtle">{{ t('仅预览，不会安装或更改当前主题。', 'Preview only. Your installed themes and current appearance remain unchanged.') }}</p>
    </section>
  </AppDialog>
</template>

<style scoped>
.theme-preview-dialog { width: min(720px, calc(100vw - 32px)); }
.preview-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
iframe { display: block; width: 100%; height: min(420px, 60vh); margin: 16px 0; border: 1px solid var(--color-border-default); border-radius: 8px; }
</style>
