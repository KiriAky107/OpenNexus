<script setup lang="ts">
import { computed,ref,defineAsyncComponent } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { validateCanvasContent } from '@/services/workspaceDocuments'
import CanvasSourceEditor from './CanvasSourceEditor.vue'
import { t } from '@/i18n'
const CanvasVisualEditor=defineAsyncComponent(()=>import('./CanvasVisualEditor.vue'))
const editor=useEditorStore(),source=ref(false)
const diagnostic=computed(()=>{try{validateCanvasContent(editor.content);return''}catch(cause){return String(cause)}})
</script>
<template>
  <section class="canvas-editor">
    <div v-if="source||diagnostic" class="canvas-mode"><strong>JSON Canvas</strong><button class="button-secondary" :aria-pressed="source" :disabled="!!diagnostic" @click="source=false">{{ t('显示可视化画布','Show visual canvas') }}</button></div>
    <p v-if="diagnostic" class="error-banner" role="alert">{{ t('无法显示画布，请在源码中修复；原文件会保留。','Cannot display this canvas. Repair its source; the original file is preserved.') }} {{ diagnostic }}</p>
    <CanvasSourceEditor v-if="source||diagnostic" />
    <CanvasVisualEditor v-else><template #tools><button role="menuitem" @click="source=true">{{ t('查看 JSON 源码','View JSON source') }}</button></template></CanvasVisualEditor>
  </section>
</template>
<style scoped>
.canvas-editor{display:flex;flex:1;flex-direction:column;min-height:0;min-width:0}.canvas-mode{display:flex;align-items:center;justify-content:space-between;gap:var(--space-sm);padding:var(--space-sm) var(--space-lg);border-bottom:1px solid var(--color-border-default)}
</style>
