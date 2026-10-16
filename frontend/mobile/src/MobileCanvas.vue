<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CanvasModel, nodeBounds, type CanvasNode, type CanvasEdge } from '../../src/features/editor/canvasModel'
import { CanvasGeometry, intersects } from '../../src/features/editor/canvasGeometry'
import { resolveCanvasFile } from '../../src/services/workspaceDocuments'
import { CanvasGestures, type CanvasGestureTarget } from './canvasGestures'
import { canvasText } from './canvasLocale'
import { canvasImagePath, createCanvasImages } from './canvasImages'
import type { Language } from './locale'

const props = defineProps<{ modelValue: string; language: Language; readOnly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [source: string]; error: [code: string]; compositionStart: []; compositionEnd: []; link: [href: string]; image: []; validation: [pending: boolean] }>()
const host = ref<HTMLElement>(), revision = ref(0), selected = ref<string[]>([]), edgeId = ref('')
const multi = ref(false), connecting = ref(false), connectFrom = ref(''), panel = ref(false), localError = ref('')
const addType = ref<'file' | 'link' | null>(null), addValue = ref(''), clipboard = ref('')
const repairOpen = ref(false), repairValue = ref(props.modelValue), invalid = ref(false)
const copy = computed(() => canvasText(props.language))
const rawFields = ref<Record<string, string>>(Object.create(null)), invalidFields = ref(new Set<string>())
const feedback = computed(() => localError.value || invalidFields.value.size ? copy.value.invalidValue : '')
let model: CanvasModel | undefined, gestures: CanvasGestures | undefined, geometry: CanvasGeometry | undefined
const composing = ref(false), toolsMenu = ref<HTMLDetailsElement>()
let batch = '', disposed = false, resizeObserver: ResizeObserver | undefined
const dimensions = ref({ width: 390, height: 500 })
const imageCache = createCanvasImages()
function notify() { if (model) { geometry = new CanvasGeometry(model.document); revision.value++; emit('update:modelValue', model.source) } }
function initialize(source: string) {
  try { model = new CanvasModel(source); gestures = new CanvasGestures(model, notify); geometry = new CanvasGeometry(model.document); invalid.value = false; revision.value++ }
  catch { invalid.value = true; model = undefined; gestures = undefined; geometry = undefined }
}
initialize(props.modelValue)
const nodes = computed(() => { revision.value; return model?.document.nodes ?? [] })
const activeNode = computed(() => selected.value.length === 1 ? nodes.value.find(node => node.id === selected.value[0]) : undefined)
const activeEdge = computed(() => { revision.value; return model?.document.edges.find(edge => edge.id === edgeId.value) })
watch(() => `${selected.value.length === 1 ? selected.value[0] : ''}:${edgeId.value}`, () => { rawFields.value = Object.create(null); invalidFields.value.clear(); batch = crypto.randomUUID() })
watch(() => invalidFields.value.size > 0, pending => emit('validation', pending), { immediate: true })
function fieldKey(key: string, edge = false) { return JSON.stringify([edge ? 'edge' : 'node', edge ? activeEdge.value?.id : activeNode.value?.id, key]) }
function fieldValue(key: string, edge = false) { return rawFields.value[fieldKey(key, edge)] ?? (edge ? activeEdge.value?.[key] : activeNode.value?.[key]) ?? '' }
function discardFields() { rawFields.value = Object.create(null); invalidFields.value.clear(); localError.value = '' }
const view = computed(() => { revision.value; return gestures?.viewport ?? { x: 0, y: 0, zoom: 1 } })
const bounds = computed(() => ({ left: -view.value.x / view.value.zoom - 80, top: -view.value.y / view.value.zoom - 80,
  right: (dimensions.value.width - view.value.x) / view.value.zoom + 80, bottom: (dimensions.value.height - view.value.y) / view.value.zoom + 80 }))
const visibleNodes = computed(() => { revision.value; return nodes.value.map(node => gestures?.preview.get(node.id) ?? node)
  .filter(node => intersects({ left: node.x, right: node.x + node.width, top: node.y, bottom: node.y + node.height }, bounds.value)) })
