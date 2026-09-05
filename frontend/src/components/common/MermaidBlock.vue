<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { renderMermaid, useMermaidTheme } from '@/services/mermaidService'

const props = defineProps<{
  source: string
  interactive?: boolean
  zoomable?: boolean
}>()

const emit = defineEmits<{
  (e: 'error', message: string): void
  (e: 'rendered', info: { width: number; height: number }): void
}>()

const { mermaidTheme, themeId } = useMermaidTheme()
const svgHtml = ref('')
const isLoading = ref(true)
const hasError = ref(false)
const errorMessage = ref('')
const scale = ref(1)
let renderToken = 0

const canZoom = computed(() => props.zoomable ?? props.interactive ?? false)

async function doRender() {
  const token = ++renderToken
  isLoading.value = true
  hasError.value = false
  try {
    const result = await renderMermaid(props.source, {
      theme: mermaidTheme.value,
      mode: props.interactive ? 'interactive' : 'static',
    })
    if (token !== renderToken) return
    svgHtml.value = result.svg
    if (result.warnings.length > 0) {
      hasError.value = true
      errorMessage.value = result.warnings.join('\n')
      emit('error', result.warnings[0])
    }
    emit('rendered', { width: result.width, height: result.height })
  } catch (err) {
    if (token !== renderToken) return
    hasError.value = true
    errorMessage.value = err instanceof Error ? err.message : '渲染失败'
    emit('error', errorMessage.value)
  } finally {
    if (token === renderToken) isLoading.value = false
  }
}

onMounted(doRender)

watch(() => [props.source, mermaidTheme.value, themeId.value], () => { scale.value = 1; doRender() }, { flush: 'post' })

function zoomIn() { scale.value = Math.min(scale.value * 1.2, 5) }
function zoomOut() { scale.value = Math.max(scale.value / 1.2, 0.2) }
function zoomReset() { scale.value = 1 }
</script>

<template>
  <div class="mermaid-block" :class="{ interactive, 'has-error': hasError }">
    <div v-if="isLoading" class="mermaid-loading">
      <span class="loading-spinner"></span>
      <span>正在渲染 Mermaid 图表…</span>
    </div>
    <div
      v-else
      class="mermaid-container"
      :style="{ transform: `scale(${scale})`, transformOrigin: 'top left' }"
      v-html="svgHtml"
    />
    <div v-if="canZoom && !isLoading" class="mermaid-toolbar">
      <button class="toolbar-btn" @click="zoomOut" title="缩小">−</button>
      <span class="zoom-level">{{ Math.round(scale * 100) }}%</span>
      <button class="toolbar-btn" @click="zoomIn" title="放大">+</button>
      <button class="toolbar-btn" @click="zoomReset" title="重置">⟲</button>
    </div>
    <div v-if="hasError" class="mermaid-error">
      <strong>渲染失败</strong>
      <pre>{{ errorMessage }}</pre>
    </div>
  </div>
</template>

<style scoped>
.mermaid-block {
  position: relative;
  margin: .85em 0;
  padding: var(--space-md);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-surface-primary);
  overflow: auto;
  user-select: text;
}

.mermaid-block :deep(svg) {
  max-width: 100%;
  height: auto;
  display: block;
}

.mermaid-container {
  transition: transform var(--motion-fast);
}

.mermaid-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-sm);
  padding: var(--space-2xl);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}

.loading-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border-default);
  border-top-color: var(--color-accent-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.mermaid-toolbar {
  position: sticky;
  bottom: 4px;
  left: 0;
  right: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  padding: 4px 8px;
  margin-top: var(--space-sm);
  border-radius: var(--radius-md);
  background: var(--color-background-secondary);
  border: 1px solid var(--color-border-default);
}

.toolbar-btn {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  font-size: 14px;
  background: var(--color-surface-primary);
  border: 1px solid var(--color-border-default);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--motion-fast);
}
.toolbar-btn:hover {
  border-color: var(--color-accent-secondary);
  color: var(--color-accent-primary);
}

.zoom-level {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  min-width: 44px;
  text-align: center;
  font-family: var(--font-ui-mono);
}

.mermaid-error {
  margin-top: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  border-radius: var(--radius-sm);
  background: var(--color-error-soft);
  color: var(--color-error);
  font-size: var(--font-size-sm);
}
.mermaid-error strong { display: block; margin-bottom: 4px; }
.mermaid-error pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: var(--font-ui-mono);
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}

.has-error .mermaid-container {
  opacity: 0.6;
}
</style>
