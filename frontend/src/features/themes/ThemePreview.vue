<script setup lang="ts">
import '@/styles/theme-preview-tokens.css'
defineProps<{ variant: 'light' | 'dark' | 'sepia' | 'paper' }>()
</script>

<template><div class="theme-preview" :class="`preview-${variant}`" :data-theme-preview="variant" aria-hidden="true"><span></span><span></span><span></span><div></div></div></template>

<style scoped>
.theme-preview { display: grid; grid-template-columns: 30px 1fr; grid-template-rows: repeat(3, 18px); gap: 6px; height: 120px; padding: var(--space-md); border-radius: var(--radius-md); background: var(--color-preview-background); border: 1px solid var(--color-preview-border); }
.theme-preview span { grid-column: 1; border-radius: 4px; background: var(--color-preview-node); }
.theme-preview div { grid-column: 2; grid-row: 1 / 4; border-radius: 6px; background: var(--color-preview-surface); }
.preview-paper { border-style: dashed; box-shadow: 3px 3px 0 var(--color-preview-stack-back), 6px 6px 0 var(--color-preview-stack-front); }
.preview-paper span:nth-child(2) { background: var(--color-preview-node-secondary); }
.preview-paper span:nth-child(3) { background: var(--color-preview-node-tertiary); }
.preview-paper div { border: 1px solid var(--color-preview-rule-border); background: repeating-linear-gradient(var(--color-preview-surface) 0 14px, var(--color-preview-rule) 14px 15px); }
</style>
