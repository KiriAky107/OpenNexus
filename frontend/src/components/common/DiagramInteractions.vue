<script setup lang="ts">
import { nextTick, ref } from 'vue'
import DOMPurify from 'dompurify'

const viewer = ref<HTMLDialogElement | null>(null)
const svgHtml = ref('')
const scale = ref(1)
const baseWidth = ref(800)
let opener: HTMLElement | null = null
function widthOf(svg: SVGSVGElement) {
  return svg.viewBox?.baseVal?.width || Number(svg.getAttribute('viewBox')?.split(/[ ,]+/)[2]) || svg.getBoundingClientRect().width || 800
}
async function interact(event: MouseEvent) {
  if (!(event.target instanceof Element)) return
  const button = event.target.closest<HTMLElement>('[data-diagram-action]')
  const diagram = button?.closest<HTMLElement>('.editor-mermaid-preview, .markdown-mermaid')
  const svg = diagram?.querySelector<SVGSVGElement>('svg')
  if (!button || !diagram || !svg) return
  event.preventDefault()
  event.stopPropagation()
  const action = button.dataset.diagramAction
  if (action === 'view') {
    opener = button
    baseWidth.value = widthOf(svg)
    svgHtml.value = DOMPurify.sanitize(svg.outerHTML, { USE_PROFILES: { svg: true, svgFilters: true, html: true } })
    scale.value = 1
    await nextTick()
    viewer.value?.showModal()
    return
  }
  const previous = Number(diagram.dataset.diagramScale || 1)
  const next = action === 'reset' ? 1 : Math.max(.2, Math.min(5, previous * (action === 'in' ? 1.2 : 1 / 1.2)))
  diagram.dataset.diagramScale = String(next)
  svg.style.width = next === 1 ? '' : `${widthOf(svg) * next}px`
  svg.style.maxWidth = next === 1 ? '' : 'none'
  svg.style.height = 'auto'
}
function close() { viewer.value?.close(); svgHtml.value = ''; opener?.focus() }
</script>

<template>
  <div class="diagram-interactions" @click.capture="interact">
    <slot />
    <Teleport to="body">
      <dialog ref="viewer" class="diagram-viewer" aria-label="图表大图查看" @cancel.prevent="close">
        <header><strong>图表查看</strong><div class="diagram-controls">
          <button type="button" @click="scale = Math.max(.2, scale / 1.2)">缩小</button>
          <output>{{ Math.round(scale * 100) }}%</output>
          <button type="button" @click="scale = Math.min(5, scale * 1.2)">放大</button>
          <button type="button" @click="scale = 1">重置</button>
          <button type="button" autofocus @click="close">关闭</button>
        </div></header>
        <div class="diagram-viewer-scroll"><div class="diagram-viewer-image" :style="{ width: `${baseWidth * scale}px` }" v-html="svgHtml" /></div>
      </dialog>
    </Teleport>
  </div>
</template>

<style>
.diagram-interactions { min-width: 0; }
.diagram-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.diagram-controls button { padding: 5px 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); color: var(--color-text-primary); background: var(--color-surface-primary); cursor: pointer; font: inherit; font-size: 12px; }
.diagram-controls button:hover { border-color: var(--color-accent-primary); }
.diagram-controls button:focus-visible { outline: 2px solid var(--color-accent-primary); }
.diagram-viewer { width: min(1200px, 94vw); max-width: 94vw; height: 85vh; padding: 16px; color: var(--color-text-primary); background: var(--color-background-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
.diagram-viewer::backdrop { background: #0008; }
.diagram-viewer header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.diagram-viewer-scroll { height: calc(100% - 64px); overflow: auto; }
.diagram-viewer-image { margin: auto; }
.diagram-viewer-image svg { width: 100% !important; max-width: none !important; height: auto !important; }
</style>
