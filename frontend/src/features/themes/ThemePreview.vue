<script setup lang="ts">
defineProps<{ variant: 'light' | 'dark' | 'sepia' | 'paper' }>()
</script>

<template><div class="theme-preview" :class="`preview-${variant}`" aria-hidden="true"><span></span><span></span><span></span><div></div></div></template>

<style scoped>
.theme-preview { display: grid; grid-template-columns: 30px 1fr; grid-template-rows: repeat(3, 18px); gap: 6px; height: 120px; padding: var(--space-md); border-radius: var(--radius-md); background: #fff; border: 1px solid #ddd; }
.theme-preview span { grid-column: 1; border-radius: 4px; background: #dfe3eb; }
.theme-preview div { grid-column: 2; grid-row: 1 / 4; border-radius: 6px; background: #f4f5f7; }
.preview-dark { background: #0d1117; border-color: #30363d; }
.preview-dark span { background: #30363d; }
.preview-dark div { background: #161b22; }
.preview-sepia { background: #fbf3df; border-color: #ddcfad; }
.preview-sepia span { background: #d8c69c; }
.preview-sepia div { background: #f4e8ca; }
.preview-paper { background: #fffdf5; border: 1px dashed #8b7865; box-shadow: 3px 3px 0 #d8e6e2, 6px 6px 0 #f0d8cf; }
.preview-paper span { background: #efd8d0; }
.preview-paper span:nth-child(2) { background: #d8e7e8; }
.preview-paper span:nth-child(3) { background: #f6e9b8; }
.preview-paper div { border: 1px solid #b5a693; background: repeating-linear-gradient(#fffef8 0 14px, #dce4db 14px 15px); }
</style>
