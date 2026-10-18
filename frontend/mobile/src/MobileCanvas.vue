<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CanvasModel, nodeBounds, type CanvasNode, type CanvasEdge } from '../../src/features/editor/canvasModel'
import { CanvasGeometry, intersects } from '../../src/features/editor/canvasGeometry'
import CanvasScene from '../../src/features/editor/CanvasScene.vue'
import CanvasInspector from '../../src/features/editor/CanvasInspector.vue'
import CanvasProperties from '../../src/features/editor/CanvasProperties.vue'
import CanvasToolbar from '../../src/features/editor/CanvasToolbar.vue'
import { resolveCanvasFile } from '../../src/services/workspaceDocuments'
import { CanvasGestures, type CanvasGestureTarget } from './canvasGestures'
import { canvasText } from './canvasLocale'
import { canvasImagePath, createCanvasImages } from './canvasImages'
import type { Language } from './locale'

const props = defineProps<{ modelValue: string; language: Language; readOnly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [source: string]; error: [code: string]; compositionStart: []; compositionEnd: []; link: [href: string]; image: []; validation: [pending: boolean] }>()
const scene = ref<InstanceType<typeof CanvasScene>>()
const host = computed(() => scene.value?.element), revision = ref(0), selected = ref<string[]>([]), edgeId = ref('')
const multi = ref(false), connecting = ref(false), connectFrom = ref(''), panel = ref(false), localError = ref('')
const addType = ref<'file' | 'link' | null>(null), addValue = ref(''), clipboard = ref('')
const repairOpen = ref(false), repairValue = ref(props.modelValue), invalid = ref(false)
const copy = computed(() => canvasText(props.language))
const rawFields = ref<Record<string, string>>(Object.create(null)), invalidFields = ref(new Set<string>())
const feedback = computed(() => localError.value || invalidFields.value.size ? copy.value.invalidValue : '')
let model: CanvasModel | undefined, gestures: CanvasGestures | undefined, geometry: CanvasGeometry | undefined
const composing = ref(false), toolsMenu = ref<InstanceType<typeof CanvasToolbar>>()
let batch = '', disposed = false, resizeObserver: ResizeObserver | undefined
const dimensions = ref({ width: 390, height: 500 })
const compactTools = computed(() => dimensions.value.width <= 640), toolbarOpen = ref(false)
const toolbarId = `canvas-tools-${crypto.randomUUID()}`
const toolbarArea = ref<HTMLElement>(), toolbarTrigger = ref<HTMLButtonElement>()
function closeToolbar(restoreFocus = false) {
  if (!toolbarOpen.value) return false
  toolsMenu.value?.closeMenu(); toolbarOpen.value = false
  if (restoreFocus) void nextTick(() => toolbarTrigger.value?.focus())
  return true
}
async function toggleToolbar() {
  if (composing.value) return
  if (closeToolbar()) return
  toolbarOpen.value = true
  await nextTick(); toolbarArea.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
}
function toolbarOutside(event: PointerEvent) {
  if (!toolbarArea.value?.contains(event.target as Node) && !toolbarTrigger.value?.contains(event.target as Node)) closeToolbar()
}
function toolbarKeys(event: KeyboardEvent) {
  if (event.key !== 'Escape' || composing.value) return
  event.preventDefault(); event.stopPropagation(); closeToolbar(true)
}
watch(compactTools, () => closeToolbar())
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
const nodePropertyValues=computed(()=>Object.fromEntries(['value','label','background','x','y','width','height','color','subpath'].map(key=>[key,fieldValue(key==='value'?activeNode.value?.type==='text'?'text':activeNode.value?.type==='file'?'file':activeNode.value?.type==='link'?'url':'label':key)])))
const edgePropertyValues=computed(()=>Object.fromEntries(['label','fromNode','toNode','fromSide','toSide','fromEnd','toEnd','color'].map(key=>[key,fieldValue(key,true)])))
const nodeFieldKey=(key:string)=>key==='value'?activeNode.value?.type==='text'?'text':activeNode.value?.type==='file'?'file':activeNode.value?.type==='link'?'url':'label':key
function propertyInput(key:string,event:Event,edge=false){field(event,edge?key:nodeFieldKey(key),edge,['x','y','width','height'].includes(key))}
function propertyEnd(key:string,event:CompositionEvent,edge=false){endComposition(event,edge?key:nodeFieldKey(key),edge)}
function locateNode(id:string){keyboardSelect(id);if(selected.value[0]!==id||composing.value||invalidFields.value.size||!gestures)return;const node=nodes.value.find(node=>node.id===id);if(node)gestures.viewport={zoom:Math.min(1.5,view.value.zoom),x:dimensions.value.width/2-(node.x+node.width/2)*Math.min(1.5,view.value.zoom),y:dimensions.value.height/2-(node.y+node.height/2)*Math.min(1.5,view.value.zoom)};revision.value++}
function discardFields() { rawFields.value = Object.create(null); invalidFields.value.clear(); localError.value = '' }
const view = computed(() => { revision.value; return gestures?.viewport ?? { x: 0, y: 0, zoom: 1 } })
const bounds = computed(() => ({ left: -view.value.x / view.value.zoom - 80, top: -view.value.y / view.value.zoom - 80,
  right: (dimensions.value.width - view.value.x) / view.value.zoom + 80, bottom: (dimensions.value.height - view.value.y) / view.value.zoom + 80 }))
