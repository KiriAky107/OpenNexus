<script setup lang="ts">
import { computed, ref } from 'vue'
import { canvasColor as color } from './canvasColors'
import { connectionLabel, nodeLabel, type CanvasNode } from './canvasModel'
import type { GeometryItem } from './canvasGeometry'
import { workspaceDocumentType } from '@/services/workspaceDocuments'
import { t } from '@/i18n'
const props = defineProps<{
  nodes: CanvasNode[]; nodeCount: number; edges: GeometryItem[]; order: ReadonlyMap<string, number>;
  pan: { x: number; y: number }; zoom: number; selected: string[]; selectedEdge: string;
  previews: Record<string, { text?: string; url?: string }>; readOnly: boolean; label: string; emptyLabel: string; touch?: boolean;
  nodeLookup?: ReadonlyMap<string,CanvasNode>
}>()
const emit = defineEmits<{ nodePointer: [event: PointerEvent, node: CanvasNode]; resizePointer: [event: PointerEvent, node: CanvasNode]; edgePointer: [event: PointerEvent]; locate: [id: string]; open: [node: CanvasNode]; selectEdge: [id: string]; imageError: [path: string] }>()
const element = ref<HTMLElement>()
const selectedSet = computed(() => new Set(props.selected))
const nodeIndex = computed(() => props.nodeLookup ?? new Map(props.nodes.map(node => [node.id,node])))
const imagePath = (node: CanvasNode) => node.type === 'group' ? node.background : node.file
function groupBackground(node: CanvasNode) {
  const url = props.previews[node.background ?? '']?.url
  return { backgroundImage: url ? `url("${url}")` : undefined, backgroundSize: node.backgroundStyle === 'repeat' ? 'auto' : node.backgroundStyle === 'ratio' ? 'contain' : 'cover', backgroundRepeat: node.backgroundStyle === 'repeat' ? 'repeat' : 'no-repeat', backgroundPosition: 'center', width: '100%', height: '100%' }
}
defineExpose({ get element() { return element.value } })
</script>

<template>
<div ref="element" class="canvas-viewport" :class="{ 'touch-canvas': touch }" tabindex="0" role="region" :aria-label="label">
        <div class="canvas-world" :style="{transform:`translate(${pan.x}px,${pan.y}px) scale(${zoom})`}">
          <svg class="canvas-edges" width="1" height="1" :style="{zIndex:nodeCount+1}" :aria-label="t('节点连接','Node connections')"><defs><marker id="canvas-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M 0 0 L 8 4 L 0 8 z" fill="context-stroke" /></marker></defs>
            <g v-for="{edge:line,geometry} in edges" :key="line.id" v-memo="[line,geometry,selectedEdge===line.id,t('连接','Connection'),connectionLabel(line,nodeIndex)]" class="canvas-edge" :class="{'edge-selected':selectedEdge===line.id}" role="button" tabindex="0" :aria-label="`${t('连接','Connection')}: ${connectionLabel(line,nodeIndex)}`" :data-edge="line.id" @pointerdown.stop="emit('edgePointer', $event)" @click="emit('selectEdge', line.id)" @keydown.enter.stop="emit('selectEdge', line.id)">
              <path :d="geometry!.path" fill="none" :stroke="line.color?color(line.color):'var(--color-text-tertiary)'" :stroke-width="selectedEdge===line.id?4:2" :marker-start="line.fromEnd==='arrow'?'url(#canvas-arrow)':undefined" :marker-end="line.toEnd==='none'?undefined:'url(#canvas-arrow)'"/><path :d="geometry!.path" fill="none" stroke="transparent" stroke-width="16" />
              <text v-if="line.label" :x="geometry!.x" :y="geometry!.y-8" text-anchor="middle">{{ line.label.slice(0,120) }}</text>
            </g>
          </svg>
          <div v-for="node in nodes" :key="node.id" v-memo="[node,selectedSet.has(node.id),previews[imagePath(node)??''],readOnly,t('文字','Text')]" class="canvas-node" :class="[node.type,{selected:selectedSet.has(node.id)}]" :data-node-id="node.id" :data-node="node.id" tabindex="0" role="group" :aria-label="`${node.type}: ${node.type==='file'?node.file:nodeLabel(node)}`" :style="{left:`${node.x}px`,top:`${node.y}px`,width:`${node.width}px`,height:`${node.height}px`,borderColor:color(node.color),zIndex:(order.get(node.id)??0)+1}" @pointerdown.stop="emit('nodePointer', $event, node)" @keydown.enter.self.prevent.stop="emit('locate', node.id)">
            <header><span>{{ node.type==='text'?t('文字','Text'):node.type==='file'?t('文件','File'):node.type==='link'?t('网址','URL'):node.label??t('分组','Group') }}</span><button v-if="node.type==='file'&&['markdown','canvas'].includes(workspaceDocumentType(node.file??''))||node.type==='link'" class="node-open" :aria-label="t('打开节点目标','Open node target')" @pointerdown.stop @click.stop="emit('open', node)">↗</button></header>
            <div class="canvas-node-content"><template v-if="node.type==='text'">{{ node.text }}</template><template v-else-if="node.type==='link'"><strong>{{ node.url }}</strong><p>{{ t('选择打开时会使用浏览器。','Opens in your browser when selected.') }}</p></template><template v-else-if="node.type==='file'"><strong :title="node.file">{{ nodeLabel(node) }}</strong><img v-if="previews[node.file??'']?.url" :src="previews[node.file??'']!.url" :alt="node.file??''" draggable="false" @error="emit('imageError', node.file!)"/><p v-else>{{ previews[node.file??'']?.text??t('读取引用内容…','Loading referenced content…') }}</p></template><div v-else-if="node.background&&previews[node.background]?.url" class="canvas-group-background" role="img" :aria-label="node.label??node.background" :style="groupBackground(node)"/></div>
            <button v-if="selectedSet.has(node.id)&&!readOnly" class="node-resize" :aria-label="t('调整节点尺寸','Resize node')" data-resize="true" @pointerdown.stop="emit('resizePointer', $event, node)">↘</button>
          </div>
        </div>
        <slot />
        <p v-if="!nodeCount" class="canvas-empty">{{ emptyLabel }}</p>
      </div>
