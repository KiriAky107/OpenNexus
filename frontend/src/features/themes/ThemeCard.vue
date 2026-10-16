<script setup lang="ts">
import ThemePreview from './ThemePreview.vue'
import { t } from '@/i18n'
defineProps<{ name: string; description?: string; selected: boolean; preview: 'light' | 'dark' | 'sepia' | 'paper' }>()
defineEmits<{ select: [] }>()
</script>

<template>
  <article class="item-card theme-card" :class="{ selected }">
    <button type="button" class="theme-select" :aria-label="name" :aria-pressed="selected" @click="$emit('select')">
      <ThemePreview :variant="preview" />
      <span class="theme-info"><span><strong>{{ name }}</strong><span v-if="description" class="subtle description">{{ description }}</span></span><span v-if="selected" class="badge success">{{ t('使用中', 'Active') }}</span></span>
      <span v-if="$slots.meta" class="subtle"><slot name="meta" /></span>
    </button>
    <div v-if="$slots.actions" class="theme-actions"><slot name="actions" /></div>
  </article>
</template>

<style scoped>
.theme-card, .theme-select { display: grid; gap: var(--space-md); text-align: left; position: relative; min-width: 0; }
.theme-select { width: 100%; padding: 0; border: 0; background: transparent; color: var(--color-text-primary); font: inherit; cursor: pointer; }
.theme-select:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 6px; border-radius: var(--radius-sm); }
.theme-info { display: flex; justify-content: space-between; gap: var(--space-md); align-items: flex-start; }
.theme-info strong { display: block; margin-bottom: 2px; }
.theme-info > .badge { flex-shrink: 0; white-space: nowrap; }
.description { display: block; margin-block: 1em; }
.theme-actions { display: flex; gap: var(--space-sm); margin-top: 4px; }
</style>