const visibleNodes = computed(() => { revision.value; return nodes.value.map(node => gestures?.preview.get(node.id) ?? node)
  .filter(node => intersects({ left: node.x, right: node.x + node.width, top: node.y, bottom: node.y + node.height }, bounds.value)) })
watch(() => visibleNodes.value.flatMap(node => [node.type === 'file' ? node.file ?? '' : '', node.background ?? '']).filter(value => canvasImagePath(value)).sort().join('\n'), value => imageCache.bind(value ? value.split('\n') : []), { immediate: true })
const scenePreviews = computed(() => Object.fromEntries(nodes.value.flatMap(node => [node.file, node.background]).filter((path): path is string => typeof path === 'string').map(path => {
  const key = canvasImagePath(path), image = key ? imageCache.items[key] : undefined
  return [path, { url: image?.url, text: image?.error === 'limit' ? copy.value.imageLimit : image?.url ? undefined : copy.value.imageUnavailable }]
})))
function imageError(path: string) { const key = canvasImagePath(path); if (key) imageCache.failed(key) }
function requestOpen(node: CanvasNode) { if (composing.value || invalidFields.value.size) return; if (node.type === 'link') emit('link',node.url!); else if (node.type === 'file' && resolveCanvasFile(node.file)) emit('link',node.file!) }
const sceneOrder = computed(() => new Map(nodes.value.map((node,index) => [node.id,index])))
const sceneNodeLookup = computed(() => { revision.value; return geometry?.nodes })
function selectEdge(id: string) { if (composing.value || invalidFields.value.size || props.readOnly) return; edgeId.value = id; selected.value = [] }
const edges = computed(() => { revision.value; return geometry?.project(gestures?.preview ?? new Map()).filter(item => item.geometry && intersects(item.geometry.bounds, bounds.value)) ?? [] })
const hasSelection = computed(() => Boolean(selected.value.length || activeEdge.value))
const title = (node: CanvasNode) => node.type === 'text' ? node.text ?? '' : node.type === 'file' ? node.file ?? '' : node.type === 'link' ? node.url ?? '' : node.label ?? ''
function guarded(action: () => boolean | unknown, fieldInput = false) {
  if (!model || props.readOnly || composing.value || disposed || (!fieldInput && invalidFields.value.size)) return false
  localError.value = ''
  try { const before = model.source; action(); if (model.source !== before) { if (!fieldInput) rawFields.value = Object.create(null); notify() }; toolsMenu.value?.closeMenu(); closeToolbar(); return true }
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
  closeToolbar()
  cancel(); const box = nodeBounds(nodes.value), width = dimensions.value.width, height = dimensions.value.height
  const zoom = Math.max(0.15, Math.min(1.5, (width - 48) / Math.max(1, box.width), (height - 48) / Math.max(1, box.height)))
  gestures.viewport = { zoom, x: (width - box.width * zoom) / 2 - box.x * zoom, y: (height - box.height * zoom) / 2 - box.y * zoom }; revision.value++
}
function add(type: CanvasNode['type'], value = '') {
  if (!gestures) return
  const position = gestures.world({ x: dimensions.value.width / 2 - 100, y: dimensions.value.height / 2 - 60 })
  return guarded(() => { const id = model!.add(type, position.x, position.y, value); selected.value = [id]; edgeId.value = ''; panel.value = true; batch = crypto.randomUUID() })
}
function addReference(type: 'file' | 'link') { cancel(); addType.value = type; addValue.value = ''; localError.value = ''; toolsMenu.value?.closeMenu(); closeToolbar() }
function confirmReference() { const type = addType.value; if (type && add(type, addValue.value)) addType.value = null }
function property() { cancel(); if (invalidFields.value.size) return; closeToolbar(); panel.value = !panel.value; batch = crypto.randomUUID() }
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
function repairInput() { if (!composing.value && !props.readOnly) emit('update:modelValue', repairValue.value) }
function endComposition(event: CompositionEvent, key: string, edge = false) { composing.value = false; field(event, key, edge); emit('compositionEnd') }
function remove() { guarded(() => { model!.remove(selected.value, edgeId.value || undefined); selected.value = []; edgeId.value = ''; panel.value = false }) }
function duplicate() { guarded(() => { selected.value = model!.paste(model!.copy(selected.value)); edgeId.value = '' }) }
function copySelection() { if (model && selected.value.length) clipboard.value = model.copy(selected.value); toolsMenu.value?.closeMenu(); closeToolbar() }
function paste() { if (clipboard.value) guarded(() => { selected.value = model!.paste(clipboard.value); edgeId.value = '' }) }
function undo() { if (invalidFields.value.size) { discardFields(); return }; guarded(() => { model!.undo(); discardFields(); selected.value = selected.value.filter(id => model!.document.nodes.some(node => node.id === id)); if (!model!.document.edges.some(edge => edge.id === edgeId.value)) edgeId.value = ''; panel.value = false }) }
function redo() { guarded(() => model!.redo()) }
function openReference() {
  const node = activeNode.value
  if (node?.type === 'link') emit('link', node.url!)
  else if (node?.type === 'file' && resolveCanvasFile(node.file)) emit('link', node.file!)
}
function applyRepair() { if (props.readOnly || composing.value) return; initialize(repairValue.value); if (model) { emit('update:modelValue', model.source); repairOpen.value = false } }
function visibility() { if (document.visibilityState === 'hidden') { cancel(); closeToolbar() } }
watch(() => props.readOnly, value => { if (value) cancel() })
watch(() => props.modelValue, source => { if (model && source !== model.source) { cancel(); emit('error', 'CANVAS_EDITOR_CONTENT_CHANGED') } })
onMounted(() => {
  document.addEventListener('visibilitychange', visibility)
  window.addEventListener('pointerdown', toolbarOutside)
  if (typeof ResizeObserver !== 'undefined') { resizeObserver = new ResizeObserver(entries => { const box = entries[0]?.contentRect; if (box) dimensions.value = { width: box.width, height: box.height }; cancel() }); if (host.value) resizeObserver.observe(host.value) }
  void nextTick()
})
onBeforeUnmount(() => { disposed = true; cancel(); imageCache.dispose(); resizeObserver?.disconnect(); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('pointerdown', toolbarOutside) })
defineExpose({ undo, redo, cancel, source: () => model?.source ?? props.modelValue,
  handleBack() {
    if (composing.value || invalidFields.value.size) return true
    if (gestures?.pointers.size) { cancel(); return true }
    if (addType.value) { addType.value = null; return true }
    if (toolsMenu.value?.closeMenu()) return true
    if (closeToolbar(true)) return true
    if (repairOpen.value) { repairOpen.value = false; return true }
    if (panel.value) { panel.value = false; return true }
    if (connecting.value || multi.value) { connecting.value = false; multi.value = false; connectFrom.value = ''; return true }
    return false
  },
  imagePosition() { if (!gestures || composing.value || props.readOnly || invalidFields.value.size) return null; cancel(); const point = gestures.world({ x: dimensions.value.width / 2 - 100, y: dimensions.value.height / 2 - 80 }); return { x: Math.round(point.x), y: Math.round(point.y) } },
  acceptImage(before: string, after: string) { if (!model || disposed || composing.value || model.source !== before) return false; try { cancel(); model.acceptSource(after); notify(); return true } catch { return false } },
})
</script>

