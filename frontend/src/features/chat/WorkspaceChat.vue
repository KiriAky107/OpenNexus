<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatView from './ChatView.vue'
import { useEditorStore } from '@/stores/editor'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const editor = useEditorStore()
const context = computed(() => editor.currentFilePath ? { file_path: editor.currentFilePath, content: editor.content } : undefined)
const panel = ref<HTMLElement | null>(null)
const storageKey = 'notes-agent.workspace-chat.bounds.v1'
const width = ref(640), height = ref(680)
const x = ref(Math.max(8, window.innerWidth - 660)), y = ref(64)
try { const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null'); if (saved && [saved.x,saved.y,saved.width,saved.height].every(Number.isFinite)) { x.value=saved.x; y.value=saved.y; width.value=saved.width; height.value=saved.height } } catch { /* storage unavailable */ }
function save() { try { localStorage.setItem(storageKey, JSON.stringify({x:x.value,y:y.value,width:width.value,height:height.value})) } catch { /* storage unavailable */ } }
function reset() { width.value=640; height.value=680; x.value=window.innerWidth-660; y.value=32; clamp(); save() }
let resizing: { x:number; y:number; width:number; height:number } | null = null
function resizeStart(e: PointerEvent) { if (e.button !== 0) return; resizing={x:e.clientX,y:e.clientY,width:width.value,height:height.value}; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); e.preventDefault() }
function resizeMove(e: PointerEvent) { if (!resizing) return; width.value=resizing.width+e.clientX-resizing.x; height.value=resizing.height+e.clientY-resizing.y; clamp(); save() }
let drag: { id: number; x: number; y: number; left: number; top: number } | null = null
function clamp() {
  width.value=Math.min(Math.max(360,width.value),window.innerWidth-16); height.value=Math.min(Math.max(360,height.value),window.innerHeight-16)
  x.value = Math.max(8, Math.min(x.value, window.innerWidth - width.value - 8))
  y.value = Math.max(8, Math.min(y.value, window.innerHeight - height.value - 8))
}
function start(event: PointerEvent) {
  if (event.button !== 0 || (event.target as Element).closest('button,a')) return
  drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: x.value, top: y.value }
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}
function move(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId) return
  x.value = drag.left + event.clientX - drag.x; y.value = drag.top + event.clientY - drag.y; clamp(); save()
}
function keyboard(event: KeyboardEvent) {
  if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return
  event.preventDefault()
  x.value += event.key === 'ArrowRight' ? 20 : event.key === 'ArrowLeft' ? -20 : 0
  y.value += event.key === 'ArrowDown' ? 20 : event.key === 'ArrowUp' ? -20 : 0
  clamp(); save()
}
onMounted(() => { clamp(); window.addEventListener('resize', clamp) })
onBeforeUnmount(() => window.removeEventListener('resize', clamp))
</script>
<template>
  <Teleport to="body">
    <section v-show="props.open" ref="panel" class="workspace-chat surface" role="dialog" aria-label="工作区 AI 对话" :style="{ left: x + 'px', top: y + 'px', width: width + 'px', height: height + 'px' }" @keydown.esc.stop="emit('close')">
      <header class="workspace-chat-handle" tabindex="0" aria-label="拖动聊天窗口，也可使用方向键移动" @pointerdown="start" @pointermove="move" @pointerup="drag = null" @lostpointercapture="drag = null" @keydown="keyboard">
        <strong>工作区 AI 对话</strong><button class="button-secondary" @click="reset">重置窗口</button><a href="#/chat">在 AI 对话页继续</a><button class="button-secondary" aria-label="关闭聊天窗口" @click="emit('close')">关闭</button>
      </header>
      <ChatView embedded :workspace-context="context" />
      <button class="window-resizer" aria-label="调整聊天窗口大小" title="拖动调整大小" @pointerdown="resizeStart" @pointermove="resizeMove" @pointerup="resizing=null" @lostpointercapture="resizing=null" @keydown.right.prevent="width+=20; clamp(); save()" @keydown.left.prevent="width-=20; clamp(); save()" @keydown.down.prevent="height+=20; clamp(); save()" @keydown.up.prevent="height-=20; clamp(); save()">◢</button>
    </section>
  </Teleport>
</template>
<style scoped>
.window-resizer { position:absolute; right:0; bottom:0; width:20px; height:20px; min-height:0; padding:0; border:0; background:transparent; color:var(--color-text-secondary); cursor:nwse-resize; touch-action:none; }
.workspace-chat { position: fixed; z-index: 100; display: flex; flex-direction: column; width: min(640px, calc(100vw - 16px)); height: min(680px, calc(100dvh - 16px)); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-background-primary); color: var(--color-text-primary); box-shadow: var(--shadow-md); overflow: hidden; }
.workspace-chat-handle { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 10px 14px; background: var(--color-surface-secondary); cursor: move; touch-action: none; flex-shrink: 0; }
.workspace-chat-handle strong { flex: 1 1 130px; margin-right: auto; }
.workspace-chat :deep(.chat-page) { flex: 1; }
.workspace-chat :deep(.chat-toolbar) { padding: 10px; gap: 8px; }
.workspace-chat :deep(.message-timeline) { padding: 12px; }
.workspace-chat :deep(.chat-composer) { padding: 12px; }
</style>
