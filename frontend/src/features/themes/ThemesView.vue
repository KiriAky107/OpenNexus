<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'
const themeStore = useThemeStore()
</script>

<template>
  <section class="feature-page">
    <header class="feature-header"><div><h1>主题</h1><p>预览并切换 Design Token，编辑器偏好会即时生效。</p></div><button class="button-secondary" @click="themeStore.resetToDefault">恢复默认</button></header>
    <div class="feature-grid themes">
      <button v-for="theme in themeStore.themes" :key="theme.theme_id" class="item-card theme-card" :class="{ selected: themeStore.currentThemeId === theme.theme_id }" @click="themeStore.applyTheme(theme.theme_id)">
        <div class="theme-preview" :class="`preview-${theme.theme_id}`"><span></span><span></span><span></span><div></div></div>
        <div class="theme-info"><div><strong>{{ theme.name }}</strong><p class="subtle">{{ theme.description }}</p></div><span v-if="themeStore.currentThemeId === theme.theme_id" class="badge success">使用中</span></div>
        <p class="subtle">v{{ theme.version }} · {{ theme.builtin ? '内置主题' : theme.author }}</p>
      </button>
    </div>
    <div class="panel preference-panel"><h2 class="panel-title">编辑器外观</h2><div class="form-grid"><div class="field"><label>字号：{{ themeStore.fontEditorSize }}px</label><input v-model.number="themeStore.fontEditorSize" type="range" min="12" max="24" /></div><div class="field"><label>行高：{{ themeStore.lineHeight }}</label><input v-model.number="themeStore.lineHeight" type="range" min="1.2" max="2.2" step="0.1" /></div><div class="field"><label>字体</label><select v-model="themeStore.fontEditorFamily" class="select"><option value="system-ui">系统字体</option><option value="serif">衬线字体</option><option value="var(--font-ui-mono)">等宽字体</option></select></div></div><div class="editor-preview" :style="{ fontSize: `${themeStore.fontEditorSize}px`, lineHeight: themeStore.lineHeight, fontFamily: themeStore.fontEditorFamily }"><h3>主题预览</h3><p>知识的价值不只在于保存，更在于被重新发现和使用。</p><code>const notes = await search('本地优先')</code></div></div>
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
.editor-preview p { margin: var(--space-sm) 0; }.editor-preview code { color: var(--color-accent-primary); }
</style>