watch(() => visibleNodes.value.flatMap(node => [node.type === 'file' ? node.file ?? '' : '', node.background ?? '']).filter(value => canvasImagePath(value)).sort().join('\n'), value => imageCache.bind(value ? value.split('\n') : []), { immediate: true })
const image = (path: unknown) => { const resolved = canvasImagePath(path); return resolved ? imageCache.items[resolved] : undefined }
const imageError = (path: string) => { const resolved = canvasImagePath(path); if (resolved) imageCache.failed(resolved) }
const edges = computed(() => { revision.value; return geometry?.project(gestures?.preview ?? new Map()).filter(item => item.geometry && intersects(item.geometry.bounds, bounds.value)) ?? [] })
const hasSelection = computed(() => Boolean(selected.value.length || activeEdge.value))
const palette: Record<string, string> = { '1': '#ffdfdd', '2': '#ffe8cd', '3': '#fff6c7', '4': '#dcf0dc', '5': '#dce9ff', '6': '#ebdefa' }
const color = (value?: string) => value ? palette[value] ?? value : '#ffffff'
const title = (node: CanvasNode) => node.type === 'text' ? node.text ?? '' : node.type === 'file' ? node.file ?? '' : node.type === 'link' ? node.url ?? '' : node.label ?? ''
function guarded(action: () => boolean | unknown, fieldInput = false) {
  if (!model || props.readOnly || composing.value || disposed || (!fieldInput && invalidFields.value.size)) return false
  localError.value = ''
  try { const before = model.source; action(); if (model.source !== before) { if (!fieldInput) rawFields.value = Object.create(null); notify() }; if (toolsMenu.value) toolsMenu.value.open = false; return true }
  catch { localError.value = copy.value.invalidValue; return false }
}
function point(event: PointerEvent) { const box = host.value!.getBoundingClientRect(); return { x: event.clientX - box.left, y: event.clientY - box.top } }
function down(event: PointerEvent) {
  if (!gestures || !host.value || event.button > 0 || composing.value || addType.value) return
  if (gestures.pointers.size) {
    if (gestures.down(event.pointerId, point(event), { kind: 'pan' })) { event.preventDefault(); host.value.setPointerCapture?.(event.pointerId); revision.value++ }
    return
  }
  const target = event.target as Element, nodeId = target.closest<HTMLElement>('[data-node]')?.dataset.node
  const edge = target.closest<SVGElement>('[data-edge]')?.dataset.edge
  if (invalidFields.value.size && (edge !== edgeId.value || nodeId !== activeNode.value?.id)) return
  let action: CanvasGestureTarget = { kind: 'pan' }
  if (!props.readOnly && edge) { selected.value = []; edgeId.value = edge; panel.value = false; return }
  if (!props.readOnly && nodeId && nodes.value.some(node => node.id === nodeId)) {
    if (connecting.value) {
      if (!connectFrom.value) { connectFrom.value = nodeId; selected.value = [nodeId] }
      else { guarded(() => model!.connect(connectFrom.value, nodeId)); connectFrom.value = ''; connecting.value = false }
      return
    }
    if (multi.value) { selected.value = selected.value.includes(nodeId) ? selected.value.filter(id => id !== nodeId) : [...selected.value, nodeId]; edgeId.value = ''; return }
    if (!selected.value.includes(nodeId)) { selected.value = [nodeId]; batch = crypto.randomUUID() }
    edgeId.value = ''
    action = target.closest('[data-resize]') ? { kind: 'resize', id: nodeId } : { kind: 'move', ids: [...selected.value] }
  } else if (!nodeId) { selected.value = []; edgeId.value = ''; panel.value = false }
  if (gestures.down(event.pointerId, point(event), action)) { event.preventDefault(); host.value.setPointerCapture?.(event.pointerId); revision.value++ }
}
function move(event: PointerEvent) { if (!gestures?.pointers.has(event.pointerId)) return; event.preventDefault(); gestures.move(event.pointerId, point(event)); revision.value++ }
function up(event: PointerEvent) {
  if (!gestures?.pointers.has(event.pointerId)) return
  try { if (gestures.up(event.pointerId)) rawFields.value = Object.create(null) } catch { localError.value = copy.value.invalidValue }
  host.value?.releasePointerCapture?.(event.pointerId); revision.value++
}
function cancel() { gestures?.cancel(); revision.value++ }
function lost(event: PointerEvent) { if (gestures?.pointers.has(event.pointerId)) cancel() }
function fit() {
  if (!gestures) return
  cancel(); const box = nodeBounds(nodes.value), width = dimensions.value.width, height = dimensions.value.height
  const zoom = Math.max(0.15, Math.min(1.5, (width - 48) / Math.max(1, box.width), (height - 48) / Math.max(1, box.height)))
  gestures.viewport = { zoom, x: (width - box.width * zoom) / 2 - box.x * zoom, y: (height - box.height * zoom) / 2 - box.y * zoom }; revision.value++
}
function add(type: CanvasNode['type'], value = '') {
  if (!gestures) return
  const position = gestures.world({ x: dimensions.value.width / 2 - 100, y: dimensions.value.height / 2 - 60 })
  return guarded(() => { const id = model!.add(type, position.x, position.y, value); selected.value = [id]; edgeId.value = ''; panel.value = true; batch = crypto.randomUUID() })
}
function addReference(type: 'file' | 'link') { cancel(); addType.value = type; addValue.value = ''; localError.value = ''; if (toolsMenu.value) toolsMenu.value.open = false }
function confirmReference() { const type = addType.value; if (type && add(type, addValue.value)) addType.value = null }
function property() { cancel(); if (invalidFields.value.size) return; panel.value = !panel.value; batch = crypto.randomUUID() }
function keyboardSelect(id: string) {
  if (composing.value || invalidFields.value.size) return
  cancel(); selected.value = [id]; edgeId.value = ''; panel.value = true; batch = crypto.randomUUID()
}
function nodeField(key: keyof CanvasNode, value: unknown) { const node = activeNode.value; return node ? guarded(() => model!.updateNode(node.id, { [key]: value }, batch), true) : false }
function edgeField(key: keyof CanvasEdge, value: unknown) { const edge = activeEdge.value; return edge ? guarded(() => model!.updateEdge(edge.id, { [key]: value }, batch), true) : false }
function field(event: Event, key: string, edge = false, numeric = false) {
  const raw = (event.target as HTMLInputElement).value, owner = fieldKey(key, edge)
  rawFields.value[owner] = raw
  if (composing.value) return
  const value = numeric ? Number(raw) : raw || undefined
  if (numeric && (!raw || !Number.isFinite(value))) { invalidFields.value.add(owner); return }
  const accepted = edge ? edgeField(key, value) : nodeField(key, key === 'text' || key === 'label' ? raw : value)
  if (accepted) invalidFields.value.delete(owner); else invalidFields.value.add(owner)
}
function startComposition() { composing.value = true; emit('compositionStart') }
function finishComposition() { composing.value = false; emit('compositionEnd') }
function endComposition(event: CompositionEvent, key: string, edge = false) { composing.value = false; field(event, key, edge); emit('compositionEnd') }
function remove() { guarded(() => { model!.remove(selected.value, edgeId.value || undefined); selected.value = []; edgeId.value = ''; panel.value = false }) }
function duplicate() { guarded(() => { selected.value = model!.paste(model!.copy(selected.value)); edgeId.value = '' }) }
function copySelection() { if (model && selected.value.length) clipboard.value = model.copy(selected.value); if (toolsMenu.value) toolsMenu.value.open = false }
function paste() { if (clipboard.value) guarded(() => { selected.value = model!.paste(clipboard.value); edgeId.value = '' }) }
function undo() { if (invalidFields.value.size) { discardFields(); return }; guarded(() => { model!.undo(); discardFields(); selected.value = selected.value.filter(id => model!.document.nodes.some(node => node.id === id)); if (!model!.document.edges.some(edge => edge.id === edgeId.value)) edgeId.value = ''; panel.value = false }) }
function redo() { guarded(() => model!.redo()) }
function openReference() {
  const node = activeNode.value
  if (node?.type === 'link') emit('link', node.url!)
  else if (node?.type === 'file' && resolveCanvasFile(node.file)) emit('link', node.file!)
}
function applyRepair() { if (props.readOnly || composing.value) return; initialize(repairValue.value); if (model) { emit('update:modelValue', model.source); repairOpen.value = false } }
function visibility() { if (document.visibilityState === 'hidden') cancel() }
watch(() => props.readOnly, value => { if (value) cancel() })
watch(() => props.modelValue, source => { if (model && source !== model.source) { cancel(); emit('error', 'CANVAS_EDITOR_CONTENT_CHANGED') } })
onMounted(() => {
  document.addEventListener('visibilitychange', visibility)
  if (typeof ResizeObserver !== 'undefined') { resizeObserver = new ResizeObserver(entries => { const box = entries[0]?.contentRect; if (box) dimensions.value = { width: box.width, height: box.height }; cancel() }); if (host.value) resizeObserver.observe(host.value) }
  void nextTick()
})
onBeforeUnmount(() => { disposed = true; cancel(); imageCache.dispose(); resizeObserver?.disconnect(); document.removeEventListener('visibilitychange', visibility) })
defineExpose({ undo, redo, cancel, source: () => model?.source ?? props.modelValue,
  imagePosition() { if (!gestures || composing.value || props.readOnly || invalidFields.value.size) return null; cancel(); const point = gestures.world({ x: dimensions.value.width / 2 - 100, y: dimensions.value.height / 2 - 80 }); return { x: Math.round(point.x), y: Math.round(point.y) } },
  acceptImage(before: string, after: string) { if (!model || disposed || composing.value || model.source !== before) return false; try { cancel(); model.acceptSource(after); notify(); return true } catch { return false } },
})
</script>

