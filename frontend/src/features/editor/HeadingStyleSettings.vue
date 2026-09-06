<script setup lang="ts">
import { useHeadingAppearanceStore } from '@/stores/headingAppearance'
import { t } from '@/i18n'
const appearance = useHeadingAppearanceStore()
</script>

<template>
  <details class="ui-disclosure heading-style-settings">
    <summary>{{ t('标题样式', 'Heading styles') }}</summary>
    <div class="heading-settings-body">
      <label><input v-model="appearance.preferences.custom" type="checkbox" /> {{ t('自定义标题样式', 'Customize heading styles') }}</label>
      <p class="subtle">{{ t('关闭时跟随主题。设置作用于正文 H1–H6，不改写 Markdown，也不改变笔记属性标题。', 'Disable to follow the theme. Applies to document H1–H6 without rewriting Markdown or the metadata title.') }}</p>
      <label>{{ t('标题字体', 'Heading font') }}
        <select v-model="appearance.preferences.family" class="select" :disabled="!appearance.preferences.custom">
          <option value="inherit">{{ t('跟随正文', 'Follow body') }}</option><option value="serif">{{ t('衬线字体', 'Serif') }}</option><option value="sans-serif">{{ t('无衬线字体', 'Sans serif') }}</option><option value="monospace">{{ t('等宽字体', 'Monospace') }}</option>
        </select>
      </label>
      <div v-for="(level, index) in appearance.preferences.levels" :key="index" class="heading-setting-row">
        <strong>H{{ index + 1 }}</strong>
        <label>{{ t('字号 px', 'Size px') }}<input v-model.number="level.size" class="input" type="number" min="12" max="72" :disabled="!appearance.preferences.custom" :aria-label="`H${index + 1} ${t('字号', 'size')}`" /></label>
        <label>{{ t('粗细', 'Weight') }}<select v-model.number="level.weight" class="select" :disabled="!appearance.preferences.custom" :aria-label="`H${index + 1} ${t('粗细', 'weight')}`"><option :value="400">{{ t('常规', 'Regular') }}</option><option :value="500">Medium</option><option :value="600">Semibold</option><option :value="700">{{ t('加粗', 'Bold') }}</option><option :value="800">Extra bold</option></select></label>
      </div>
      <button class="button-secondary" type="button" @click="appearance.reset">{{ t('恢复跟随主题', 'Restore theme defaults') }}</button>
      <div class="heading-style-preview" :data-heading-style="appearance.preferences.custom ? 'custom' : undefined" :style="appearance.cssVariables">
        <div class="markdown-content"><component :is="`h${index + 1}`" v-for="(_, index) in appearance.preferences.levels" :key="index">H{{ index + 1 }} {{ t('标题预览', 'Heading preview') }}</component></div>
      </div>
    </div>
  </details>
</template>

<style scoped>
.heading-settings-body { display: grid; gap: 14px; padding: 16px; }
.heading-setting-row { display: grid; grid-template-columns: 40px minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: end; }
.heading-setting-row label { display: grid; gap: 6px; min-width: 0; }
.heading-setting-row strong { align-self: center; }
.heading-style-preview { border: 1px solid var(--color-border-default); border-radius: var(--radius-md); padding: 16px; overflow-wrap: anywhere; background: var(--color-surface-primary); color: var(--color-text-primary); }
</style>
