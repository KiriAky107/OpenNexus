<script setup lang="ts">
import { computed,ref,watch } from 'vue'
import ControlIcon from '@/components/common/ControlIcon.vue'
import { nodeLabel,type CanvasNode,type CanvasEdge } from './canvasModel'
import { t as translate } from '@/i18n'
const props=defineProps<{nodes:CanvasNode[];edges:CanvasEdge[];selected:string[];blocked?:boolean;touch?:boolean;language?:string}>()
defineEmits<{close:[];locate:[id:string];selectEdge:[id:string]}>()
const t=(zh:string,en:string)=>props.language==='en'?en:props.language==='zh-CN'?zh:translate(zh,en)
const filter=ref(''),count=ref(100)
const filtered=computed(()=>props.nodes.filter(node=>`${node.text??''} ${node.file??''} ${node.url??''} ${node.label??''}`.toLowerCase().includes(filter.value.trim().toLowerCase())))
const graph=computed(()=>new Map(props.nodes.map(node=>[node.id,node])))
watch(filter,()=>count.value=100)
</script>

<template>
  <aside class="canvas-inspector" :class="{'touch-inspector':touch}" :aria-label="t('画布节点与连接','Canvas nodes and connections')">
    <div class="canvas-inspector-heading"><strong>{{ t('节点与属性','Nodes and properties') }}</strong><button type="button" class="button-secondary canvas-icon-button" :disabled="blocked" :aria-label="t('收起右侧栏','Collapse right sidebar')" :title="t('收起右侧栏','Collapse right sidebar')" @click="$emit('close')"><ControlIcon name="close" :size="16" /></button></div>
    <p class="subtle"><slot name="hint">{{ t('Shift 选择两个节点后连接；拖动空白区域框选，空格拖动平移。','Shift-select two nodes to connect. Drag empty space to select; Space-drag to pan.') }}</slot></p>
    <slot />
    <h3>{{ t('节点列表','Node list') }} · {{ nodes.length }}</h3><input v-model="filter" class="input" :aria-label="t('筛选画布节点','Filter canvas nodes')" />
    <div class="canvas-node-list"><button v-for="node in filtered.slice(0,count)" :key="node.id" class="button-secondary" :disabled="blocked" :aria-pressed="selected.includes(node.id)" :data-list-node-id="node.id" :title="String(node.text??node.file??node.url??node.label??'')" @click="$emit('locate',node.id)"><span class="node-kind">{{ node.type }} · </span><span class="node-label">{{ nodeLabel(node) }}</span></button><button v-if="count<filtered.length" class="button-secondary" @click="count+=100">{{ t('更多节点','More nodes') }}</button></div>
    <h3>{{ t('连接列表','Connection list') }} · {{ edges.length }}</h3><div class="canvas-node-list"><button v-for="edge in edges.filter(value=>!selected.length||selected.includes(value.fromNode)||selected.includes(value.toNode)).slice(0,100)" :key="edge.id" class="button-secondary" :disabled="blocked" @click="$emit('selectEdge',edge.id)">{{ edge.label??`${nodeLabel(graph.get(edge.fromNode)!)} → ${nodeLabel(graph.get(edge.toNode)!)}` }}</button></div>
  </aside>
</template>

<style scoped>
.canvas-inspector{box-sizing:border-box;width:244px;flex-shrink:0;overflow:auto;padding:0 12px 12px;border-left:1px solid var(--color-border-default);background:var(--color-surface-primary)}
.canvas-inspector-heading{position:sticky;top:0;z-index:1;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 0;background:var(--color-surface-primary);font-size:var(--font-size-sm)}
.canvas-icon-button{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;padding:0}
.subtle{font-size:var(--font-size-xs);line-height:1.6;margin:0 0 14px}h3{font-size:var(--font-size-sm);margin-block:18px 8px}
.canvas-node-list{display:grid;gap:var(--space-xs);margin-top:var(--space-sm)}
.canvas-node-list button{display:flex;align-items:flex-start;gap:4px;height:auto;min-height:36px;padding:8px 10px;line-height:1.4;text-align:left;overflow-wrap:anywhere;white-space:normal;font-size:var(--font-size-xs)}
.canvas-node-list button[aria-pressed="true"]{border-color:var(--color-border-focus);color:var(--color-accent-primary);background:var(--color-accent-soft)}
.node-kind{flex-shrink:0;color:var(--color-text-tertiary);font-size:10px;line-height:1.7}.node-label{min-width:0;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
@media(max-width:640px){.canvas-inspector{width:auto;max-height:40%;border-left:0;border-top:1px solid var(--color-border-default)}}
.touch-inspector{position:absolute;z-index:10;left:8px;right:8px;bottom:8px;max-height:52%;width:auto;border:1px solid var(--color-border-default);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg)}
.touch-inspector .canvas-icon-button,.touch-inspector .canvas-node-list button{min-height:44px;min-width:44px}
</style>
