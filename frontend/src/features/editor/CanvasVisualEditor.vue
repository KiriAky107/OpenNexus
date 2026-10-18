<script setup lang="ts">
import CanvasScene from './CanvasScene.vue'
import CanvasInspector from './CanvasInspector.vue'
import CanvasProperties from './CanvasProperties.vue'
import CanvasToolbar from './CanvasToolbar.vue'
import { computed,ref,shallowRef,watch,onMounted,onBeforeUnmount,nextTick } from 'vue'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSettingsStore } from '@/stores/settings'
import { CanvasModel,nodeBounds,nodeLabel,movementIds,type CanvasNode,type CanvasSide } from './canvasModel'
import { CanvasGeometry,intersects } from './canvasGeometry'
import { CanvasPreviewCache } from './canvasPreviewCache'
import { contentHash,isDesktop } from '@/services/platform/desktop'
import { documentPaths } from '@/services/referenceImpact'
import { loadWorkspaceImage,readFileContent } from '@/services/workspaceService'
import { resolveCanvasFile,workspaceDocumentType } from '@/services/workspaceDocuments'
import { navigateMarkdownHref } from '@/services/markdownLinkService'
import { folderNoteSummary } from '@/features/workspace/folderSummary'
import { registerEditorCommands } from '@/services/editorCommandService'
import AppDialog from '@/components/common/AppDialog.vue'
import { t } from '@/i18n'

