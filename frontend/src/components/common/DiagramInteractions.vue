<script setup lang="ts">
import { nextTick, ref, onBeforeUnmount } from 'vue'
import AppIcon from './AppIcon.vue'
import { ZoomIn, ZoomOut, Refresh, Close } from '@element-plus/icons-vue'
import DOMPurify from 'dompurify'

const viewer = ref<HTMLDialogElement | null>(null)
const svgHtml = ref('')
const scale = ref(1)
const baseWidth = ref(800)
let opener: HTMLElement | null = null
let wheelTarget: HTMLElement | null = null
let anchor = { x: 0, y: 0 }
const wheelActive = ref(false)
function disarm() {
  wheelTarget?.removeAttribute('data-wheel-zoom')
  wheelTarget = null; wheelActive.value = false
  document.removeEventListener('mousemove', moved, true)
  document.removeEventListener('wheel', wheel, true)
  window.removeEventListener('blur', disarm)
}
function moved(event: MouseEvent) { if (event.clientX !== anchor.x || event.clientY !== anchor.y) disarm() }
function arm(event: MouseEvent) {
  if (event.button !== 1 || !(event.target instanceof Element) || !event.target.closest('svg') || event.target.closest('.diagram-controls')) return
  const target = event.target.closest<HTMLElement>('.editor-mermaid-preview, .markdown-mermaid, .diagram-viewer-image')
  if (!target) return
  event.preventDefault(); event.stopPropagation(); disarm()
  wheelTarget = target; wheelActive.value = true; anchor = { x: event.clientX, y: event.clientY }
  target.dataset.wheelZoom = 'true'
  document.addEventListener('mousemove', moved, true)
  document.addEventListener('wheel', wheel, { capture: true, passive: false })
  window.addEventListener('blur', disarm)
}
function wheel(event: WheelEvent) {
  if (!wheelTarget?.isConnected || !(event.target instanceof Node) || !wheelTarget.contains(event.target)) { disarm(); return }
  event.preventDefault(); event.stopPropagation()
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1)
  const factor = Math.exp(-Math.max(-200, Math.min(200, delta)) * .002)
  if (wheelTarget.classList.contains('diagram-viewer-image')) scale.value = Math.max(.2, Math.min(5, scale.value * factor))
  else zoom(wheelTarget, Math.max(.2, Math.min(5, Number(wheelTarget.dataset.diagramScale || 1) * factor)))
}
function zoom(diagram: HTMLElement, next: number) {
  const svg = diagram.querySelector<SVGSVGElement>('svg')
  if (!svg) return
  diagram.dataset.diagramScale = String(next)
  svg.style.width = next === 1 ? '' : `${widthOf(svg) * next}px`
  svg.style.maxWidth = next === 1 ? '' : 'none'
  svg.style.height = 'auto'
}
onBeforeUnmount(disarm)
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
    // Mermaid HTML labels live in SVG foreignObject nodes. Preserve that
    // integration point while still sanitizing the embedded HTML and handlers.
    const copy = svg.cloneNode(true) as SVGSVGElement
    for (const label of copy.querySelectorAll('foreignObject, foreignobject')) {
      label.innerHTML = DOMPurify.sanitize(label.innerHTML, { USE_PROFILES: { html: true } })
    }
    svgHtml.value = DOMPurify.sanitize(copy.outerHTML, {
      USE_PROFILES: { svg: true, svgFilters: true, html: true },
      ADD_TAGS: ['foreignObject'], ADD_ATTR: ['xmlns'],
      HTML_INTEGRATION_POINTS: { foreignobject: true },
    })
    scale.value = 1
    await nextTick()
    viewer.value?.showModal()
    return
  }
  const previous = Number(diagram.dataset.diagramScale || 1)
  const next = action === 'reset' ? 1 : Math.max(.2, Math.min(5, previous * (action === 'in' ? 1.2 : 1 / 1.2)))
  zoom(diagram, next)
}
function close() { disarm(); viewer.value?.close(); svgHtml.value = ''; opener?.focus() }
</script>

<template>
  <div class="diagram-interactions" @click.capture="interact" @mousedown.capture="arm">
    <slot />
    <span v-if="wheelActive" class="wheel-zoom-hint" role="status">滚轮缩放中 · 移动鼠标退出</span>
    <Teleport to="body">
      <dialog ref="viewer" class="diagram-viewer" aria-label="图表大图查看" @cancel.prevent="close" @mousedown.capture="arm">
        <header><strong>图表查看</strong><div class="diagram-controls">
          <button type="button" @click="scale = Math.max(.2, scale / 1.2)"><AppIcon :icon="ZoomOut" :size="16" />缩小</button>
          <output>{{ Math.round(scale * 100) }}%</output>
          <button type="button" @click="scale = Math.min(5, scale * 1.2)"><AppIcon :icon="ZoomIn" :size="16" />放大</button>
          <button type="button" @click="scale = 1"><AppIcon :icon="Refresh" :size="16" />重置</button>
          <button type="button" autofocus @click="close"><AppIcon :icon="Close" :size="16" />关闭</button>
        </div></header>
        <div class="diagram-viewer-scroll"><div class="diagram-viewer-image" :style="{ width: `${baseWidth * scale}px` }" v-html="svgHtml" /></div>
      </dialog>
    </Teleport>
  </div>
</template>

<style>
.diagram-interactions { min-width: 0; }
.diagram-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.diagram-controls button { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); color: var(--color-text-primary); background: var(--color-surface-primary); cursor: pointer; font: inherit; font-size: 12px; }
.diagram-controls button:hover { border-color: var(--color-accent-primary); }
.diagram-controls button:focus-visible { outline: 2px solid var(--color-accent-primary); }
.diagram-viewer { width: min(1200px, 94vw); max-width: 94vw; height: 85vh; padding: 16px; color: var(--color-text-primary); background: var(--color-background-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
.diagram-viewer::backdrop { background: #0008; }
.diagram-viewer header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.diagram-viewer-scroll { height: calc(100% - 64px); overflow: auto; }
.diagram-viewer-image { margin: auto; transition: width 180ms ease-out; }
.diagram-viewer-image svg { width: 100% !important; max-width: none !important; height: auto !important; }
</style>

<style>
.editor-mermaid-preview > svg, .markdown-mermaid > svg { transition: width 180ms ease-out; }
[data-wheel-zoom="true"] { outline: 2px solid var(--color-accent-primary); outline-offset: -2px; cursor: zoom-in; }
.wheel-zoom-hint { position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%); z-index: 2000; padding: 8px 14px; border-radius: var(--radius-md); background: var(--color-surface-elevated); color: var(--color-text-primary); border: 1px solid var(--color-border-default); pointer-events: none; }
@media (prefers-reduced-motion: reduce) { .editor-mermaid-preview > svg, .markdown-mermaid > svg, .diagram-viewer-image { transition: none; } }
</style>
