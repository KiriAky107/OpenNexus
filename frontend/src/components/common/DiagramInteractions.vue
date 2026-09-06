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
let lastWheel = 0
const zoomBases = new WeakMap<HTMLElement, number>()
let anchorFrame = 0
let anchorUntil = 0
function stopAnchoring() { cancelAnimationFrame(anchorFrame); anchorFrame = 0 }
function anchorZoom(svg: SVGSVGElement, event: WheelEvent) {
  stopAnchoring()
  const rect = svg.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
  const screenX = rect.left + x * rect.width
  const screenY = rect.top + y * rect.height
  const scrollers: HTMLElement[] = []
  for (let node = svg.parentElement; node; node = node.parentElement) {
    const style = getComputedStyle(node)
    if (/(auto|scroll)/.test(`${style.overflowX} ${style.overflowY}`)) scrollers.push(node)
    if (node === viewer.value) break
  }
  anchorUntil = performance.now() + 240
  const follow = () => {
    if (!svg.isConnected) return
    // Inner horizontal overflow and the editor's outer vertical scroll may differ.
    // Re-measure after each scroll, letting the outer container take the remainder.
    for (const node of scrollers) {
      const current = svg.getBoundingClientRect()
      node.scrollLeft += current.left + x * current.width - screenX
      node.scrollTop += current.top + y * current.height - screenY
    }
    if (performance.now() < anchorUntil) anchorFrame = requestAnimationFrame(follow)
  }
  anchorFrame = requestAnimationFrame(follow)
}
function wheelFactor(event: WheelEvent) {
  const now = performance.now()
  const elapsed = lastWheel ? Math.min(100, Math.max(0, now - lastWheel)) : 80
  lastWheel = now
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1)
  return Math.exp(-Math.sign(delta) * Math.min(Math.abs(delta) * .0005, elapsed * .0005))
}
function viewerWheel(event: WheelEvent) {
  event.preventDefault(); event.stopPropagation()
  const svg = viewer.value?.querySelector<SVGSVGElement>('.diagram-viewer-image svg')
  if (svg) anchorZoom(svg, event)
  scale.value = Math.max(.2, Math.min(5, scale.value * wheelFactor(event)))
}
function disarm() {
  stopAnchoring(); lastWheel = 0
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
  if (!target || target.classList.contains('diagram-viewer-image')) return
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
  const svg = wheelTarget.querySelector<SVGSVGElement>('svg')
  if (svg) anchorZoom(svg, event)
  const factor = wheelFactor(event)
  if (wheelTarget.classList.contains('diagram-viewer-image')) scale.value = Math.max(.2, Math.min(5, scale.value * factor))
  else zoom(wheelTarget, Math.max(.2, Math.min(5, Number(wheelTarget.dataset.diagramScale || 1) * factor)))
}
function zoom(diagram: HTMLElement, next: number) {
  const svg = diagram.querySelector<SVGSVGElement>('svg')
  if (!svg) return
  if (!zoomBases.has(diagram)) zoomBases.set(diagram, svg.getBoundingClientRect().width || widthOf(svg))
  diagram.dataset.diagramScale = String(next)
  svg.style.width = next === 1 ? '' : `${zoomBases.get(diagram)! * next}px`
  svg.style.maxWidth = next === 1 ? '' : 'none'
  svg.style.height = 'auto'
  if (next === 1) zoomBases.delete(diagram)
}
onBeforeUnmount(disarm)
function widthOf(svg: SVGSVGElement) {
  return svg.viewBox?.baseVal?.width || Number(svg.getAttribute('viewBox')?.split(/[ ,]+/)[2]) || svg.getBoundingClientRect().width || 800
}
async function interact(event: MouseEvent) {
  if (!(event.target instanceof Element)) return
  const codeButton = event.target.closest<HTMLButtonElement>('[data-code-action]')
  if (codeButton) {
    const block = codeButton.closest<HTMLElement>('.markdown-code-block, .markdown-mermaid')
    const source = block?.querySelector<HTMLElement>('.markdown-code-source')
    if (!block || !source) return
    event.preventDefault(); event.stopPropagation()
    if (codeButton.dataset.codeAction === 'copy') {
      try { await navigator.clipboard.writeText(source.textContent ?? ''); codeButton.textContent = '已复制' }
      catch { codeButton.textContent = '复制失败，请选择源码复制' }
    } else {
      disarm()
      source.hidden = !source.hidden
      const svg = block.querySelector<SVGSVGElement>(':scope > svg')
      if (svg) svg.style.display = source.hidden ? '' : 'none'
      block.dataset.sourceView = String(!source.hidden)
      codeButton.setAttribute('aria-pressed', String(!source.hidden))
      codeButton.textContent = source.hidden ? '查看源码' : '查看预览'
    }
    return
  }
  const button = event.target.closest<HTMLElement>('[data-diagram-action]')
  const diagram = button?.closest<HTMLElement>('.editor-mermaid-preview, .markdown-mermaid')
  const svg = diagram?.querySelector<SVGSVGElement>('svg')
  if (!button || !diagram || !svg) return
  event.preventDefault()
  event.stopPropagation()
  const action = button.dataset.diagramAction
  if (action === 'view') {
    disarm()
    opener = button
    const intrinsicWidth = widthOf(svg)
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
    const viewport = viewer.value?.querySelector<HTMLElement>('.diagram-viewer-scroll')
    const box = svg.getAttribute('viewBox')?.trim().split(/[ ,]+/).map(Number)
    const intrinsicHeight = box?.length === 4 && box[3]! > 0 ? box[3]! : svg.getBoundingClientRect().height
    // Opening is independent of the inline preview's zoom and any previous modal scroll.
    // Keep native size for small diagrams; fit wide/tall diagrams completely at 100%.
    baseWidth.value = Math.min(intrinsicWidth, viewport?.clientWidth || intrinsicWidth,
      intrinsicHeight > 0 && viewport?.clientHeight ? viewport.clientHeight * intrinsicWidth / intrinsicHeight : intrinsicWidth)
    await nextTick()
    if (viewport) { viewport.scrollLeft = 0; viewport.scrollTop = 0 }
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
        <div class="diagram-viewer-scroll" @wheel="viewerWheel"><div class="diagram-viewer-image" :style="{ width: `${baseWidth * scale}px` }" v-html="svgHtml" /></div>
      </dialog>
    </Teleport>
  </div>
</template>

<style>
.diagram-interactions { min-width: 0; }
.markdown-code-toolbar { display: flex; align-items: center; gap: var(--space-sm); padding: var(--space-sm); color: var(--color-code-muted); font: 12px/1.4 var(--font-editor-mono); }
.markdown-code-toolbar > span { margin-right: auto; }
.markdown-code-toolbar button { font: inherit; }
.markdown-code-source { text-align: left; white-space: pre; overflow: auto; padding: var(--space-md); background: var(--color-code-background); color: var(--color-code-text); font-family: var(--font-editor-mono); }
.markdown-code-source[hidden] { display: none !important; }
.markdown-mermaid[data-source-view='true'] > .diagram-controls { display: none; }
.diagram-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.diagram-controls button { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); color: var(--color-text-primary); background: var(--color-surface-primary); cursor: pointer; font: inherit; font-size: 12px; }
.diagram-controls button:hover { border-color: var(--color-accent-primary); }
.diagram-controls button:focus-visible { outline: 2px solid var(--color-accent-primary); }
.diagram-viewer { margin: auto; width: min(1200px, 94vw); max-width: 94vw; height: 85vh; padding: 16px; color: var(--color-text-primary); background: var(--color-background-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
.diagram-viewer[open] { display: flex; flex-direction: column; gap: var(--space-md); overflow: hidden; }
.diagram-viewer::backdrop { background: var(--color-background-overlay); }
.diagram-viewer header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-sm); flex-shrink: 0; }
.diagram-viewer header .diagram-controls { flex-wrap: wrap; }
.diagram-viewer-scroll { display: flex; flex: 1; min-height: 0; overflow: auto; }
/* Auto margins center small diagrams and become zero on overflow, keeping all edges reachable. */
.diagram-viewer-image { flex: 0 0 auto; margin: auto; transition: width 180ms ease-out; }
.diagram-viewer-image svg { display: block; width: 100% !important; max-width: none !important; height: auto !important; }
</style>

<style>
.editor-mermaid-preview > svg, .markdown-mermaid > svg { transition: width 180ms ease-out; }
[data-wheel-zoom="true"] { outline: 2px solid var(--color-accent-primary); outline-offset: -2px; cursor: zoom-in; }
.wheel-zoom-hint { position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%); z-index: 2000; padding: 8px 14px; border-radius: var(--radius-md); background: var(--color-surface-elevated); color: var(--color-text-primary); border: 1px solid var(--color-border-default); pointer-events: none; }
@media (prefers-reduced-motion: reduce) { .editor-mermaid-preview > svg, .markdown-mermaid > svg, .diagram-viewer-image { transition: none; } }
</style>

<style>
:is(.editor-mermaid-preview, .markdown-mermaid) > .diagram-controls { opacity: 0; pointer-events: none; transition: opacity 160ms ease; }
:is(.editor-mermaid-preview, .markdown-mermaid):is(:hover, :focus-within) > .diagram-controls { opacity: 1; pointer-events: auto; }
@media (hover: none) { :is(.editor-mermaid-preview, .markdown-mermaid) > .diagram-controls { opacity: 1; pointer-events: auto; } }
</style>

<style>
/* Keep 10px axis labels readable on narrow screens; the existing container scrolls. */
.function-plot-preview > svg, .markdown-function-plot > svg { min-width: 640px; }
</style>
