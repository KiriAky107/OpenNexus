<script setup lang="ts">
import { ref } from 'vue'
import type { CanvasNode, CanvasEdge } from './canvasModel'
import { t as translate } from '@/i18n'
const props=withDefaults(defineProps<{node?:CanvasNode;edge?:CanvasEdge;nodeValues:Record<string,unknown>;edgeValues:Record<string,unknown>;selectionCount:number;readOnly:boolean;immediate?:boolean;touch?:boolean;language?:string}>(),{})
const emit=defineEmits<{nodeInput:[key:string,event:Event];edgeInput:[key:string,event:Event];nodeCompositionEnd:[key:string,event:CompositionEvent];edgeCompositionEnd:[key:string,event:CompositionEvent];compositionStart:[];applyNode:[];applyEdge:[]}>()
const t=(zh:string,en:string)=>props.language==='en'?en:props.language==='zh-CN'?zh:translate(zh,en)
const dimensionLabel=(key:'x'|'y'|'width'|'height')=>({x:t('横坐标','x'),y:t('纵坐标','y'),width:t('宽度','width'),height:t('高度','height')})[key]
const sideLabel=(key:'fromSide'|'toSide')=>key==='fromSide'?t('起点侧边','From side'):t('终点侧边','To side')
const sideText=(side:'left'|'right'|'top'|'bottom')=>({left:t('左侧','Left'),right:t('右侧','Right'),top:t('上侧','Top'),bottom:t('下侧','Bottom')})[side]
const endLabel=(key:'fromEnd'|'toEnd')=>key==='fromEnd'?t('起点箭头','From arrow'):t('终点箭头','To arrow')
const composing=ref(false)
const value=(key:string,edge=false)=>String((edge?props.edgeValues:props.nodeValues)[key]??'')
function input(key:string,event:Event,edge=false){if(composing.value&&!props.immediate)return;if(edge)emit('edgeInput',key,event);else emit('nodeInput',key,event)}
function start(){composing.value=true;emit('compositionStart')}
function end(key:string,event:CompositionEvent,edge=false){composing.value=false;if(edge)emit('edgeCompositionEnd',key,event);else emit('nodeCompositionEnd',key,event)}
</script>

<template>
  <div class="canvas-property-fields" :class="{'touch-properties':touch}">
    <form v-if="node" class="node-properties" @submit.prevent="emit('applyNode')">
      <strong>{{ t('节点属性','Node properties') }} · {{ selectionCount }}</strong>
      <label>{{ node.type==='file'?t('知识库相对路径','Vault-relative path'):node.type==='link'?'URL':t('内容','Content') }}<textarea :value="value('value')" class="textarea" :aria-label="t('节点内容','Node content')" :readonly="readOnly" :disabled="readOnly&&!touch" :inputmode="node.type==='link'?'url':undefined" rows="4" @input="input('value',$event)" @compositionstart="start" @compositionend="end('value',$event)" /></label>
      <slot name="nodeExtra" />
      <div class="node-dimensions numeric-fields"><label v-for="key in (['x','y','width','height'] as const)" :key="key">{{ dimensionLabel(key) }}<input :value="value(key)" class="input" type="number" :aria-label="dimensionLabel(key)" :readonly="readOnly" :disabled="readOnly&&!touch" @input="input(key,$event)" /></label></div>
      <label>{{ t('颜色','Color') }}<select :value="value('color')" class="select" :aria-label="t('节点颜色','Node color')" :disabled="readOnly" @change="input('color',$event)"><option value="">{{ t('默认','Default') }}</option><option v-for="color in ['1','2','3','4','5','6']" :key="color" :value="color">{{ color }}</option><option v-if="value('color').startsWith('#')" :value="value('color')">{{ value('color') }}</option></select></label>
      <label v-if="node.type==='file'">{{ t('标题或块子路径','Heading or block subpath') }}<input :value="value('subpath')" class="input" :aria-label="t('标题或块子路径','Heading or block subpath')" placeholder="#heading" :readonly="readOnly" :disabled="readOnly&&!touch" @input="input('subpath',$event)" @compositionstart="start" @compositionend="end('subpath',$event)" /></label>
      <slot name="nodeActions" />
      <button v-if="!immediate" class="button-primary" :disabled="readOnly||composing">{{ t('应用属性修改','Apply property changes') }}</button>
    </form>
    <form v-if="edge" class="node-properties" @submit.prevent="emit('applyEdge')">
      <strong>{{ t('连接属性','Connection properties') }}</strong>
      <label>{{ t('连线标签','Connection label') }}<input :value="value('label',true)" class="input" :aria-label="t('连线标签','Connection label')" :readonly="readOnly" :disabled="readOnly&&!touch" @input="input('label',$event,true)" @compositionstart="start" @compositionend="end('label',$event,true)" /></label>
      <slot name="edgeExtra" />
      <label v-for="key in (['fromSide','toSide'] as const)" :key="key">{{ sideLabel(key) }}<select :value="value(key,true)" class="select" :aria-label="sideLabel(key)" :disabled="readOnly" @change="input(key,$event,true)"><option v-if="immediate" value="">{{ t('自动','Auto') }}</option><option v-for="side in (['left','right','top','bottom'] as const)" :key="side" :value="side">{{ sideText(side) }}</option></select></label>
      <label v-for="key in (['fromEnd','toEnd'] as const)" :key="key">{{ endLabel(key) }}<select :value="value(key,true)" class="select" :aria-label="endLabel(key)" :disabled="readOnly" @change="input(key,$event,true)"><option v-if="immediate" value="">{{ t('自动','Auto') }}</option><option value="none">{{ t('无箭头','No arrow') }}</option><option value="arrow">{{ t('箭头','Arrow') }}</option></select></label>
      <slot name="edgeActions" />
      <button v-if="!immediate" class="button-primary" :disabled="readOnly||composing">{{ t('应用连接修改','Apply connection changes') }}</button>
    </form>
  </div>
</template>

<style scoped>
.node-properties{display:grid;gap:var(--space-sm);margin-bottom:var(--space-lg)}
label,:deep(label){display:grid;gap:var(--space-xs);font-size:var(--font-size-xs)}
.node-dimensions{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-sm)}
input,textarea,select,:deep(input),:deep(select){width:100%;min-width:0;box-sizing:border-box}
.touch-properties input,.touch-properties textarea,.touch-properties select,.touch-properties :deep(input),.touch-properties :deep(select){font-size:16px;min-height:44px;background:var(--color-surface-primary);color:var(--color-text-primary)}
</style>