const editor=useEditorStore(),workspace=useWorkspaceStore(),settings=useSettingsStore()
const layout=useLayoutPreferencesStore()
const sidebar=computed({get:()=>layout.canvasInspectorVisible,set:value=>{layout.canvasInspectorVisible=value}})
let fittedAll=false,viewReady=false
const model=new CanvasModel(editor.content),document=shallowRef(model.document),historyRevision=ref(0)
const scene=ref<InstanceType<typeof CanvasScene>>()
const viewport=computed(()=>scene.value?.element),viewportSize=ref({width:800,height:600}),pan=ref({x:60,y:60}),zoom=ref(1)
const selected=ref<string[]>([]),selectedEdge=ref(''),error=ref(''),navigationError=ref('')
const isCurrentDocument=editor.captureDocument()
const readOnly=computed(()=>!isCurrentDocument()||['conflict','external_changed'].includes(editor.saveStatus))
const canUndo=computed(()=>{void historyRevision.value;return model.canUndo}),canRedo=computed(()=>{void historyRevision.value;return model.canRedo})
const graph=computed(()=>new CanvasGeometry(document.value)),selectedSet=computed(()=>new Set(selected.value))
const picked=computed(()=>graph.value.nodes.get(selected.value[0]??'')),edge=computed(()=>graph.value.edges.get(selectedEdge.value))
const draft=ref({value:'',x:0,y:0,width:300,height:180,color:'',subpath:''}),edgeDraft=ref({label:'',fromSide:'right' as CanvasSide,toSide:'left' as CanvasSide,fromEnd:'none' as 'none'|'arrow',toEnd:'arrow' as 'none'|'arrow'})
const creating=ref<'file'|'link'|null>(null),newValue=ref('')
const paths=computed(()=>documentPaths(workspace.fileTree)),fileOptions=computed(()=>paths.value.filter(path=>['markdown','canvas','image'].includes(workspaceDocumentType(path))))
let disposed=false,space=false,clipboard='',resizeObserver:ResizeObserver|undefined,disposeCommands:(()=>void)|undefined,previewGeneration=0
const previews=ref<Record<string,{text?:string;url?:string}>>({}),previewCache=new CanvasPreviewCache()
let previousPreviewKeys=new Map<string,string>()
const unknownRevisions=new WeakMap<object,number>();let unknownRevision=0
const gesture=ref<{kind:'move'|'resize'|'pan'|'box';startX:number;startY:number;x:number;y:number;ids:string[];width?:number;height?:number;add?:boolean}>()
const delta=computed(()=>gesture.value?{x:(gesture.value.x-gesture.value.startX)/zoom.value,y:(gesture.value.y-gesture.value.startY)/zoom.value}:{x:0,y:0})
const moved=computed(()=>{
  const active=gesture.value,overrides=new Map<string,CanvasNode>()
  if(active?.kind!=='move'&&active?.kind!=='resize')return overrides
  for(const id of active.ids){const node=graph.value.nodes.get(id);if(!node)continue
    overrides.set(id,active.kind==='move'?{...node,x:node.x+delta.value.x,y:node.y+delta.value.y}:{...node,width:Math.max(80,node.width+delta.value.x),height:Math.max(60,node.height+delta.value.y)})
  }
  return overrides
})
const displayedNodes=computed(()=>moved.value.size?document.value.nodes.map(node=>moved.value.get(node.id)??node):document.value.nodes)
const visibleNodes=computed(()=>displayedNodes.value.filter(node=>selectedSet.value.has(node.id)||(node.x+node.width)*zoom.value+pan.value.x>=-100&&node.x*zoom.value+pan.value.x<=viewportSize.value.width+100&&(node.y+node.height)*zoom.value+pan.value.y>=-100&&node.y*zoom.value+pan.value.y<=viewportSize.value.height+100))
const edges=computed(()=>{
  const view={left:(-pan.value.x-100)/zoom.value,right:(viewportSize.value.width-pan.value.x+100)/zoom.value,top:(-pan.value.y-100)/zoom.value,bottom:(viewportSize.value.height-pan.value.y+100)/zoom.value}
  return graph.value.project(moved.value).filter(({edge,geometry})=>geometry&&(edge.id===selectedEdge.value||selectedSet.value.has(edge.fromNode)||selectedSet.value.has(edge.toNode)||intersects(geometry.bounds,view)))
})
const box=computed(()=>{
  const active=gesture.value;if(active?.kind!=='box')return null
  return{x:Math.min(active.startX,active.x),y:Math.min(active.startY,active.y),width:Math.abs(active.x-active.startX),height:Math.abs(active.y-active.startY)}
})
const viewKey=()=>`canvas-view:${workspace.vaultId}:${editor.currentFilePath}`
function rememberView(){try{localStorage.setItem(viewKey(),JSON.stringify({pan:pan.value,zoom:zoom.value}))}catch{/* session state */}}
function sync(){if(!isCurrentDocument())return;document.value=model.document;historyRevision.value++;editor.updateContent(model.source);editor.scheduleAutoSave(settings.autoSaveInterval);selected.value=selected.value.filter(id=>model.document.nodes.some(node=>node.id===id));if(!model.document.edges.some(edge=>edge.id===selectedEdge.value))selectedEdge.value=''}
function change(action:()=>unknown){if(readOnly.value){error.value=t('请先处理文件冲突。','Resolve the file conflict first.');return false}try{const before=model.source;action();const changed=model.source!==before;if(changed)sync();error.value='';return changed}catch(cause){error.value=String(cause);return false}}
function select(id:string,extend=false){selectedEdge.value='';selected.value=extend?(selected.value.includes(id)?selected.value.filter(value=>value!==id):[...selected.value,id]):[id]}
watch(picked,node=>{if(node)draft.value={value:String(node.text??node.file??node.url??node.label??''),x:node.x,y:node.y,width:node.width,height:node.height,color:typeof node.color==='string'?node.color:'',subpath:typeof node.subpath==='string'?node.subpath:''}},{immediate:true})
watch(edge,value=>{if(value)edgeDraft.value={label:value.label??'',fromSide:value.fromSide??'right',toSide:value.toSide??'left',fromEnd:value.fromEnd??'none',toEnd:value.toEnd??'arrow'}},{immediate:true})
function propertyInput(key:string,event:Event,edge=false){const raw=(event.target as HTMLInputElement).value;if(edge)(edgeDraft.value as Record<string,unknown>)[key]=raw;else(draft.value as Record<string,unknown>)[key]=['x','y','width','height'].includes(key)&&raw!==''?Number(raw):raw}
function applyNode(){const node=picked.value;if(!node)return;const key={text:'text',file:'file',link:'url',group:'label'}[node.type];const fields:Partial<CanvasNode>={x:Math.round(Number(draft.value.x)),y:Math.round(Number(draft.value.y)),width:Math.round(Number(draft.value.width)),height:Math.round(Number(draft.value.height)),color:draft.value.color||undefined,[key]:draft.value.value};if(node.type==='file'){fields.subpath=draft.value.subpath||undefined;if(fields.subpath&&!String(fields.subpath).startsWith('#')){error.value='子路径必须以 # 开头。';return}}change(()=>model.updateNode(node.id,fields))}
function applyEdge(){if(edge.value)change(()=>model.updateEdge(edge.value!.id,{...edgeDraft.value}))}
function centerPoint(){return{x:(viewportSize.value.width/2-pan.value.x)/zoom.value-150,y:(viewportSize.value.height/2-pan.value.y)/zoom.value-90}}
function add(type:CanvasNode['type'],value=''){const point=centerPoint();let id='';if(change(()=>{id=model.add(type,point.x,point.y,value)}))select(id)}
function beginAdd(type:'file'|'link'){creating.value=type;newValue.value=type==='file'?fileOptions.value[0]?.slice(1)??'':'https://';error.value=''}
function confirmAdd(){if(!creating.value)return;if(creating.value==='file'&&!paths.value.includes(`/${newValue.value}`)){error.value='请选择当前知识库内的文件。';return}const point=centerPoint();let id='';if(change(()=>{id=model.add(creating.value!,point.x,point.y,newValue.value)})){creating.value=null;select(id)}}
function connect(){if(selected.value.length===2){let id='';if(change(()=>{id=model.connect(selected.value[0]!,selected.value[1]!)})){selectedEdge.value=id;selected.value=[]}}}
function group(){let id:string|undefined;if(change(()=>{id=model.group(selected.value)})&&id)select(id)}
function remove(){change(()=>model.remove(selected.value,selectedEdge.value))}
function copy(){clipboard=model.copy(selected.value);return clipboard}
function paste(value=clipboard){if(!value)return;let ids:string[]=[];if(change(()=>{ids=model.paste(value)})){selected.value=ids;selectedEdge.value=''}}
function onCopy(event:ClipboardEvent){if((event.target as HTMLElement).closest('input,textarea,select')||!selected.value.length)return;event.preventDefault();event.clipboardData?.setData('text/plain',copy())}
function onPaste(event:ClipboardEvent){if((event.target as HTMLElement).closest('input,textarea,select'))return;const content=event.clipboardData?.getData('text/plain')??'';if(!content.includes('opennexus_canvas'))return;event.preventDefault();paste(content)}
function fit(ids?:string[]){fittedAll=!ids;const nodes=ids?document.value.nodes.filter(node=>ids.includes(node.id)):document.value.nodes;const bounds=nodeBounds(nodes);zoom.value=Math.max(.1,Math.min(1.5,(viewportSize.value.width-64)/Math.max(100,bounds.width),(viewportSize.value.height-64)/Math.max(100,bounds.height)));pan.value={x:(viewportSize.value.width-bounds.width*zoom.value)/2-bounds.x*zoom.value,y:(viewportSize.value.height-bounds.height*zoom.value)/2-bounds.y*zoom.value};rememberView()}
function magnify(factor:number,x=viewportSize.value.width/2,y=viewportSize.value.height/2){fittedAll=false;const next=Math.max(.1,Math.min(3,zoom.value*factor));pan.value={x:x-(x-pan.value.x)*next/zoom.value,y:y-(y-pan.value.y)*next/zoom.value};zoom.value=next;rememberView()}
function wheel(event:WheelEvent){event.preventDefault();const rect=viewport.value!.getBoundingClientRect();magnify(Math.exp(-event.deltaY*.002),event.clientX-rect.left,event.clientY-rect.top)}
function local(event:PointerEvent){const rect=viewport.value!.getBoundingClientRect();return{x:event.clientX-rect.left,y:event.clientY-rect.top}}
function begin(event:PointerEvent,node?:CanvasNode,resize=false){
  if(event.button!==0&&event.button!==1&&event.button!==2)return
  event.preventDefault();const point=local(event)
  let kind:'pan'|'box'|'move'|'resize'=space||event.button!==0?'pan':node?(resize?'resize':'move'):'box'
  let ids:string[]=[]
  if(node&&kind!=='pan'){
    if(!selected.value.includes(node.id))select(node.id,event.shiftKey)
    else if(event.shiftKey&&!resize){select(node.id,true);return}
    ids=movementIds(document.value.nodes,selected.value)
    if(resize)ids=[node.id]
  }
  if(kind==='box'&&!event.shiftKey){selected.value=[];selectedEdge.value=''}
  gesture.value={kind,startX:point.x,startY:point.y,x:point.x,y:point.y,ids,add:event.shiftKey}
  viewport.value?.setPointerCapture?.(event.pointerId)
}
let pendingPoint:{x:number;y:number}|undefined,moveFrame:number|undefined
function flushMove(){if(moveFrame!==undefined)cancelAnimationFrame(moveFrame);moveFrame=undefined;const active=gesture.value,point=pendingPoint;pendingPoint=undefined;if(!active||!point)return;if(active.kind==='pan'){fittedAll=false;pan.value={x:pan.value.x+point.x-active.x,y:pan.value.y+point.y-active.y}}active.x=point.x;active.y=point.y}
function cancelGesture(){if(moveFrame!==undefined)cancelAnimationFrame(moveFrame);moveFrame=undefined;pendingPoint=undefined;gesture.value=undefined}
function move(event:PointerEvent){if(!gesture.value)return;pendingPoint=local(event);if(moveFrame===undefined)moveFrame=requestAnimationFrame(flushMove)}
function end(event:PointerEvent){flushMove();const active=gesture.value;if(!active)return;const offset={...delta.value},rectangle=box.value
  gesture.value=undefined;viewport.value?.releasePointerCapture?.(event.pointerId)
  if(active.kind==='move'&&(Math.abs(offset.x)>1||Math.abs(offset.y)>1))change(()=>model.move(active.ids,offset.x,offset.y))
  if(active.kind==='resize'){const node=document.value.nodes.find(node=>node.id===active.ids[0]);if(node)change(()=>model.updateNode(node.id,{width:Math.round(Math.max(80,node.width+offset.x)),height:Math.round(Math.max(60,node.height+offset.y))}))}
  if(active.kind==='box'&&rectangle){const x=(rectangle.x-pan.value.x)/zoom.value,y=(rectangle.y-pan.value.y)/zoom.value,w=rectangle.width/zoom.value,h=rectangle.height/zoom.value;const matches=document.value.nodes.filter(node=>node.x+node.width>=x&&node.x<=x+w&&node.y+node.height>=y&&node.y<=y+h).map(node=>node.id);selected.value=[...new Set([...(active.add?selected.value:[]),...matches])]}
  rememberView()
}
function keydown(event:KeyboardEvent){
  if((event.target as HTMLElement).closest('input,textarea,select')||event.isComposing)return
  const key=event.key.toLowerCase(),modifier=event.ctrlKey||event.metaKey
  if(key===' '){if((event.target as HTMLElement).closest('button'))return;space=true;event.preventDefault();return}
  if(key==='escape'){cancelGesture();selected.value=[];selectedEdge.value='';return}
  if(modifier&&key==='a'){selected.value=document.value.nodes.map(node=>node.id);selectedEdge.value='';event.preventDefault();return}
  if(modifier&&key==='z'){event.preventDefault();change(()=>event.shiftKey?model.redo():model.undo());return}
  if(modifier&&key==='y'){event.preventDefault();change(()=>model.redo());return}
  if(modifier&&key==='d'){event.preventDefault();paste(copy());return}
  if(key==='delete'||key==='backspace'){event.preventDefault();remove();return}
  if(key==='home'){event.preventDefault();fit();return}
  const movement:Record<string,[number,number]>={arrowleft:[-1,0],arrowright:[1,0],arrowup:[0,-1],arrowdown:[0,1]}
  if(movement[key]&&selected.value.length){event.preventDefault();const[dx,dy]=movement[key];change(()=>model.move(selected.value,dx*(event.shiftKey?1:10),dy*(event.shiftKey?1:10)))}
}
function locate(id:string){
  if(!model.document.nodes.some(node=>node.id===id)){
    selected.value=[];selectedEdge.value='';navigationError.value=t('画布节点已不存在，请重新检查引用。','The canvas node no longer exists. Recheck references.');return
  }
  navigationError.value='';select(id);sidebar.value=true;void nextTick(()=>{fit([id]);viewport.value?.focus()})
}
watch(()=>editor.canvasNodeRequest,request=>{if(request?.path===editor.currentFilePath)locate(request.nodeId)},{immediate:true})
async function open(node:CanvasNode){
  try{
    if(node.type==='link'){await navigateMarkdownHref(node.url!);return}
    if(node.type!=='file')return
    const target=resolveCanvasFile(node.file)
    if(!target||!paths.value.includes(target))throw new Error('引用目标不存在或路径不安全。')
    if(!['markdown','canvas'].includes(workspaceDocumentType(target)))return
    await navigateMarkdownHref(target.split('/').map(encodeURIComponent).join('/')+(typeof node.subpath==='string'?node.subpath:''))
  }catch(cause){error.value=String(cause)}
}
const imagePath=(node:CanvasNode)=>node.type==='group'?node.background:node.file
const treeFiles=computed(()=>{
  const indexed=new Map<string,typeof workspace.fileTree[number]>(),queue=[...workspace.fileTree]
  for(let index=0;index<queue.length;index++){const node=queue[index]!;indexed.set(node.path,node);queue.push(...(node.children??[]))}
  return indexed
})
const previewSources=computed(()=>[...new Set(visibleNodes.value.map(node=>imagePath(node)).filter((path):path is string=>typeof path==='string'))].map(raw=>{
  const target=resolveCanvasFile(raw),entry=target?treeFiles.value.get(target):undefined
  let revision=entry?.content_hash
  if(entry&&!revision){if(!unknownRevisions.has(entry))unknownRevisions.set(entry,++unknownRevision);revision=`unverified:${unknownRevisions.get(entry)}`}
  return {raw,path:target??raw,valid:Boolean(target&&entry?.type==='file'),revision,vault:workspace.vaultId,
    key:JSON.stringify([workspace.vaultId,target,revision??'missing']),type:target?workspaceDocumentType(target):'unsupported'}
}))
watch(()=>JSON.stringify(previewSources.value),async()=>{
  const version=++previewGeneration,vault=workspace.vaultId,sources=previewSources.value,next:typeof previews.value={}
  const allowed=sources.slice(0,previewCache.maxEntries),keys=new Map(allowed.map(source=>[source.raw,source.key]))
  for(const source of sources){
    if(!source.valid)next[source.raw]={text:t('引用目标缺失','Missing target')}
    else if(!keys.has(source.raw))next[source.raw]={text:t('预览数量较多，请放大画布后查看。','Zoom in to view more previews.')}
    else if(previousPreviewKeys.get(source.raw)===source.key&&previews.value[source.raw])next[source.raw]=previews.value[source.raw]!
  }
  previews.value=next;previousPreviewKeys=keys
  previewCache.configure(vault,allowed)
  for(let offset=0;offset<allowed.length;offset+=6){
    await Promise.all(allowed.slice(offset,offset+6).map(async source=>{
      if(!source.valid)return
      try{
        const value=await previewCache.read(source,async()=>{
          if(source.type==='image'){
            const blob=await loadWorkspaceImage(source.path.slice(1))
            if(isDesktop()&&/^[0-9a-f]{64}$/i.test(source.revision??'')){
              const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')
              if(hash!==source.revision)throw new Error(t('引用目标已变化，请刷新后重试。','The target changed. Refresh and try again.'))
            }
            return{blob}
          }
          if(source.type==='markdown'){
            const content=await readFileContent(source.path)
            if(isDesktop()&&/^[0-9a-f]{64}$/i.test(source.revision??'')&&await contentHash(content)!==source.revision)throw new Error(t('引用目标已变化，请刷新后重试。','The target changed. Refresh and try again.'))
            return{text:folderNoteSummary(source.path,content).summary}
          }
          return{text:source.type==='canvas'?t('结构化画布','Structured canvas'):t('暂不支持预览此格式','Preview unavailable for this format')}
        })
        if(value&&!disposed&&version===previewGeneration&&vault===workspace.vaultId)next[source.raw]=value.limited?{text:t('预览超过当前容量，请放大画布后查看。','Zoom in to view previews within the available capacity.')}:value
      }catch(cause){if(!disposed&&version===previewGeneration&&vault===workspace.vaultId){next[source.raw]={text:t('无法读取引用目标','Unable to read target')};error.value=String(cause)}}
    }))
    if(disposed||version!==previewGeneration||vault!==workspace.vaultId)return
    previews.value={...next}
  }
},{immediate:true})
onMounted(()=>{
  const measure=()=>{if(!viewport.value)return;const size={width:viewport.value.clientWidth||800,height:viewport.value.clientHeight||600},previous=viewportSize.value;viewportSize.value=size;if(!viewReady)return;if(fittedAll)fit();else{pan.value={x:pan.value.x+(size.width-previous.width)/2,y:pan.value.y+(size.height-previous.height)/2};rememberView()}}
  measure();if(typeof ResizeObserver!=='undefined'){resizeObserver=new ResizeObserver(measure);resizeObserver.observe(viewport.value!)}
  let restored=false;try{const saved=JSON.parse(localStorage.getItem(viewKey())??'null');if(saved&&Number.isFinite(saved.zoom)&&saved.zoom>=.1&&saved.zoom<=3&&Number.isFinite(saved.pan?.x)&&Number.isFinite(saved.pan?.y)){pan.value=saved.pan;zoom.value=saved.zoom;restored=true}}catch{/* no view state */}if(!restored)fit()
  viewReady=true
  const request=editor.canvasNodeRequest;if(request?.path===editor.currentFilePath)locate(request.nodeId)
  disposeCommands=registerEditorCommands({available:()=>!disposed&&!readOnly.value,handlers:{'editor.undo':()=>change(()=>model.undo())?{ok:true}:{ok:false,reason:'unavailable'},'editor.redo':()=>change(()=>model.redo())?{ok:true}:{ok:false,reason:'unavailable'}}})
})
onBeforeUnmount(()=>{disposed=true;previewGeneration++;cancelGesture();resizeObserver?.disconnect();disposeCommands?.();previewCache.dispose()})
</script>