<template>
  <section class="canvas-editor" :aria-label="copy.canvas">
    <template v-if="invalid">
      <p role="alert">{{ copy.invalid }}</p><button class="quiet" @click="repairOpen = !repairOpen">{{ copy.repair }}</button>
      <div v-if="repairOpen" class="repair"><textarea v-model="repairValue" :aria-label="copy.repairLabel" :disabled="readOnly" @input="repairInput" @compositionstart="startComposition" @compositionend="finishComposition(); repairInput()" /><button :disabled="readOnly || composing" @click="applyRepair">{{ copy.repairApply }}</button></div>
    </template>
    <template v-else>
      <div v-if="compactTools" class="canvas-tools-toggle"><button ref="toolbarTrigger" class="button-secondary" :aria-label="copy.tools" :aria-expanded="toolbarOpen" :aria-controls="toolbarId" :disabled="composing" @click="toggleToolbar">{{ copy.tools }}</button></div>
      <div v-show="!compactTools || toolbarOpen" :id="toolbarId" ref="toolbarArea" class="canvas-tools-area" :class="{ 'floating-canvas-tools': compactTools }" role="region" :aria-label="copy.tools" @keydown="toolbarKeys">
      <CanvasToolbar ref="toolsMenu" :language="language" touch :read-only="Boolean(readOnly || composing)" :selection-count="selected.length" :edge-selected="Boolean(edgeId)" :can-undo="Boolean(model?.canUndo)" :can-redo="Boolean(model?.canRedo)" :node-count="nodes.length" :sidebar="panel" :show-inspector="false" :arrange-enabled="false"
        @add-text="add('text',copy.textName)" @add-file="addReference('file')" @add-link="addReference('link')" @group="add('group',copy.groupName)" @connect="guarded(()=>model!.connect(selected[0]!,selected[1]!))" @undo="undo" @redo="redo" @copy="copySelection" @paste="paste" @remove="remove">
        <template #selection><button class="button-secondary" :disabled="readOnly || composing" :aria-pressed="multi" @click="cancel();multi=!multi;connecting=false;closeToolbar()">{{ copy.select }}</button><button class="button-secondary" :disabled="readOnly || composing" :aria-pressed="connecting" @click="cancel();connecting=!connecting;connectFrom='';multi=false;closeToolbar()">{{ copy.connect }}</button><button class="button-secondary" :disabled="!hasSelection || composing" @click="property">{{ copy.properties }}</button><button class="button-secondary" @click="fit">{{ copy.fit }}</button></template>
        <template #tools><button role="menuitem" :disabled="readOnly || composing" @click="cancel();closeToolbar();emit('image')">{{ copy.importImage }}</button><button role="menuitem" :disabled="readOnly || composing || !selected.length" @click="duplicate">{{ copy.duplicate }}</button></template>
      </CanvasToolbar>
      </div>
      <p class="canvas-hint" role="status">{{ connecting ? copy.connectHelp : copy.hint }} <span v-if="hasSelection">{{ copy.selected }}: {{ selected.length || 1 }}</span></p>
      <p v-if="feedback" role="alert">{{ feedback }} <button v-if="invalidFields.size" class="quiet" @click="discardFields">{{ copy.discardFields }}</button></p>
      <CanvasScene ref="scene" :nodes="visibleNodes" :node-lookup="sceneNodeLookup" :node-count="nodes.length" :edges="edges" :order="sceneOrder" :pan="{x:view.x,y:view.y}" :zoom="view.zoom" :selected="selected" :selected-edge="edgeId" :previews="scenePreviews" :read-only="Boolean(readOnly)" :label="copy.canvas" :empty-label="copy.empty" touch
        @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="cancel" @lostpointercapture="lost" @node-pointer="down" @resize-pointer="down" @edge-pointer="down" @locate="keyboardSelect" @open="requestOpen" @select-edge="selectEdge" @image-error="imageError" />
      <CanvasInspector v-if="panel" class="canvas-properties" :nodes="nodes" :edges="model?.document.edges ?? []" :selected="selected" :blocked="composing || Boolean(invalidFields.size)" :language="language" touch @close="panel=false" @locate="locateNode" @select-edge="selectEdge">
        <template #hint>{{ copy.hint }}</template>
        <CanvasProperties :node="activeNode" :edge="activeEdge" :node-values="nodePropertyValues" :edge-values="edgePropertyValues" :selection-count="selected.length" :read-only="Boolean(readOnly)" :language="language" touch immediate @node-input="propertyInput" @edge-input="(key,event)=>propertyInput(key,event,true)" @composition-start="startComposition" @node-composition-end="propertyEnd" @edge-composition-end="(key,event)=>propertyEnd(key,event,true)">
          <template #nodeExtra><label v-if="activeNode?.type!=='group'">{{ copy.label }}<input class="input" :aria-label="copy.label" :value="fieldValue('label')" :readonly="readOnly" @input="field($event,'label')" @compositionstart="startComposition" @compositionend="endComposition($event,'label')" /></label><label v-if="activeNode?.type==='group'">{{ copy.background }}<input class="input" :aria-label="copy.background" :value="fieldValue('background')" :readonly="readOnly" @input="field($event,'background')" @compositionstart="startComposition" @compositionend="endComposition($event,'background')" /></label></template>
          <template #nodeActions><button v-if="activeNode?.type==='file'||activeNode?.type==='link'" type="button" class="button-secondary" :disabled="composing" @click="openReference">{{ copy.openReference }}</button></template>
          <template #edgeExtra><label v-for="key in (['fromNode','toNode'] as const)" :key="key">{{ key==='fromNode'?copy.from:copy.to }}<select class="select" :aria-label="key==='fromNode'?copy.from:copy.to" :value="fieldValue(key,true)" :disabled="readOnly" @change="field($event,key,true)"><option v-for="node in nodes" :key="node.id" :value="node.id">{{ title(node).slice(0,80)||node.id }}</option></select></label></template>
          <template #edgeActions><label>{{ copy.color }}<select class="select" :aria-label="copy.color" :value="fieldValue('color',true)" :disabled="readOnly" @change="field($event,'color',true)"><option value="">{{ copy.noColor }}</option><option v-for="value in ['1','2','3','4','5','6']" :key="value" :value="value">{{ value }}</option><option v-if="activeEdge?.color?.startsWith('#')" :value="activeEdge.color">{{ activeEdge.color }}</option></select></label></template>
        </CanvasProperties>
      </CanvasInspector>
      <div v-if="addType" class="canvas-dialog" role="dialog" aria-modal="true" :aria-label="addType === 'file' ? copy.file : copy.link"><form @submit.prevent="confirmReference"><label>{{ addType === 'file' ? copy.fileValue : copy.urlValue }}<input v-model="addValue" autofocus :disabled="readOnly" @compositionstart="startComposition" @compositionend="finishComposition" /></label><p v-if="addType === 'file'">{{ copy.fileHelp }}</p><p v-if="localError" role="alert">{{ feedback }}</p><div><button type="button" class="quiet" :disabled="composing" @click="addType = null">{{ copy.cancel }}</button><button :disabled="readOnly || composing || !addValue">{{ copy.confirm }}</button></div></form></div>
    </template>
  </section>
