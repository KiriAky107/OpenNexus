<script setup lang="ts">
import { computed } from 'vue'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import { useThemeStore } from '@/stores/theme'
import { t } from '@/i18n'

const themeStore = useThemeStore()
const shikiPreview = `\`\`\`typescript
const notes = await search('本地优先')
\`\`\``
const codeThemeLabel = computed(() => themeStore.resolvedCodeBlockTheme === 'github-dark'
  ? 'Shiki · GitHub Dark'
  : 'Shiki · GitHub Light')
</script>

<template>
  <section class="feature-page">
    <header class="feature-header"><div><h1>{{ t('主题', 'Themes') }}</h1><p>{{ t('预览并切换 Design Token，编辑器偏好会即时生效。', 'Preview and switch design tokens. Editor preferences apply immediately.') }}</p></div><button class="button-secondary" @click="themeStore.resetToDefault">{{ t('恢复默认', 'Reset defaults') }}</button></header>
    <div class="feature-grid themes">
      <button v-for="theme in themeStore.themes" :key="theme.theme_id" class="item-card theme-card" :class="{ selected: themeStore.currentThemeId === theme.theme_id }" @click="themeStore.applyTheme(theme.theme_id)">
        <div class="theme-preview" :class="`preview-${theme.theme_id}`"><span></span><span></span><span></span><div></div></div>
        <div class="theme-info"><div><strong>{{ theme.name }}</strong><p class="subtle">{{ theme.description }}</p></div><span v-if="themeStore.currentThemeId === theme.theme_id" class="badge success">{{ t('使用中', 'Active') }}</span></div>
        <p class="subtle">v{{ theme.version }} · {{ theme.builtin ? t('内置主题', 'Built-in theme') : theme.author }}</p>
      </button>
    </div>
    <div class="panel preference-panel">
      <h2 class="panel-title">{{ t('编辑器外观', 'Editor Appearance') }}</h2>
      <div class="form-grid">
        <div class="field"><label>{{ t('字号', 'Font size') }}: {{ themeStore.fontEditorSize }}px</label><input v-model.number="themeStore.fontEditorSize" type="range" min="12" max="24" /></div>
        <div class="field"><label>{{ t('行高', 'Line height') }}: {{ themeStore.lineHeight }}</label><input v-model.number="themeStore.lineHeight" type="range" min="1.2" max="2.2" step="0.1" /></div>
        <div class="field"><label>{{ t('字体', 'Font') }}</label><select v-model="themeStore.fontEditorFamily" class="select"><option value="system-ui">{{ t('系统字体', 'System font') }}</option><option value="serif">{{ t('衬线字体', 'Serif') }}</option><option value="var(--font-ui-mono)">{{ t('等宽字体', 'Monospace') }}</option></select></div>
        <div class="field"><label>{{ t('代码块样式', 'Code block style') }}</label><select v-model="themeStore.codeBlockTheme" class="select"><option value="auto">{{ t('跟随主题', 'Follow theme') }}</option><option value="github-light">GitHub Light</option><option value="github-dark">GitHub Dark</option></select><small>{{ t('Markdown 渲染使用对应的 Shiki GitHub 主题', 'Markdown rendering uses the matching Shiki GitHub theme') }}</small></div>
      </div>
      <div class="editor-preview" :style="{ fontSize: `${themeStore.fontEditorSize}px`, lineHeight: themeStore.lineHeight, fontFamily: themeStore.fontEditorFamily }">
        <div class="preview-heading"><h3>{{ t('主题预览', 'Theme Preview') }}</h3><span class="badge info">{{ codeThemeLabel }}</span></div>
        <p>{{ t('知识的价值不只在于保存，更在于被重新发现和使用。', 'Knowledge gains value when it can be rediscovered and used.') }}</p>
        <MarkdownContent class="code-theme-preview" :source="shikiPreview" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.themes { margin-bottom: var(--space-xl); }
.theme-card { display: grid; gap: var(--space-md); text-align: left; }
.theme-preview { display: grid; grid-template-columns: 30px 1fr; grid-template-rows: repeat(3, 18px); gap: 6px; height: 120px; padding: var(--space-md); border-radius: var(--radius-md); background: #fff; border: 1px solid #ddd; }
.theme-preview span { grid-column: 1; border-radius: 4px; background: #dfe3eb; }
.theme-preview div { grid-column: 2; grid-row: 1 / 4; border-radius: 6px; background: #f4f5f7; }
.preview-dark { background: #0d1117; border-color: #30363d; }.preview-dark span { background: #30363d; }.preview-dark div { background: #161b22; }
.preview-sepia { background: #fbf3df; border-color: #ddcfad; }.preview-sepia span { background: #d8c69c; }.preview-sepia div { background: #f4e8ca; }
.theme-info { display: flex; justify-content: space-between; gap: var(--space-md); }
.preference-panel { display: grid; gap: var(--space-xl); }
.editor-preview { padding: var(--space-xl); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-background-secondary); }
.editor-preview p { margin: var(--space-sm) 0; }
.preview-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); }
.field small { color: var(--color-text-tertiary); }
.code-theme-preview { margin-top: var(--space-md); }
</style>
