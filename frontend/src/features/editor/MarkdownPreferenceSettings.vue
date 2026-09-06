<script setup lang="ts">
import { ref } from 'vue'
import { useMarkdownPreferencesStore, markdownPresets } from '@/stores/markdownPreferences'
import { t } from '@/i18n'
const store = useMarkdownPreferencesStore()
const name = ref('')
const error = ref('')
function save() { error.value = store.savePreset(name.value) ? '' : t('请输入名称，最多保存 20 个预设。', 'Enter a name; up to 20 presets.'); if (!error.value) name.value = '' }
</script>
<template>
  <section class="markdown-preferences surface-nested">
    <h3>{{ t('Markdown 语法与编辑预设', 'Markdown syntax and editing presets') }}</h3>
    <p class="subtle">{{ t('语法和代码设置在下次打开写作编辑器时应用；不批量改写已有笔记。静态预览即时更新。', 'Syntax and code settings apply when the visual editor next opens; existing notes are not rewritten in bulk. Static previews update immediately.') }}</p>
    <div class="inline-actions"><button class="button-secondary" @click="store.apply(markdownPresets.extended)">{{ t('扩展 Markdown', 'Extended Markdown') }}</button><button class="button-secondary" @click="store.apply(markdownPresets.github)">GitHub</button><button class="button-secondary" @click="store.apply(markdownPresets.plain)">{{ t('基础 Markdown', 'Basic Markdown') }}</button></div>
    <div class="form-grid">
      <label>{{ t('标题语法', 'Heading syntax') }}<select v-model="store.preferences.heading" class="select"><option value="atx">ATX (#)</option><option value="setext">Setext (=== / ---)</option></select></label>
      <label>{{ t('无序列表', 'Bullet list') }}<select v-model="store.preferences.bullet" class="select"><option>-</option><option>*</option><option>+</option></select></label>
      <label>{{ t('有序列表', 'Ordered list') }}<select v-model="store.preferences.incrementList" class="select"><option :value="true">1. 2. 3.</option><option :value="false">1. 1. 1.</option></select></label>
      <label>{{ t('代码围栏', 'Code fence') }}<select v-model="store.preferences.fence" class="select"><option value="`">```</option><option value="~">~~~</option></select></label>
    </div>
    <p class="subtle">{{ t('Setext 适用于 H1/H2，H3–H6 仍使用 #。写作模式保存时会规范化整篇正文的标记；源码模式保留手写语法。', 'Setext applies to H1/H2; H3–H6 use #. Visual-mode saves normalize document markers; source mode preserves handwritten syntax.') }}</p>
    <div class="markdown-switches">
      <label><input v-model="store.preferences.autoLinks" type="checkbox" />{{ t('自动识别裸链接', 'Recognize bare URLs') }}</label>
      <label><input v-model="store.preferences.math" type="checkbox" />{{ t('数学公式', 'Math') }}</label>
      <label><input v-model="store.preferences.callouts" type="checkbox" />{{ t('警告框与提示框', 'Alerts and callouts') }}</label>
      <label><input v-model="store.preferences.diagrams" type="checkbox" />Mermaid</label>
      <label><input v-model="store.preferences.lineNumbers" type="checkbox" />{{ t('代码行号', 'Code line numbers') }}</label>
      <label><input v-model="store.preferences.wrapCode" type="checkbox" />{{ t('代码自动换行', 'Wrap code') }}</label>
    </div>
    <div class="form-grid">
      <label>{{ t('代码缩进', 'Code indent') }}<select v-model.number="store.preferences.indent" class="select"><option :value="2">2</option><option :value="4">4</option><option :value="8">8</option></select></label>
      <label>{{ t('新建代码块默认语言', 'Default language for new code blocks') }}<input v-model="store.preferences.defaultLanguage" class="input" maxlength="40" placeholder="python" /></label>
    </div>
    <form class="inline-actions" @submit.prevent="save"><input v-model="name" class="input" maxlength="40" :aria-label="t('预设名称', 'Preset name')" :placeholder="t('我的预设名称', 'My preset name')" /><button class="button-primary">{{ t('保存为预设', 'Save preset') }}</button></form>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <div v-for="(preset, index) in store.customPresets" :key="preset.name" class="inline-actions"><strong>{{ preset.name }}</strong><button class="button-secondary" @click="store.apply(preset.preferences)">{{ t('应用', 'Apply') }}</button><button class="button-danger" @click="store.customPresets.splice(index, 1)">{{ t('删除', 'Delete') }}</button></div>
  </section>
</template>
<style scoped>
.markdown-preferences { display: grid; gap: 16px; padding: 16px; margin-block: 16px; }
.markdown-preferences .form-grid > label { display: grid; gap: 6px; min-width: 0; }
.markdown-switches { display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap: 12px; }
.markdown-switches label { display: flex; align-items: center; gap: 8px; }
.inline-actions { flex-wrap: wrap; }
</style>