<template>
  <section class="canvas-editor" :aria-label="copy.canvas">
    <template v-if="invalid">
      <p role="alert">{{ copy.invalid }}</p><button class="quiet" @click="repairOpen = !repairOpen">{{ copy.repair }}</button>
      <div v-if="repairOpen" class="repair"><textarea v-model="repairValue" :aria-label="copy.repairLabel" :disabled="readOnly" @compositionstart="startComposition" @compositionend="composing = false; emit('compositionEnd')" /><button :disabled="readOnly || composing" @click="applyRepair">{{ copy.repairApply }}</button></div>
    </template>
    <template v-else>
      <nav class="canvas-toolbar" :aria-label="copy.canvas">
        <button class="quiet" :disabled="readOnly || composing" @click="add('text', copy.textName)">{{ copy.text }}</button>
        <details ref="toolsMenu"><summary>{{ copy.add }}</summary><div class="canvas-menu">
          <button class="quiet" :disabled="readOnly || composing" @click="addReference('file')">{{ copy.file }}</button><button class="quiet" :disabled="readOnly || composing" @click="addReference('link')">{{ copy.link }}</button><button class="quiet" :disabled="readOnly || composing" @click="add('group', copy.groupName)">{{ copy.group }}</button>
          <button class="quiet" :disabled="readOnly || composing" @click="cancel(); emit('image')">{{ copy.importImage }}</button>
          <button class="quiet" :disabled="readOnly || composing || !selected.length" @click="copySelection">{{ copy.copy }}</button><button class="quiet" :disabled="readOnly || composing || !clipboard" @click="paste">{{ copy.paste }}</button><button class="quiet" :disabled="readOnly || composing || !selected.length" @click="duplicate">{{ copy.duplicate }}</button>
          <button class="quiet" :disabled="readOnly || composing || !hasSelection" @click="remove">{{ copy.remove }}</button>
        </div></details>
        <button class="quiet" :disabled="readOnly || composing" :aria-pressed="multi" @click="cancel(); multi = !multi; connecting = false">{{ copy.select }}</button>
        <button class="quiet" :disabled="readOnly || composing" :aria-pressed="connecting" @click="cancel(); connecting = !connecting; connectFrom = ''; multi = false">{{ copy.connect }}</button>
        <button class="quiet" :disabled="!hasSelection || composing" @click="property">{{ copy.properties }}</button><button class="quiet" @click="fit">{{ copy.fit }}</button>
      </nav>
      <p class="canvas-hint" role="status">{{ connecting ? copy.connectHelp : copy.hint }} <span v-if="hasSelection">{{ copy.selected }}: {{ selected.length || 1 }}</span></p>
      <p v-if="feedback" role="alert">{{ feedback }} <button v-if="invalidFields.size" class="quiet" @click="discardFields">{{ copy.discardFields }}</button></p>
      <div ref="host" class="canvas-viewport" :aria-label="copy.canvas" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="cancel" @lostpointercapture="lost">
        <p v-if="!nodes.length" class="canvas-empty">{{ copy.empty }}</p>
        <div class="canvas-world" :style="{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }">
          <svg class="canvas-connections" aria-hidden="true"><defs><marker id="mobile-canvas-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#42675f" /></marker></defs>
            <g v-for="item in edges" :key="item.edge.id"><path :d="item.geometry!.path" class="edge" :class="{ chosen: edgeId === item.edge.id }" :stroke="color(item.edge.color) === '#ffffff' ? '#66847d' : color(item.edge.color)" :marker-start="item.edge.fromEnd === 'arrow' ? 'url(#mobile-canvas-arrow)' : undefined" :marker-end="item.edge.toEnd !== 'none' ? 'url(#mobile-canvas-arrow)' : undefined" />
              <path :d="item.geometry!.path" class="edge-hit" :data-edge="item.edge.id" :style="{ strokeWidth: `${18 / view.zoom}px` }" /><text v-if="item.edge.label" :x="item.geometry!.x" :y="item.geometry!.y - 10">{{ item.edge.label }}</text></g>
          </svg>
          <div v-for="node in visibleNodes" :key="node.id" role="button" tabindex="0" :aria-label="title(node) || copy[node.type === 'file' ? 'file' : node.type]" :aria-pressed="selected.includes(node.id)" :data-node="node.id" class="canvas-node" :class="{ group: node.type === 'group', chosen: selected.includes(node.id) }"
            :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${node.width}px`, height: `${node.height}px`, backgroundColor: node.type === 'group' ? `${color(node.color)}aa` : color(node.color), backgroundImage: image(node.background)?.url ? `url(${image(node.background)!.url})` : undefined, backgroundSize: node.backgroundStyle === 'repeat' ? 'auto' : node.backgroundStyle === 'ratio' ? 'contain' : 'cover', backgroundRepeat: node.backgroundStyle === 'repeat' ? 'repeat' : 'no-repeat' }" @keydown.enter.prevent="keyboardSelect(node.id)" @keydown.space.prevent="keyboardSelect(node.id)">
            <strong v-if="node.label && node.type !== 'group'">{{ node.label }}</strong>
            <template v-if="node.type === 'file' && canvasImagePath(node.file)"><img v-if="image(node.file)?.url" :src="image(node.file)!.url" :alt="node.label || node.file" decoding="async" draggable="false" @error="imageError(node.file!)" /><p v-else>{{ image(node.file)?.error === 'limit' ? copy.imageLimit : copy.imageUnavailable }}</p></template>
            <pre v-else>{{ title(node) }}</pre>
            <span v-if="selected.length === 1 && selected[0] === node.id && !readOnly" data-resize="true" class="resize-handle" role="button" :aria-label="copy.resize" />
          </div>
        </div>
      </div>
      <aside v-if="panel && (activeNode || activeEdge)" class="canvas-properties" :aria-label="copy.properties">
        <button class="quiet close-panel" :disabled="composing || Boolean(invalidFields.size)" @click="panel = false">{{ copy.close }}</button>
        <template v-if="activeNode">
          <label v-if="activeNode.type === 'text'">{{ copy.textValue }}<textarea :value="fieldValue('text')" :readonly="readOnly" :lang="language" @input="field($event, 'text')" @compositionstart="startComposition" @compositionend="endComposition($event, 'text')" /></label>
          <label v-if="activeNode.type === 'file'">{{ copy.fileValue }}<input :value="fieldValue('file')" :readonly="readOnly" @input="field($event, 'file')" @compositionstart="startComposition" @compositionend="endComposition($event, 'file')" /></label>
          <label v-if="activeNode.type === 'link'">{{ copy.urlValue }}<input :value="fieldValue('url')" :readonly="readOnly" inputmode="url" @input="field($event, 'url')" @compositionstart="startComposition" @compositionend="endComposition($event, 'url')" /></label>
          <label>{{ copy.label }}<input :value="fieldValue('label')" :readonly="readOnly" @input="field($event, 'label')" @compositionstart="startComposition" @compositionend="endComposition($event, 'label')" /></label>
          <label v-if="activeNode.type === 'group'">{{ copy.background }}<input :value="fieldValue('background')" :readonly="readOnly" @input="field($event, 'background')" @compositionstart="startComposition" @compositionend="endComposition($event, 'background')" /></label>
          <div class="numeric-fields"><label v-for="key in (['x', 'y', 'width', 'height'] as const)" :key="key">{{ copy[key] }}<input type="number" :value="fieldValue(key)" :readonly="readOnly" @input="field($event, key, false, true)" /></label></div>
          <button v-if="activeNode.type === 'file' || activeNode.type === 'link'" class="quiet" :disabled="composing" @click="openReference">{{ copy.openReference }}</button>
        </template>
        <template v-if="activeEdge">
          <label>{{ copy.label }}<input :value="fieldValue('label', true)" :readonly="readOnly" @input="field($event, 'label', true)" @compositionstart="startComposition" @compositionend="endComposition($event, 'label', true)" /></label>
          <label v-for="key in (['fromNode', 'toNode'] as const)" :key="key">{{ key === 'fromNode' ? copy.from : copy.to }}<select :value="fieldValue(key, true)" :disabled="readOnly" @change="field($event, key, true)"><option v-for="node in nodes" :key="node.id" :value="node.id">{{ title(node).slice(0, 80) || node.id }}</option></select></label>
          <div class="numeric-fields"><label v-for="key in (['fromSide', 'toSide'] as const)" :key="key">{{ copy[key] }}<select :value="fieldValue(key, true)" :disabled="readOnly" @change="field($event, key, true)"><option value="">{{ copy.auto }}</option><option v-for="side in (['left','right','top','bottom'] as const)" :key="side" :value="side">{{ copy[side] }}</option></select></label></div>
          <div class="numeric-fields"><label v-for="key in (['fromEnd', 'toEnd'] as const)" :key="key">{{ copy[key] }}<select :value="fieldValue(key, true)" :disabled="readOnly" @change="field($event, key, true)"><option value="">{{ copy.auto }}</option><option value="none">{{ copy.none }}</option><option value="arrow">{{ copy.arrow }}</option></select></label></div>
        </template>
        <label>{{ copy.color }}<select :value="fieldValue('color', Boolean(activeEdge))" :disabled="readOnly" @change="field($event, 'color', Boolean(activeEdge))"><option value="">{{ copy.noColor }}</option><option v-for="value in ['1','2','3','4','5','6']" :key="value" :value="value">{{ value }}</option><option v-if="(activeNode?.color ?? activeEdge?.color)?.startsWith('#')" :value="activeNode?.color ?? activeEdge?.color">{{ activeNode?.color ?? activeEdge?.color }}</option></select></label>
      </aside>
      <div v-if="addType" class="canvas-dialog" role="dialog" aria-modal="true" :aria-label="addType === 'file' ? copy.file : copy.link"><form @submit.prevent="confirmReference"><label>{{ addType === 'file' ? copy.fileValue : copy.urlValue }}<input v-model="addValue" autofocus :disabled="readOnly" @compositionstart="startComposition" @compositionend="finishComposition" /></label><p v-if="addType === 'file'">{{ copy.fileHelp }}</p><p v-if="localError" role="alert">{{ feedback }}</p><div><button type="button" class="quiet" :disabled="composing" @click="addType = null">{{ copy.cancel }}</button><button :disabled="readOnly || composing || !addValue">{{ copy.confirm }}</button></div></form></div>
    </template>
  </section>
</template>

<style scoped>
.canvas-editor{position:relative;display:flex;flex:1;min-height:260px;flex-direction:column;overflow:hidden;background:#f4f7f3}.canvas-toolbar{display:flex;align-items:center;gap:4px;padding:6px;flex-wrap:wrap;border-bottom:1px solid #d6dfd8}.canvas-toolbar button,.canvas-toolbar summary{font-size:13px;min-height:40px;padding:6px 9px}.canvas-toolbar details{position:relative}.canvas-menu{position:absolute;z-index:8;top:100%;left:0;display:flex;flex-direction:column;min-width:180px;background:white;border:1px solid #bccdc2;border-radius:8px;padding:6px;box-shadow:0 4px 16px #143b2022}.canvas-hint{font-size:12px;padding:4px 10px;margin:0;color:#527064}.canvas-viewport{position:relative;flex:1;min-height:200px;overflow:hidden;touch-action:none;background-image:radial-gradient(#cdd9d1 1px,transparent 1px);background-size:20px 20px}.canvas-world{position:absolute;left:0;top:0;width:1px;height:1px;transform-origin:0 0}.canvas-node{position:absolute;box-sizing:border-box;padding:12px;overflow:hidden;border:1px solid #a8bab1;border-radius:8px;user-select:none;touch-action:none;cursor:grab}.canvas-node img{display:block;width:100%;height:100%;object-fit:contain;pointer-events:none}.canvas-node.chosen{outline:3px solid #326d5c;outline-offset:3px}.canvas-node.group{border:2px dashed #8eaa9c;z-index:-1}.canvas-node pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.5 system-ui}.canvas-node.group pre{font-weight:600}.canvas-connections{position:absolute;width:1px;height:1px;overflow:visible;pointer-events:none}.edge{fill:none;stroke-width:2}.edge.chosen{stroke-width:4}.edge-hit{fill:none;stroke:transparent;pointer-events:stroke}.canvas-connections text{font:14px system-ui;fill:#294d3b;text-anchor:middle;paint-order:stroke;stroke:#f4f7f3;stroke-width:4px}.resize-handle{position:absolute;right:0;bottom:0;width:30px;height:30px;border:3px solid #326d5c;border-top:0;border-left:0;border-radius:0 0 8px 0;touch-action:none}.canvas-empty{position:absolute;left:20px;right:20px;top:40%;color:#6b8172;text-align:center}.canvas-properties{position:absolute;z-index:10;left:8px;right:8px;bottom:8px;max-height:52%;overflow:auto;padding:12px;background:white;border:1px solid #b6cbbf;border-radius:12px;box-shadow:0 5px 22px #102d202e}.canvas-properties label,.canvas-dialog label{display:flex;flex-direction:column;gap:5px;font-size:13px;margin:5px 0}.canvas-properties input,.canvas-properties select,.canvas-properties textarea,.canvas-dialog input{box-sizing:border-box;width:100%;min-height:42px;font:16px system-ui;border:1px solid #baccc0;border-radius:5px;padding:7px}.canvas-properties textarea{min-height:90px;resize:vertical}.numeric-fields{display:grid;grid-template-columns:1fr 1fr;gap:8px}.close-panel{float:right}.canvas-dialog{position:absolute;inset:0;z-index:15;display:grid;place-items:center;background:#11282055;padding:12px}.canvas-dialog form{width:100%;max-width:420px;box-sizing:border-box;padding:18px;border-radius:10px;background:white}.canvas-dialog form>div{display:flex;gap:8px;justify-content:flex-end}.repair{display:flex;flex:1;flex-direction:column}.repair textarea{flex:1;min-height:200px;font:14px ui-monospace;white-space:pre}.canvas-editor>p[role=alert]{padding:6px 12px;color:#922b21;font-size:13px;margin:0}
</style>
