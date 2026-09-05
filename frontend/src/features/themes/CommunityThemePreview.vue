<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
import { getCommunityThemePreviewCss, mockCommunityThemes } from '@/services/themePackageService'
import tokensCss from '@/styles/tokens.css?raw'

const props = defineProps<{ themeId: string }>()
const emit = defineEmits<{ (event: 'close'): void }>()
const theme = computed(() => mockCommunityThemes.find(item => item.theme_id === props.themeId))
const previewDocument = computed(() => {
  // Only bundled community CSS enters this script-free, isolated document.
  // Previewing never installs a theme or changes application styles/storage.
  const doc = document.implementation.createHTMLDocument(theme.value?.name ?? '')
  doc.documentElement.dataset.theme = props.themeId
  const style = doc.createElement('style')
  style.textContent = `${tokensCss}\n${getCommunityThemePreviewCss(props.themeId)}\nbody { margin:0; padding:24px; background:var(--color-background-primary); color:var(--color-text-primary); font:16px/1.6 system-ui; } article { padding:20px; border:1px solid var(--color-border-default); border-radius:8px; background:var(--color-surface-primary); } p { color:var(--color-text-secondary); } button { padding:8px 16px; border:0; border-radius:6px; background:var(--color-accent-primary); color:white; }`
  doc.head.append(style)
  const article = doc.createElement('article')
  const heading = doc.createElement('h1'); heading.textContent = theme.value?.name ?? props.themeId
  const text = doc.createElement('p'); text.textContent = t('知识的价值不只在于保存，更在于被重新发现和使用。', 'Knowledge gains value when it can be rediscovered and used.')
  const button = doc.createElement('button'); button.textContent = t('示例按钮', 'Example button')
  article.append(heading, text, button); doc.body.append(article)
  return '<!doctype html>' + doc.documentElement.outerHTML
})
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')" @keydown.esc="emit('close')">
    <section class="modal theme-preview-dialog" role="dialog" aria-modal="true" :aria-label="t('社区主题预览', 'Community theme preview')">
      <div class="preview-heading"><h2>{{ theme?.name }}</h2><button class="button-secondary" autofocus @click="emit('close')">{{ t('关闭预览', 'Close preview') }}</button></div>
      <iframe :title="`${t('主题预览', 'Theme preview')}: ${theme?.name ?? themeId}`" sandbox="" :srcdoc="previewDocument" />
      <p class="subtle">{{ t('仅预览，不会安装或更改当前主题。', 'Preview only. Your installed themes and current appearance remain unchanged.') }}</p>
    </section>
  </div>
</template>

<style scoped>
.theme-preview-dialog { width: min(720px, calc(100vw - 32px)); }
.preview-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
iframe { display: block; width: 100%; height: min(420px, 60vh); margin: 16px 0; border: 1px solid var(--color-border-default); border-radius: 8px; }
</style>
