<script setup lang="ts">
import { ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import ControlIcon from '@/components/common/ControlIcon.vue'
import { t as translate } from '@/i18n'
const props=withDefaults(defineProps<{readOnly:boolean;selectionCount:number;edgeSelected:boolean;canUndo:boolean;canRedo:boolean;nodeCount:number;sidebar:boolean;showInspector?:boolean;arrangeEnabled?:boolean;touch?:boolean;language?:string}>(),{showInspector:true,arrangeEnabled:true})
const emit=defineEmits<{addText:[];addFile:[];addLink:[];group:[];connect:[];undo:[];redo:[];copy:[];paste:[];remove:[];arrange:[];toggleInspector:[]}>()
const t=(zh:string,en:string)=>props.language==='en'?en:props.language==='zh-CN'?zh:translate(zh,en)
const toolsOpen=ref(false),toolsMenu=ref<HTMLElement>(),toolsTrigger=ref<HTMLButtonElement>()
function closeTools(event:PointerEvent){if(!toolsMenu.value?.contains(event.target as Node))toolsOpen.value=false}
async function toggleTools(){toolsOpen.value=!toolsOpen.value;if(toolsOpen.value){await nextTick();toolsMenu.value?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus()}}
function toolsKeys(event:KeyboardEvent){
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();toolsOpen.value=false;toolsTrigger.value?.focus();return}
  if(!['ArrowDown','ArrowUp','Home','End','Tab'].includes(event.key))return
  if(event.key==='Tab'){toolsOpen.value=false;return}
  event.preventDefault();event.stopPropagation();const items=[...toolsMenu.value!.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')],index=items.indexOf(event.target as HTMLButtonElement)
  items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus()
}

onMounted(()=>window.addEventListener('pointerdown',closeTools))
onBeforeUnmount(()=>window.removeEventListener('pointerdown',closeTools))
defineExpose({closeMenu:()=>{if(!toolsOpen.value)return false;toolsOpen.value=false;return true}})
</script>

<template>
    <div class="canvas-toolbar" :class="{'touch-toolbar':touch}" role="toolbar" :aria-label="t('画布操作','Canvas operations')">
      <div class="canvas-tool-group">
        <button class="button-secondary" :disabled="readOnly" @click="emit('addText')">{{ t('文字','Text') }}</button><button class="button-secondary" :disabled="readOnly" @click="emit('addFile')">{{ t('笔记／图片','Note / image') }}</button><button class="button-secondary" :disabled="readOnly" @click="emit('addLink')">{{ t('网址','URL') }}</button><button class="button-secondary" :disabled="readOnly" @click="emit('group')">{{ t('分组','Group') }}</button>
      </div>
      <div class="canvas-tool-group canvas-history-tools">
        <button class="button-secondary canvas-icon-button" :title="t('连接所选节点','Connect selected nodes')" :aria-label="t('连接所选节点','Connect selected nodes')" :disabled="readOnly||selectionCount!==2" @click="emit('connect')"><ControlIcon name="link" /></button>
        <button class="button-secondary canvas-icon-button" :title="t('撤销','Undo')+' · Ctrl+Z'" :disabled="readOnly||!canUndo" @click="emit('undo')"><ControlIcon name="undo" /><span class="canvas-sr-only">{{ t('撤销','Undo') }}</span></button><button class="button-secondary canvas-icon-button" :title="t('重做','Redo')+' · Ctrl+Shift+Z'" :disabled="readOnly||!canRedo" @click="emit('redo')"><ControlIcon name="redo" /><span class="canvas-sr-only">{{ t('重做','Redo') }}</span></button>
      </div>
      <slot name="selection" /><div class="canvas-view-tools">
        <div ref="toolsMenu" class="canvas-tools-menu">
          <button ref="toolsTrigger" class="button-secondary canvas-icon-button" :title="t('更多画布操作','More canvas actions')" :aria-label="t('更多画布操作','More canvas actions')" aria-haspopup="menu" :aria-expanded="toolsOpen" @click="toggleTools" @keydown.down.prevent="!toolsOpen&&toggleTools()">···</button>
          <div v-if="toolsOpen" class="canvas-tools-popover" role="menu" :aria-label="t('更多画布操作','More canvas actions')" @keydown="toolsKeys" @click="toolsOpen=false">
            <button role="menuitem" :disabled="!selectionCount" @click="emit('copy')">{{ t('复制','Copy') }}</button><button role="menuitem" :disabled="readOnly" @click="emit('paste')">{{ t('粘贴','Paste') }}</button><button role="menuitem" :disabled="readOnly||(!selectionCount&&!edgeSelected)" @click="emit('remove')">{{ t('删除所选','Delete selected') }}</button>
            <span class="canvas-menu-divider" role="separator" />
            <button v-if="arrangeEnabled" role="menuitem" :disabled="readOnly||!nodeCount" @click="emit('arrange')">{{ t('整理为思维导图','Arrange as mind map') }}</button>
            <slot name="tools" />
          </div>
        </div>
        <button v-if="showInspector" class="button-secondary inspector-toggle" :aria-expanded="sidebar" :aria-label="sidebar?t('隐藏右侧栏','Hide right sidebar'):t('显示右侧栏','Show right sidebar')" :title="sidebar?t('隐藏右侧栏','Hide right sidebar'):t('显示右侧栏','Show right sidebar')" @click="emit('toggleInspector')"><ControlIcon name="panelClose" :class="{ mirrored: sidebar }" /><span>{{ t('节点与属性','Nodes and properties') }}</span></button>
      </div>
    </div>
</template>

<style scoped>
.canvas-toolbar{display:flex;gap:var(--space-xs);padding:var(--space-sm);flex-wrap:wrap;border-bottom:1px solid var(--color-border-default)}
.canvas-toolbar button{font-size:var(--font-size-xs)}
.canvas-toolbar{ position: relative; z-index: 12; align-items: center; gap: 10px; padding: 7px 12px; background: var(--color-surface-primary); }
.canvas-toolbar .button-secondary{ height: 30px; padding: 4px 10px; }
.canvas-tool-group, .canvas-view-tools{ display: flex; align-items: center; gap: 4px; }
.canvas-history-tools{ border-left: 1px solid var(--color-border-default); padding-left: 10px; }
.canvas-view-tools{ margin-left: auto; gap: 8px; }
.canvas-icon-button{ display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0 !important; }
.canvas-sr-only{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.inspector-toggle{ display: inline-flex; align-items: center; gap: 7px; }
.inspector-toggle[aria-expanded="true"]{ color: var(--color-accent-primary); background: var(--color-accent-soft); }
.mirrored{ transform: scaleX(-1); }
.canvas-tools-menu{ position: relative; }
.canvas-tools-popover{ position: absolute; right: 0; top: calc(100% + 8px); width: max-content; min-width: 190px; padding: 6px; display: grid; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-elevated); box-shadow: var(--shadow-lg); }
.canvas-tools-popover :deep(button){ width: 100%; text-align: left; padding: 8px 12px; font-size: var(--font-size-sm); border-radius: var(--radius-sm); white-space: nowrap; }
.canvas-tools-popover :deep(button:hover:not(:disabled)), .canvas-tools-popover :deep(button:focus-visible){ background: var(--color-accent-soft); color: var(--color-accent-primary); }
.canvas-menu-divider{ height: 1px; background: var(--color-border-subtle); margin: 5px; }

@media(max-width:640px){.canvas-toolbar{max-height:none;overflow:visible;gap:6px}.inspector-toggle > span{display:none}.canvas-view-tools{gap:4px}}
.touch-toolbar .button-secondary,.touch-toolbar :deep(.canvas-tools-popover button){min-height:44px;min-width:44px;height:auto}.touch-toolbar .canvas-tool-group{flex-wrap:wrap}.touch-toolbar .canvas-history-tools{padding-left:0;border-left:0}
</style>