<template>
  <div class="canvas-visual" @keydown="keydown" @keyup.space="space=false" @copy="onCopy" @paste="onPaste">
    <CanvasToolbar :read-only="readOnly" :selection-count="selected.length" :edge-selected="Boolean(selectedEdge)" :can-undo="canUndo" :can-redo="canRedo" :node-count="document.nodes.length" :sidebar="sidebar"
      @add-text="add('text',t('新文字','New text'))" @add-file="beginAdd('file')" @add-link="beginAdd('link')" @group="selected.length?group():add('group',t('分组','Group'))" @connect="connect" @undo="change(()=>model.undo())" @redo="change(()=>model.redo())" @copy="copy" @paste="paste()" @remove="remove" @arrange="change(()=>model.mindMap(selected[0]))&&fit()" @toggle-inspector="sidebar=!sidebar"><template #tools><slot name="tools" /></template></CanvasToolbar>
    <p v-if="navigationError || error" class="error-banner canvas-error" role="alert">{{ navigationError || error }}</p>
    <div class="canvas-body">
      <CanvasScene ref="scene" :nodes="visibleNodes" :node-lookup="graph.nodes" :node-count="document.nodes.length" :edges="edges" :order="graph.order" :pan="pan" :zoom="zoom" :selected="selected" :selected-edge="selectedEdge" :previews="previews" :read-only="readOnly"
        :label="t('可编辑画布：Shift 多选，空格拖动平移，方向键移动节点，Home 显示全部','Editable canvas: Shift selects multiple nodes, Space-drag pans, arrow keys move nodes, Home fits all')" :empty-label="t('添加文字、笔记、图片或网址开始构建画布。','Add text, notes, images or URLs to start your canvas.')"
        @pointerdown.self="begin($event)" @pointermove="move" @pointerup="end" @pointercancel="cancelGesture" @wheel="wheel" @contextmenu.prevent @blur="space=false"
        @node-pointer="(event,node)=>begin(event,node)" @resize-pointer="(event,node)=>begin(event,node,true)" @locate="locate" @open="open" @select-edge="id=>{selectedEdge=id;selected=[]}">
        <div v-if="box" class="canvas-selection-box" :style="{left:`${box.x}px`,top:`${box.y}px`,width:`${box.width}px`,height:`${box.height}px`}" />
      </CanvasScene>
      <CanvasInspector v-if="sidebar" :nodes="document.nodes" :edges="document.edges" :selected="selected" @close="sidebar=false" @locate="locate" @select-edge="id=>{selectedEdge=id;selected=[]}">
        <CanvasProperties :node="picked" :edge="edge" :node-values="draft" :edge-values="edgeDraft" :selection-count="selected.length" :read-only="readOnly" @node-input="(key,event)=>propertyInput(key,event)" @node-composition-end="(key,event)=>propertyInput(key,event)" @edge-input="(key,event)=>propertyInput(key,event,true)" @edge-composition-end="(key,event)=>propertyInput(key,event,true)" @apply-node="applyNode" @apply-edge="applyEdge" />
      </CanvasInspector>
    </div>
    <footer class="canvas-status"><span>{{ document.nodes.length }} {{ t('个节点','nodes') }} · {{ document.edges.length }} {{ t('条连接','connections') }} · {{ selected.length }} {{ t('已选择','selected') }}</span><div class="inline-actions"><button class="button-secondary" :aria-label="t('缩小画布','Zoom out canvas')" @click="magnify(1/1.2)">−</button><span>{{ Math.round(zoom*100) }}%</span><button class="button-secondary" :aria-label="t('放大画布','Zoom in canvas')" @click="magnify(1.2)">＋</button><button class="button-secondary" @click="fit()">{{ t('显示全部','Fit all') }}</button></div></footer>
    <AppDialog v-if="creating" :label="creating==='file'?t('添加知识库文件','Add vault file'):t('添加网址','Add URL')" @close="creating=null"><form class="canvas-create" @submit.prevent="confirmAdd"><h2>{{ creating==='file'?t('添加知识库文件','Add vault file'):t('添加网址','Add URL') }}</h2><select v-if="creating==='file'" v-model="newValue" class="select" :aria-label="t('选择知识库文件','Choose vault file')"><option v-for="path in fileOptions" :key="path" :value="path.slice(1)">{{ path }}</option></select><input v-else v-model="newValue" class="input" type="url" required :aria-label="t('节点网址','Node URL')"/><p v-if="error" class="error-banner" role="alert">{{ error }}</p><div class="inline-actions"><button class="button-primary" :disabled="!newValue||readOnly">{{ t('添加节点','Add node') }}</button><button type="button" class="button-secondary" @click="creating=null">{{ t('取消','Cancel') }}</button></div></form></AppDialog>
  </div>
</template>

<style scoped>
.canvas-group-background{position:absolute;inset:0;z-index:-1;pointer-events:none}
.canvas-visual{display:flex;flex:1;min-height:0;min-width:0;flex-direction:column;color:var(--color-text-primary)}.canvas-body{display:flex;flex:1;min-height:0;min-width:0}.canvas-selection-box{position:absolute;border:1px solid var(--color-border-focus);background:var(--color-accent-soft);opacity:.65;pointer-events:none}.canvas-status{display:flex;justify-content:space-between;align-items:center;gap:var(--space-sm);padding:var(--space-sm);border-top:1px solid var(--color-border-default);font-size:var(--font-size-xs);flex-wrap:wrap}.canvas-create{display:grid;gap:var(--space-lg);width:min(520px,100%)}.canvas-error{margin:0;padding:var(--space-sm)}@media(max-width:640px){.canvas-body{flex-direction:column}}
.canvas-status { min-height: 40px; padding: 4px 12px; background: var(--color-surface-primary); }
.canvas-status button { min-width: 28px; height: 28px; padding: 3px 8px; }
@media (max-width: 640px) { }
</style>
