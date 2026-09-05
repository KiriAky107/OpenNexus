<script setup lang="ts">
import { computed } from 'vue'
import { toolDescription, toolLabel } from './labels'
import { t } from '@/i18n'

const props = defineProps<{ name: string; description: string; selected: boolean }>()
const emit = defineEmits<{ toggle: [name: string] }>()
const summary = computed(() => toolDescription(props.name, props.description))
const showOriginal = computed(() => props.description.length > 0)
</script>

<template>
  <article class="tool-choice" :class="{ selected }">
    <label class="tool-selection">
      <input type="checkbox" :checked="selected" @change="emit('toggle', name)" />
      <span class="tool-copy">
        <strong>{{ toolLabel(name) }}</strong>
        <code>{{ name }}</code>
        <small class="tool-summary">{{ summary }}</small>
      </span>
    </label>
    <details v-if="showOriginal" class="tool-original">
      <summary>{{ t('查看服务原文与参数', 'View original service description and parameters') }}</summary>
      <p>{{ description }}</p>
    </details>
  </article>
</template>

<style scoped>
.tool-choice { min-width: 0; padding: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); }
.tool-choice.selected { border-color: var(--color-accent-primary); background: var(--color-accent-soft); }
.tool-selection { display: flex; align-items: flex-start; gap: var(--space-sm); cursor: pointer; }
.tool-selection input { flex-shrink: 0; margin-top: 4px; }
.tool-copy { min-width: 0; overflow-wrap: anywhere; }
.tool-copy strong, .tool-copy code, .tool-summary { display: block; }
.tool-copy code { margin: 3px 0; color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.tool-summary { color: var(--color-text-secondary); line-height: 1.6; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.tool-original { margin-top: var(--space-sm); font-size: var(--font-size-xs); }
.tool-original summary { cursor: pointer; color: var(--color-text-secondary); }
.tool-original p { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 240px; overflow: auto; margin-top: var(--space-sm); user-select: text; }
</style>