</template>

<style scoped>
.canvas-tools-toggle{display:flex;justify-content:flex-end;padding:var(--space-xs) var(--space-sm);background:var(--color-surface-primary);border-bottom:1px solid var(--color-border-default)}
.canvas-tools-toggle button{min-width:44px;min-height:44px}
.canvas-tools-area{min-width:0}
.floating-canvas-tools{position:absolute;z-index:13;top:calc(44px + 2 * var(--space-xs));left:4px;right:4px;max-width:640px;max-height:min(60%,420px);overflow:auto;border:1px solid var(--color-border-default);border-radius:var(--radius-md);background:var(--color-surface-primary);box-shadow:var(--shadow-lg)}
.floating-canvas-tools :deep(.canvas-toolbar){border-bottom:0}
.floating-canvas-tools :deep(.canvas-view-tools){width:100%;margin-left:0;justify-content:flex-end}
.floating-canvas-tools :deep(.canvas-tools-menu){width:100%;text-align:right}
.floating-canvas-tools :deep(.canvas-tools-popover){position:static;min-width:0;max-width:100%;width:100%;box-sizing:border-box;text-align:left}
.canvas-editor{position:relative;display:flex;flex:1;min-height:260px;flex-direction:column;overflow:hidden;background:var(--color-background-secondary)}.canvas-menu{position:absolute;z-index:8;top:100%;left:0;display:flex;flex-direction:column;min-width:180px;background:var(--color-surface-primary);border:1px solid var(--color-border-default);border-radius:8px;padding:6px;box-shadow:var(--shadow-md)}.canvas-hint{font-size:12px;padding:4px 10px;margin:0;color:var(--color-text-secondary)}.canvas-connections{position:absolute;width:1px;height:1px;overflow:visible;pointer-events:none}.edge{fill:none;stroke-width:2}.edge.chosen{stroke-width:4}.edge-hit{fill:none;stroke:transparent;pointer-events:stroke}.canvas-connections text{font:14px var(--font-ui-sans);fill:var(--color-text-primary);text-anchor:middle;paint-order:stroke;stroke:var(--color-background-secondary);stroke-width:4px}.resize-handle{position:absolute;right:0;bottom:0;width:30px;height:30px;border:3px solid var(--color-border-focus);border-top:0;border-left:0;border-radius:0 0 8px 0;touch-action:none}.canvas-dialog{position:absolute;inset:0;z-index:15;display:grid;place-items:center;background:var(--color-background-overlay);padding:12px}.canvas-dialog form{width:100%;max-width:420px;box-sizing:border-box;padding:18px;border-radius:10px;background:var(--color-surface-primary)}.canvas-dialog form>div{display:flex;gap:8px;justify-content:flex-end}.repair{display:flex;flex:1;flex-direction:column}.repair textarea{flex:1;min-height:200px;font:14px var(--font-ui-mono);white-space:pre}.canvas-editor>p[role=alert]{padding:6px 12px;color:var(--color-error);font-size:13px;margin:0}
.repair textarea,.canvas-properties input,.canvas-properties select,.canvas-properties textarea,.canvas-dialog input{background:var(--color-surface-primary);color:var(--color-text-primary)}
</style>