</template>

<style scoped>
.canvas-group-background{position:absolute;inset:0;z-index:-1;pointer-events:none}
.canvas-viewport{position:relative;flex:1;overflow:hidden;min-height:200px;min-width:0;touch-action:none;background-color:var(--color-background-secondary);background-image:radial-gradient(var(--color-border-default) 1px,transparent 1px);background-size:20px 20px;user-select:none}
.canvas-world{position:absolute;left:0;top:0;transform-origin:0 0;pointer-events:none}
.canvas-edges{position:absolute;overflow:visible;pointer-events:none}
.canvas-edge{pointer-events:stroke;cursor:pointer}
.canvas-edge text{fill:var(--color-text-primary);font-size:14px;paint-order:stroke;stroke:var(--color-background-secondary);stroke-width:5px;pointer-events:auto}
.canvas-node{position:absolute;box-sizing:border-box;border:2px solid var(--color-border-default);border-radius:var(--radius-md);background:var(--color-surface-primary);box-shadow:var(--shadow-sm);display:flex;flex-direction:column;overflow:hidden;pointer-events:auto;cursor:grab}
.canvas-node.selected{outline:3px solid var(--color-border-focus);outline-offset:2px}
.canvas-node.group{background:color-mix(in srgb,var(--color-accent-soft) 25%,transparent);box-shadow:none}
.canvas-node header{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--color-border-subtle);font-size:12px;color:var(--color-text-secondary);min-height:20px}
.canvas-node-content{flex:1;min-height:0;padding:12px;white-space:pre-wrap;overflow:hidden;overflow-wrap:anywhere;font-size:14px;line-height:1.5}
.canvas-node-content p{font-size:12px;color:var(--color-text-secondary);margin-block:8px}
.canvas-node-content img{width:100%;height:calc(100% - 24px);object-fit:contain}
.node-open{cursor:pointer;border:1px solid var(--color-border-default);border-radius:var(--radius-sm);width:24px;height:24px;background:var(--color-background-primary);color:var(--color-text-primary)}
.node-resize{position:absolute;bottom:0;right:0;width:24px;height:24px;cursor:nwse-resize;background:var(--color-accent-soft);color:var(--color-text-primary)}
.canvas-selection-box{position:absolute;border:1px solid var(--color-border-focus);background:var(--color-accent-soft);opacity:.65;pointer-events:none}
.canvas-empty{position:absolute;top:40%;left:15%;right:15%;text-align:center;color:var(--color-text-secondary);pointer-events:none}
.canvas-node header > span{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.canvas-node-content > strong{ display: block; font-size: 14px; }
@media(max-width:640px){.canvas-viewport{min-height:180px}}
.touch-canvas .node-open,.touch-canvas .node-resize{width:44px;height:44px}
</style>
