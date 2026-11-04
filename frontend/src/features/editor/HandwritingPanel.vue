<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import HandwritingView from './HandwritingView.vue'
import { cloneInk, emptyHandwriting, eraseStroke, fitHandwrittenText, HANDWRITING_COLORS, InkInput, layoutHandwrittenText, MAX_INK_POINTS, strokePath, type Handwriting, type InkPoint } from './handwriting'
export type HandwritingAction = 'recognize' | 'summarize' | 'organize' | 'generate'
export interface HandwritingAnalysis { action: HandwritingAction; text: string; image: string; instruction: string }
import type { HandwritingProposal } from './handwritingActions'
const props=defineProps<{ ink?: Handwriting; text?: string; language?: string; readOnly?: boolean; analyze?: (request: HandwritingAnalysis, signal: AbortSignal) => Promise<string> }>()
const emit=defineEmits<{close:[];apply:[proposal:HandwritingProposal]}>()
const t=(zh:string,en:string)=>props.language==='en'?en:zh
const initialInk=cloneInk(props.ink??emptyHandwriting())
const ink=ref(fitHandwrittenText(props.text??'',initialInk)??initialInk), text=ref(props.text??''), color=ref<string>(HANDWRITING_COLORS.ink), width=ref(3), penOnly=ref(false), eraser=ref(false)
const input=new InkInput(), revision=ref(0), surface=ref<HTMLElement>(), drawing=ref(false), dirty=ref(false), discard=ref(false)
const past: string[]=[], future: string[]=[]
const error=ref(''), result=ref(''), instruction=ref(''), action=ref<HandwritingAction>('recognize'), busy=ref(false)
const dialog=ref<HTMLElement>()
let previousFocus:HTMLElement|null=null
let controller: AbortController|undefined, generation=0, disposed=false
let resizeObserver:ResizeObserver|undefined
const draft=computed(()=>{void revision.value;return input.draft?strokePath(input.draft):undefined})
const overflow=computed(()=>ink.value.mode==='text'&&layoutHandwrittenText(text.value,ink.value).overflow)
function remember(value=JSON.stringify(ink.value)){past.push(value);let size=past.reduce((n,s)=>n+s.length*2,0);while(past.length>1&&(past.length>40||size>8*1024*1024))size-=past.shift()!.length*2;future.length=0;dirty.value=true;revision.value++}
function undo(){input.cancel();drawing.value=false;const previous=past.pop();if(previous){future.push(JSON.stringify(ink.value));ink.value=JSON.parse(previous);dirty.value=true;revision.value++}}
function redo(){const next=future.pop();if(next){past.push(JSON.stringify(ink.value));ink.value=JSON.parse(next);dirty.value=true;revision.value++}}
function point(event:PointerEvent): InkPoint {
  const rect=surface.value!.getBoundingClientRect()
  return {x:Math.max(0,Math.min(ink.value.width,(event.clientX-rect.left)*ink.value.width/rect.width)),y:Math.max(0,Math.min(ink.value.height,(event.clientY-rect.top)*ink.value.height/rect.height)),pressure:event.pointerType==='pen'?Math.max(.05,Math.min(1,event.pressure)):.5}
}
function down(event:PointerEvent){
  if(props.readOnly||busy.value||ink.value.mode!=='ink'||event.button!==0)return
  event.preventDefault();surface.value?.setPointerCapture?.(event.pointerId)
  if(eraser.value){if(!event.isPrimary)return;const previous=JSON.stringify(ink.value);if(eraseStroke(ink.value,point(event)))remember(previous);return}
  input.down(event.pointerId,event.pointerType,point(event),color.value,width.value,penOnly.value);drawing.value=!!input.draft;revision.value++
}
function move(event:PointerEvent){
  if(props.readOnly||busy.value)return
  const samples=event.getCoalescedEvents?.()??[event]
  input.move(event.pointerId,(samples.length?samples:[event]).map(point));revision.value++
}
function up(event:PointerEvent){
  if(!props.readOnly&&!busy.value)input.move(event.pointerId,[point(event)])
  const stroke=input.up(event.pointerId);drawing.value=false;revision.value++
  if(input.overflowed){error.value=t('本笔画超过采样上限，未保存不完整笔画；请分段书写。','This stroke exceeds the sample limit. No partial stroke was saved. Use shorter strokes.');return}
  if(!stroke||props.readOnly||busy.value)return
  if(ink.value.strokes.length>=1000||ink.value.strokes.reduce((n,s)=>n+s.points.length,0)+stroke.points.length>MAX_INK_POINTS){error.value=t('这一页笔迹已达上限，请保存后新建手写节点。','This page is full. Save it and add another handwriting node.');return}
  remember();ink.value.strokes.push(stroke)
}
function cancelPointer(event?:PointerEvent){input.cancel(event?.pointerId);drawing.value=false;revision.value++}
function close(){if(busy.value){cancelAi();return}if(dirty.value)discard.value=true;else emit('close')}
function cancelAi(){generation++;controller?.abort();controller=undefined;busy.value=false}
function invalidateAi(){cancelAi();result.value='';error.value=''}
function visibility(){if(document.visibilityState==='hidden'){cancelPointer();cancelAi()}}
function trapTab(event:KeyboardEvent){const items=[...dialog.value!.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')].filter(item=>!item.closest('details:not([open])')||item.closest('summary'));if(!items.length)return;const first=items[0]!,last=items.at(-1)!;if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
watch(()=>props.readOnly,value=>{if(value){cancelPointer();cancelAi()}})
watch(text,()=>{dirty.value=true;result.value=''})
function apply(kind:HandwritingProposal['kind'],value?:string,outputInk?:Handwriting){if(props.readOnly||busy.value||drawing.value)return;emit('apply',{kind,original:{ink:cloneInk(ink.value),text:text.value},output:value===undefined?undefined:{text:value,ink:outputInk}})}
function save(){if(!overflow.value)apply('save')}
async function image():Promise<string>{
  if(ink.value.mode==='text')return ''
  const canvas=document.createElement('canvas');canvas.width=ink.value.width;canvas.height=ink.value.height
  const context=canvas.getContext('2d');if(!context)throw Error(t('无法生成手写图像。','Unable to render handwriting.'))
  context.fillStyle=HANDWRITING_COLORS.paper;context.fillRect(0,0,canvas.width,canvas.height)
  for(const stroke of ink.value.strokes){context.fillStyle=stroke.color;context.fill(new Path2D(strokePath(stroke)))}
  return canvas.toDataURL('image/png')
}
async function run(){
  if(!props.analyze||props.readOnly||busy.value||drawing.value)return
  if(action.value!=='generate'&&!text.value.trim()&&!ink.value.strokes.length)return
  cancelPointer();const current=++generation;controller=new AbortController();busy.value=true;error.value='';result.value=''
  try{
    const value=await props.analyze({action:action.value,text:text.value,image:action.value==='generate'?'':await image(),instruction:instruction.value},controller.signal)
    if(disposed||current!==generation||props.readOnly)return
    if(typeof value!=='string'||!value.trim()||value.length>64000)throw Error(t('模型没有返回完整有效的文字。','The model returned no complete text.'))
    result.value=value
  }catch(cause){if(!disposed&&current===generation)error.value=cause instanceof Error?cause.message:String(cause)}
  finally{if(current===generation){busy.value=false;controller=undefined}}
}
function writeText(value:string){
  const next=emptyHandwriting('text');next.width=ink.value.width;next.height=ink.value.height
  const fitted=fitHandwrittenText(value,next)
  if(!fitted){error.value=t('文字超出最大纸面，请分成多个节点；原文未截断。','The text exceeds the maximum page. Split it into several nodes; the source was not truncated.');return}
  apply('handwrite',value,fitted)
}
onMounted(()=>{previousFocus=document.activeElement as HTMLElement;dialog.value?.querySelector<HTMLButtonElement>('header button')?.focus();document.addEventListener('visibilitychange',visibility);if(typeof ResizeObserver!=='undefined'&&surface.value){resizeObserver=new ResizeObserver(()=>cancelPointer());resizeObserver.observe(surface.value)}})
onBeforeUnmount(()=>{disposed=true;cancelPointer();cancelAi();resizeObserver?.disconnect();document.removeEventListener('visibilitychange',visibility);if(dialog.value?.contains(document.activeElement)&&previousFocus?.isConnected)previousFocus.focus()})
defineExpose({close,cancel:cancelPointer})
</script>
<template>
<div ref="dialog" class="ink-dialog" role="dialog" aria-modal="true" :aria-label="t('手写画布','Handwriting canvas')" @keydown.esc.stop.prevent="close" @keydown.tab="trapTab" @pointerdown.stop @keydown.stop>
  <section class="ink-panel">
    <header><h2>{{ t('手写画布','Handwriting canvas') }}</h2><button type="button" @click="close">{{ t('关闭','Close') }}</button></header>
    <div class="ink-toolbar">
      <template v-if="ink.mode==='ink'"><button type="button" :aria-pressed="!eraser" @click="eraser=false">{{ t('画笔','Pen') }}</button><button type="button" :aria-pressed="eraser" @click="eraser=true">{{ t('笔画橡皮','Stroke eraser') }}</button><label>{{ t('墨色','Ink') }}<input v-model="color" type="color"/></label><label>{{ t('笔宽','Width') }}<input v-model.number="width" type="range" min="1" max="12"/></label><label><input v-model="penOnly" type="checkbox"/>{{ t('仅手写笔','Stylus only') }}</label><button :disabled="!past.length||readOnly||busy" @click="undo">{{ t('撤销笔画','Undo stroke') }}</button><button :disabled="!future.length||readOnly||busy" @click="redo">{{ t('重做','Redo') }}</button></template>
      <template v-else><label>{{ t('字号','Size') }}<input v-model.number="ink.fontSize" type="range" min="12" max="48" @input="dirty=true"/></label><label>{{ t('行距','Line spacing') }}<input v-model.number="ink.lineHeight" type="range" min="1" max="3" step="0.1" @input="dirty=true"/></label><label>{{ t('字距','Letter spacing') }}<input v-model.number="ink.spacing" type="range" min="0" max="12" @input="dirty=true"/></label><label>{{ t('自然变化','Variation') }}<input v-model.number="ink.variation" type="range" min="0" max="0.3" step="0.01" @input="dirty=true"/></label></template>
      <label>{{ t('纸张','Paper') }}<select v-model="ink.paper" @change="dirty=true"><option value="plain">{{ t('空白','Plain') }}</option><option value="ruled">{{ t('横线','Ruled') }}</option><option value="grid">{{ t('方格','Grid') }}</option></select></label>
    </div>
    <div class="ink-columns">
      <div class="ink-paper-wrap"><div ref="surface" class="ink-surface" :style="{aspectRatio:`${ink.width}/${ink.height}`}" :aria-label="t('手写区域','Drawing surface')" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="cancelPointer" @lostpointercapture="cancelPointer" @contextmenu.prevent><HandwritingView :ink="ink" :text="text" :draft="draft" :draft-color="color"/></div><p>{{ t('双指触摸会取消当前笔画；支持触笔压感。原笔迹不会被识别结果覆盖。','A second touch cancels the current stroke. Stylus pressure is supported. Recognition preserves the original ink.') }}</p></div>
      <div class="ink-content">
        <label>{{ ink.mode==='ink'?t('转写或原文（可校对）','Transcript (editable)'):t('原始文字','Source text') }}<textarea v-model="text" :readonly="readOnly||busy" rows="6" maxlength="32000"/></label>
        <div class="ink-actions"><button :disabled="!text.trim()||readOnly||busy" @click="apply('text',text)">{{ t('加入文字节点','Add text node') }}</button><button :disabled="!text.trim()||readOnly||busy" @click="writeText(text)">{{ t('文字转手写','Text to handwriting') }}</button><button :disabled="!text.trim()||readOnly||busy" @click="apply('markdown',text)">{{ t('另存为 Markdown','Save as Markdown') }}</button></div>
        <p v-if="overflow" role="alert">{{ t('文字超出纸面，请减小字号或拆分内容。','Text exceeds the page. Reduce the size or split the content.') }}</p>
        <section v-if="analyze" class="ink-ai"><h3>{{ t('Agent 识别与整理','Agent recognition and organization') }}</h3><slot name="model" :busy="busy" :invalidate="invalidateAi"/><select v-model="action" :disabled="busy" :aria-label="t('处理方式','Action')"><option value="recognize">{{ t('手写转文字','Recognize handwriting') }}</option><option value="summarize">{{ t('总结要点','Summarize') }}</option><option value="organize">{{ t('规整为结构化笔记','Organize notes') }}</option><option value="generate">{{ t('Agent 编写手写内容','Agent handwriting') }}</option></select><label>{{ t('补充要求','Instructions') }}<textarea v-model="instruction" rows="2" maxlength="4000" :readonly="busy"/></label><p>{{ t('仅发送此页内容到所选模型。请核对识别结果，模糊处不应当作已确认事实。','Only this page is sent to the selected model. Review its result and uncertain passages.') }}</p><button :disabled="readOnly||busy||drawing" @click="run">{{ t('生成预览','Generate preview') }}</button><button v-if="busy" @click="cancelAi">{{ t('取消请求','Cancel request') }}</button><label v-if="result">{{ t('结果预览（可修改）','Preview (editable)') }}<textarea v-model="result" rows="7" maxlength="64000"/></label><div v-if="result" class="ink-actions"><button :disabled="readOnly" @click="apply('text',result)">{{ t('加入文字节点','Add text node') }}</button><button :disabled="readOnly" @click="writeText(result)">{{ t('加入手写节点','Add handwriting node') }}</button><button :disabled="readOnly" @click="apply('markdown',result)">{{ t('另存为 Markdown','Save as Markdown') }}</button></div></section>
        <p v-else>{{ t('可在 PC 的同一画布上调用 Agent 识别与整理；本页笔迹和文字可离线保存。','Open this canvas on PC for Agent recognition. Ink and text can be saved offline.') }}</p>
        <p v-if="error" role="alert">{{ error }}</p>
      </div>
    </div>
    <footer><template v-if="discard"><span>{{ t('放弃本次尚未保存的修改？','Discard unsaved changes?') }}</span><button @click="emit('close')">{{ t('放弃修改','Discard') }}</button><button @click="discard=false">{{ t('继续编辑','Keep editing') }}</button></template><button :disabled="readOnly||busy||drawing||overflow" @click="save">{{ t('保存手写节点','Save handwriting node') }}</button></footer>
  </section>
</div>
</template>
<style scoped>
.ink-dialog{position:fixed;inset:0;z-index:1000;background:var(--color-background-overlay);display:grid;place-items:center;padding:12px;color:var(--color-text-primary)}.ink-panel{width:min(1300px,100%);max-height:95dvh;display:flex;flex-direction:column;background:var(--color-surface-primary);border-radius:12px;overflow:hidden}.ink-panel header,.ink-panel footer,.ink-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:12px;border-bottom:1px solid var(--color-border-default)}h2,h3{margin:0;font-size:17px}.ink-panel header{justify-content:space-between}.ink-panel footer{justify-content:flex-end}.ink-columns{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(260px,1fr);overflow:auto;gap:16px;padding:14px;min-height:0}.ink-paper-wrap{min-width:0}.ink-surface{width:100%;touch-action:none;user-select:none;overflow:hidden;border:1px solid var(--color-border-default);cursor:crosshair}.ink-content{display:grid;align-content:start;gap:12px}.ink-content label{display:grid;gap:6px}.ink-panel textarea{width:100%;box-sizing:border-box;resize:vertical;min-height:64px}.ink-panel textarea,.ink-panel select{background:var(--color-background-primary);color:inherit;border:1px solid var(--color-border-default);border-radius:6px;padding:8px}.ink-panel button{min-height:40px;padding:6px 12px;border:1px solid var(--color-border-default);border-radius:6px;background:var(--color-background-primary);color:inherit}.ink-panel button[aria-pressed=true]{outline:2px solid var(--color-border-focus)}.ink-panel button:disabled{opacity:.45}.ink-toolbar label{display:flex;align-items:center;gap:5px}.ink-toolbar input[type=range]{width:80px}.ink-actions{display:flex;flex-wrap:wrap;gap:6px}.ink-ai{display:grid;gap:8px;padding-top:12px;border-top:1px solid var(--color-border-default)}p{font-size:12px;color:var(--color-text-secondary);margin:4px 0}p[role=alert]{color:var(--color-error)}@media(max-width:720px){.ink-columns{grid-template-columns:1fr}.ink-dialog{padding:0}.ink-panel{max-height:100dvh;height:100dvh;border-radius:0}.ink-toolbar button{min-width:44px;min-height:44px}}
</style>
