<script setup lang="ts">
import { computed,ref,shallowRef,watch,onMounted,onBeforeUnmount,nextTick } from 'vue'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import ControlIcon from '@/components/common/ControlIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSettingsStore } from '@/stores/settings'
import { CanvasModel,nodeBounds,nodeLabel,movementIds,type CanvasNode,type CanvasSide } from './canvasModel'
import { CanvasGeometry,intersects } from './canvasGeometry'
import { CanvasPreviewCache } from './canvasPreviewCache'
import { contentHash,isDesktop } from '@/services/platform/desktop'
import { documentPaths } from '@/services/referenceImpact'
import { loadWorkspaceImage,readFileContent } from '@/services/workspaceService'
import { workspaceDocumentType } from '@/services/workspaceDocuments'
import { resolveVaultReference } from '@/services/vaultReferences'
import { navigateMarkdownHref } from '@/services/markdownLinkService'
import { folderNoteSummary } from '@/features/workspace/folderSummary'
import { registerEditorCommands } from '@/services/editorCommandService'
import AppDialog from '@/components/common/AppDialog.vue'
import { t } from '@/i18n'

const editor=useEditorStore(),workspace=useWorkspaceStore(),settings=useSettingsStore()
const layout=useLayoutPreferencesStore()
const sidebar=computed({get:()=>layout.canvasInspectorVisible,set:value=>{layout.canvasInspectorVisible=value}})
const toolsOpen=ref(false),toolsMenu=ref<HTMLElement>(),toolsTrigger=ref<HTMLButtonElement>()
let fittedAll=false,viewReady=false
const model=new CanvasModel(editor.content),document=shallowRef(model.document),historyRevision=ref(0)
const viewport=ref<HTMLElement>(),viewportSize=ref({width:800,height:600}),pan=ref({x:60,y:60}),zoom=ref(1)
const selected=ref<string[]>([]),selectedEdge=ref(''),error=ref(''),nodeFilter=ref(''),nodeCount=ref(100)
const readOnly=computed(()=>['conflict','external_changed'].includes(editor.saveStatus))
const canUndo=computed(()=>{void historyRevision.value;return model.canUndo}),canRedo=computed(()=>{void historyRevision.value;return model.canRedo})
const graph=computed(()=>new CanvasGeometry(document.value)),selectedSet=computed(()=>new Set(selected.value))
const picked=computed(()=>graph.value.nodes.get(selected.value[0]??'')),edge=computed(()=>graph.value.edges.get(selectedEdge.value))
const draft=ref({value:'',x:0,y:0,width:300,height:180,color:'',subpath:''}),edgeDraft=ref({label:'',fromSide:'right' as CanvasSide,toSide:'left' as CanvasSide,fromEnd:'none' as 'none'|'arrow',toEnd:'arrow' as 'none'|'arrow'})
const creating=ref<'file'|'link'|null>(null),newValue=ref('')
const paths=computed(()=>documentPaths(workspace.fileTree)),fileOptions=computed(()=>paths.value.filter(path=>['markdown','canvas','image'].includes(workspaceDocumentType(path))))
const filteredNodes=computed(()=>document.value.nodes.filter(node=>`${node.text??''} ${node.file??''} ${node.url??''} ${node.label??''}`.toLowerCase().includes(nodeFilter.value.trim().toLowerCase())))
const listed=computed(()=>filteredNodes.value.slice(0,nodeCount.value))
watch(nodeFilter,()=>{nodeCount.value=100})
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
function sync(){document.value=model.document;historyRevision.value++;editor.updateContent(model.source);editor.scheduleAutoSave(settings.autoSaveInterval);selected.value=selected.value.filter(id=>model.document.nodes.some(node=>node.id===id));if(!model.document.edges.some(edge=>edge.id===selectedEdge.value))selectedEdge.value=''}
function change(action:()=>unknown){if(readOnly.value){error.value=t('请先处理文件冲突。','Resolve the file conflict first.');return false}try{const before=model.source;action();const changed=model.source!==before;if(changed)sync();error.value='';return changed}catch(cause){error.value=String(cause);return false}}
function select(id:string,extend=false){selectedEdge.value='';selected.value=extend?(selected.value.includes(id)?selected.value.filter(value=>value!==id):[...selected.value,id]):[id]}
watch(picked,node=>{if(node)draft.value={value:String(node.text??node.file??node.url??node.label??''),x:node.x,y:node.y,width:node.width,height:node.height,color:typeof node.color==='string'?node.color:'',subpath:typeof node.subpath==='string'?node.subpath:''}},{immediate:true})
watch(edge,value=>{if(value)edgeDraft.value={label:value.label??'',fromSide:value.fromSide??'right',toSide:value.toSide??'left',fromEnd:value.fromEnd??'none',toEnd:value.toEnd??'arrow'}},{immediate:true})
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
function locate(id:string){select(id);sidebar.value=true;void nextTick(()=>{fit([id]);viewport.value?.focus()})}
function closeTools(event:PointerEvent){if(!toolsMenu.value?.contains(event.target as Node))toolsOpen.value=false}
async function toggleTools(){toolsOpen.value=!toolsOpen.value;if(toolsOpen.value){await nextTick();toolsMenu.value?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus()}}
function toolsKeys(event:KeyboardEvent){
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();toolsOpen.value=false;toolsTrigger.value?.focus();return}
  if(!['ArrowDown','ArrowUp','Home','End','Tab'].includes(event.key))return
  if(event.key==='Tab'){toolsOpen.value=false;return}
  event.preventDefault();event.stopPropagation();const items=[...toolsMenu.value!.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')],index=items.indexOf(event.target as HTMLButtonElement)
  items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus()
}
watch(()=>editor.canvasNodeRequest,request=>{if(request?.path===editor.currentFilePath)locate(request.nodeId)},{immediate:true})
async function open(node:CanvasNode){
  try{
    if(node.type==='link'){await navigateMarkdownHref(node.url!);return}
    if(node.type!=='file')return
    const target=resolveVaultReference(editor.currentFilePath??'/map.canvas',node.file!,true)
    if(!target||!paths.value.includes(target))throw new Error('引用目标不存在或路径不安全。')
    if(!['markdown','canvas'].includes(workspaceDocumentType(target)))return
    await navigateMarkdownHref(target+(typeof node.subpath==='string'?node.subpath:''))
  }catch(cause){error.value=String(cause)}
}
function color(value:unknown){if(typeof value!=='string')return'var(--color-border-default)';if(/^#[\da-f]{6}$/i.test(value))return value;return({'1':'var(--color-error)','2':'var(--color-warning)','3':'var(--color-accent-secondary)','4':'var(--color-success)','5':'var(--color-info)','6':'var(--color-accent-primary)'}as Record<string,string>)[value]??'var(--color-border-default)'}
const imagePath=(node:CanvasNode)=>node.type==='group'?node.background:node.file
function groupBackground(node:CanvasNode){
  const url=previews.value[node.background??'']?.url
  return{backgroundImage:url?`url("${url}")`:undefined,backgroundSize:node.backgroundStyle==='repeat'?'auto':node.backgroundStyle==='ratio'?'contain':'cover',backgroundRepeat:node.backgroundStyle==='repeat'?'repeat':'no-repeat',backgroundPosition:'center',width:'100%',height:'100%'}
}
const treeFiles=computed(()=>{
  const indexed=new Map<string,typeof workspace.fileTree[number]>(),queue=[...workspace.fileTree]
  for(let index=0;index<queue.length;index++){const node=queue[index]!;indexed.set(node.path,node);queue.push(...(node.children??[]))}
  return indexed
})
const previewSources=computed(()=>[...new Set(visibleNodes.value.map(node=>imagePath(node)).filter((path):path is string=>typeof path==='string'))].map(raw=>{
  const target=resolveVaultReference(editor.currentFilePath??'/map.canvas',raw,true),entry=target?treeFiles.value.get(target):undefined
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
  window.addEventListener('pointerdown',closeTools)
  const measure=()=>{if(!viewport.value)return;const size={width:viewport.value.clientWidth||800,height:viewport.value.clientHeight||600},previous=viewportSize.value;viewportSize.value=size;if(!viewReady)return;if(fittedAll)fit();else{pan.value={x:pan.value.x+(size.width-previous.width)/2,y:pan.value.y+(size.height-previous.height)/2};rememberView()}}
  measure();if(typeof ResizeObserver!=='undefined'){resizeObserver=new ResizeObserver(measure);resizeObserver.observe(viewport.value!)}
  let restored=false;try{const saved=JSON.parse(localStorage.getItem(viewKey())??'null');if(saved&&Number.isFinite(saved.zoom)&&saved.zoom>=.1&&saved.zoom<=3&&Number.isFinite(saved.pan?.x)&&Number.isFinite(saved.pan?.y)){pan.value=saved.pan;zoom.value=saved.zoom;restored=true}}catch{/* no view state */}if(!restored)fit()
  viewReady=true
  const request=editor.canvasNodeRequest;if(request?.path===editor.currentFilePath)locate(request.nodeId)
  disposeCommands=registerEditorCommands({available:()=>!disposed&&!readOnly.value,handlers:{'editor.undo':()=>change(()=>model.undo())?{ok:true}:{ok:false,reason:'unavailable'},'editor.redo':()=>change(()=>model.redo())?{ok:true}:{ok:false,reason:'unavailable'}}})
})
onBeforeUnmount(()=>{disposed=true;previewGeneration++;cancelGesture();window.removeEventListener('pointerdown',closeTools);resizeObserver?.disconnect();disposeCommands?.();previewCache.dispose()})
</script>

<template>
  <div class="canvas-visual" @keydown="keydown" @keyup.space="space=false" @copy="onCopy" @paste="onPaste">
    <div class="canvas-toolbar" role="toolbar" :aria-label="t('画布操作','Canvas operations')">
      <div class="canvas-tool-group">
        <button class="button-secondary" :disabled="readOnly" @click="add('text',t('新文字','New text'))">{{ t('文字','Text') }}</button><button class="button-secondary" :disabled="readOnly" @click="beginAdd('file')">{{ t('笔记／图片','Note / image') }}</button><button class="button-secondary" :disabled="readOnly" @click="beginAdd('link')">{{ t('网址','URL') }}</button><button class="button-secondary" :disabled="readOnly" @click="selected.length?group():add('group',t('分组','Group'))">{{ t('分组','Group') }}</button>
      </div>
      <div class="canvas-tool-group canvas-history-tools">
        <button class="button-secondary canvas-icon-button" :title="t('连接所选节点','Connect selected nodes')" :aria-label="t('连接所选节点','Connect selected nodes')" :disabled="readOnly||selected.length!==2" @click="connect"><ControlIcon name="link" /></button>
        <button class="button-secondary canvas-icon-button" :title="t('撤销','Undo')+' · Ctrl+Z'" :disabled="readOnly||!canUndo" @click="change(()=>model.undo())"><ControlIcon name="undo" /><span class="canvas-sr-only">{{ t('撤销','Undo') }}</span></button><button class="button-secondary canvas-icon-button" :title="t('重做','Redo')+' · Ctrl+Shift+Z'" :disabled="readOnly||!canRedo" @click="change(()=>model.redo())"><ControlIcon name="redo" /><span class="canvas-sr-only">{{ t('重做','Redo') }}</span></button>
      </div>
      <div class="canvas-view-tools">
        <div ref="toolsMenu" class="canvas-tools-menu">
          <button ref="toolsTrigger" class="button-secondary canvas-icon-button" :title="t('更多画布操作','More canvas actions')" :aria-label="t('更多画布操作','More canvas actions')" aria-haspopup="menu" :aria-expanded="toolsOpen" @click="toggleTools" @keydown.down.prevent="!toolsOpen&&toggleTools()">···</button>
          <div v-if="toolsOpen" class="canvas-tools-popover" role="menu" :aria-label="t('更多画布操作','More canvas actions')" @keydown="toolsKeys" @click="toolsOpen=false">
            <button role="menuitem" :disabled="!selected.length" @click="copy">{{ t('复制','Copy') }}</button><button role="menuitem" :disabled="readOnly" @click="paste()">{{ t('粘贴','Paste') }}</button><button role="menuitem" :disabled="readOnly||(!selected.length&&!selectedEdge)" @click="remove">{{ t('删除所选','Delete selected') }}</button>
            <span class="canvas-menu-divider" role="separator" />
            <button role="menuitem" :disabled="readOnly||!document.nodes.length" @click="change(()=>model.mindMap(selected[0]))&&fit()">{{ t('整理为思维导图','Arrange as mind map') }}</button>
            <slot name="tools" />
          </div>
        </div>
        <button class="button-secondary inspector-toggle" :aria-expanded="sidebar" :aria-label="sidebar?t('隐藏右侧栏','Hide right sidebar'):t('显示右侧栏','Show right sidebar')" :title="sidebar?t('隐藏右侧栏','Hide right sidebar'):t('显示右侧栏','Show right sidebar')" @click="sidebar=!sidebar"><ControlIcon name="panelClose" :class="{ mirrored: sidebar }" /><span>{{ t('节点与属性','Nodes and properties') }}</span></button>
      </div>
    </div>
    <p v-if="error" class="error-banner canvas-error" role="alert">{{ error }}</p>
    <div class="canvas-body">
      <div ref="viewport" class="canvas-viewport" tabindex="0" role="region" :aria-label="t('可编辑画布：Shift 多选，空格拖动平移，方向键移动节点，Home 显示全部','Editable canvas: Shift selects multiple nodes, Space-drag pans, arrow keys move nodes, Home fits all')" @pointerdown.self="begin($event)" @pointermove="move" @pointerup="end" @pointercancel="cancelGesture" @wheel="wheel" @contextmenu.prevent @blur="space=false">
        <div class="canvas-world" :style="{transform:`translate(${pan.x}px,${pan.y}px) scale(${zoom})`}">
          <svg class="canvas-edges" width="1" height="1" :style="{zIndex:document.nodes.length+1}" aria-label="节点连接"><defs><marker id="canvas-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M 0 0 L 8 4 L 0 8 z" fill="context-stroke" /></marker></defs>
            <g v-for="{edge:line,geometry} in edges" :key="line.id" v-memo="[line,geometry,selectedEdge===line.id,t('连接','Connection')]" class="canvas-edge" :class="{'edge-selected':selectedEdge===line.id}" role="button" tabindex="0" :aria-label="`${t('连接','Connection')}: ${line.label??line.fromNode+' → '+line.toNode}`" @pointerdown.stop @click="selectedEdge=line.id;selected=[]" @keydown.enter.stop="selectedEdge=line.id;selected=[]">
              <path :d="geometry!.path" fill="none" :stroke="line.color?color(line.color):'var(--color-text-tertiary)'" :stroke-width="selectedEdge===line.id?4:2" :marker-start="line.fromEnd==='arrow'?'url(#canvas-arrow)':undefined" :marker-end="line.toEnd==='none'?undefined:'url(#canvas-arrow)'"/><path :d="geometry!.path" fill="none" stroke="transparent" stroke-width="16" />
              <text v-if="line.label" :x="geometry!.x" :y="geometry!.y-8" text-anchor="middle">{{ line.label.slice(0,120) }}</text>
            </g>
          </svg>
          <div v-for="node in visibleNodes" :key="node.id" v-memo="[node,selectedSet.has(node.id),previews[imagePath(node)??''],readOnly,t('文字','Text')]" class="canvas-node" :class="[node.type,{selected:selectedSet.has(node.id)}]" :data-node-id="node.id" tabindex="0" role="group" :aria-label="`${node.type}: ${nodeLabel(node)}`" :style="{left:`${node.x}px`,top:`${node.y}px`,width:`${node.width}px`,height:`${node.height}px`,borderColor:color(node.color),zIndex:(graph.order.get(node.id)??0)+1}" @pointerdown.stop="begin($event,node)" @keydown.enter.self.prevent.stop="locate(node.id)">
            <header><span>{{ node.type==='text'?t('文字','Text'):node.type==='file'?t('文件','File'):node.type==='link'?t('网址','URL'):node.label??t('分组','Group') }}</span><button v-if="node.type==='file'&&['markdown','canvas'].includes(workspaceDocumentType(node.file??''))||node.type==='link'" class="node-open" :aria-label="t('打开节点目标','Open node target')" @pointerdown.stop @click.stop="open(node)">↗</button></header>
            <div class="canvas-node-content"><template v-if="node.type==='text'">{{ node.text }}</template><template v-else-if="node.type==='link'"><strong>{{ node.url }}</strong><p>{{ t('选择打开时会使用浏览器。','Opens in your browser when selected.') }}</p></template><template v-else-if="node.type==='file'"><strong :title="node.file">{{ nodeLabel(node) }}</strong><img v-if="previews[node.file??'']?.url" :src="previews[node.file??'']!.url" :alt="node.file??''" draggable="false"/><p v-else>{{ previews[node.file??'']?.text??t('读取引用内容…','Loading referenced content…') }}</p></template><div v-else-if="node.background&&previews[node.background]?.url" class="canvas-group-background" role="img" :aria-label="node.label??node.background" :style="groupBackground(node)"/></div>
            <button v-if="selectedSet.has(node.id)&&!readOnly" class="node-resize" :aria-label="t('调整节点尺寸','Resize node')" @pointerdown.stop="begin($event,node,true)">↘</button>
          </div>
        </div>
        <div v-if="box" class="canvas-selection-box" :style="{left:`${box.x}px`,top:`${box.y}px`,width:`${box.width}px`,height:`${box.height}px`}" />
        <p v-if="!document.nodes.length" class="canvas-empty">{{ t('添加文字、笔记、图片或网址开始构建画布。','Add text, notes, images or URLs to start your canvas.') }}</p>
      </div>
      <aside v-if="sidebar" class="canvas-inspector" :aria-label="t('画布节点与连接','Canvas nodes and connections')">
        <div class="canvas-inspector-heading"><strong>{{ t('节点与属性','Nodes and properties') }}</strong><button class="button-secondary canvas-icon-button" :aria-label="t('收起右侧栏','Collapse right sidebar')" :title="t('收起右侧栏','Collapse right sidebar')" @click="sidebar=false"><ControlIcon name="close" :size="16" /></button></div>
        <p class="subtle">{{ t('Shift 选择两个节点后连接；拖动空白区域框选，空格拖动平移。','Shift-select two nodes to connect. Drag empty space to select; Space-drag to pan.') }}</p>
        <form v-if="picked" class="node-properties" @submit.prevent="applyNode"><strong>{{ t('节点属性','Node properties') }} · {{ selected.length }}</strong><label>{{ picked.type==='file'?t('知识库相对路径','Vault-relative path'):picked.type==='link'?'URL':t('内容','Content') }}<textarea v-model="draft.value" class="textarea" :aria-label="t('节点内容','Node content')" :disabled="readOnly" rows="4" /></label><div class="node-dimensions"><label v-for="field in (['x','y','width','height'] as const)" :key="field">{{ field }}<input v-model.number="draft[field]" class="input" type="number" :aria-label="field" :disabled="readOnly" /></label></div><label>{{ t('颜色','Color') }}<select v-model="draft.color" class="select" :disabled="readOnly"><option value="">{{ t('默认','Default') }}</option><option v-for="value in ['1','2','3','4','5','6']" :key="value" :value="value">{{ value }}</option><option v-if="draft.color.startsWith('#')" :value="draft.color">{{ draft.color }}</option></select></label><label v-if="picked.type==='file'">{{ t('标题或块子路径','Heading or block subpath') }}<input v-model="draft.subpath" class="input" placeholder="#heading" :disabled="readOnly" /></label><button class="button-primary" :disabled="readOnly">{{ t('应用属性修改','Apply property changes') }}</button></form>
        <form v-if="edge" class="node-properties" @submit.prevent="applyEdge"><strong>{{ t('连接属性','Connection properties') }}</strong><label>{{ t('连线标签','Connection label') }}<input v-model="edgeDraft.label" class="input" :aria-label="t('连线标签','Connection label')" :disabled="readOnly" /></label><label v-for="field in (['fromSide','toSide'] as const)" :key="field">{{ field }}<select v-model="edgeDraft[field]" class="select" :disabled="readOnly"><option v-for="side in ['left','right','top','bottom']" :key="side" :value="side">{{ side }}</option></select></label><label v-for="field in (['fromEnd','toEnd'] as const)" :key="field">{{ field }}<select v-model="edgeDraft[field]" class="select" :disabled="readOnly"><option value="none">{{ t('无箭头','No arrow') }}</option><option value="arrow">{{ t('箭头','Arrow') }}</option></select></label><button class="button-primary" :disabled="readOnly">{{ t('应用连接修改','Apply connection changes') }}</button></form>
        <h3>{{ t('节点列表','Node list') }} · {{ document.nodes.length }}</h3><input v-model="nodeFilter" class="input" :aria-label="t('筛选画布节点','Filter canvas nodes')"/><div class="canvas-node-list"><button v-for="node in listed" :key="node.id" class="button-secondary" :aria-pressed="selectedSet.has(node.id)" :data-list-node-id="node.id" :title="String(node.text??node.file??node.url??node.label??'')" @click="locate(node.id)"><span class="node-kind">{{ node.type }} · </span><span class="node-label">{{ nodeLabel(node) }}</span></button><button v-if="nodeCount<filteredNodes.length" class="button-secondary" @click="nodeCount+=100">{{ t('更多节点','More nodes') }}</button></div>
        <h3>{{ t('连接列表','Connection list') }} · {{ document.edges.length }}</h3><div class="canvas-node-list"><button v-for="line in document.edges.filter(value=>!selected.length||selected.includes(value.fromNode)||selected.includes(value.toNode)).slice(0,100)" :key="line.id" class="button-secondary" @click="selectedEdge=line.id;selected=[]">{{ line.label??`${nodeLabel(graph.nodes.get(line.fromNode)!)} → ${nodeLabel(graph.nodes.get(line.toNode)!)}` }}</button></div>
      </aside>
    </div>
    <footer class="canvas-status"><span>{{ document.nodes.length }} {{ t('个节点','nodes') }} · {{ document.edges.length }} {{ t('条连接','connections') }} · {{ selected.length }} {{ t('已选择','selected') }}</span><div class="inline-actions"><button class="button-secondary" :aria-label="t('缩小画布','Zoom out canvas')" @click="magnify(1/1.2)">−</button><span>{{ Math.round(zoom*100) }}%</span><button class="button-secondary" :aria-label="t('放大画布','Zoom in canvas')" @click="magnify(1.2)">＋</button><button class="button-secondary" @click="fit()">{{ t('显示全部','Fit all') }}</button></div></footer>
    <AppDialog v-if="creating" :label="creating==='file'?t('添加知识库文件','Add vault file'):t('添加网址','Add URL')" @close="creating=null"><form class="canvas-create" @submit.prevent="confirmAdd"><h2>{{ creating==='file'?t('添加知识库文件','Add vault file'):t('添加网址','Add URL') }}</h2><select v-if="creating==='file'" v-model="newValue" class="select" :aria-label="t('选择知识库文件','Choose vault file')"><option v-for="path in fileOptions" :key="path" :value="path.slice(1)">{{ path }}</option></select><input v-else v-model="newValue" class="input" type="url" required :aria-label="t('节点网址','Node URL')"/><p v-if="error" class="error-banner" role="alert">{{ error }}</p><div class="inline-actions"><button class="button-primary" :disabled="!newValue||readOnly">{{ t('添加节点','Add node') }}</button><button type="button" class="button-secondary" @click="creating=null">{{ t('取消','Cancel') }}</button></div></form></AppDialog>
  </div>
</template>

<style scoped>
.canvas-group-background{position:absolute;inset:0;z-index:-1;pointer-events:none}
.canvas-visual{display:flex;flex:1;min-height:0;min-width:0;flex-direction:column;color:var(--color-text-primary)}.canvas-toolbar{display:flex;gap:var(--space-xs);padding:var(--space-sm);flex-wrap:wrap;border-bottom:1px solid var(--color-border-default)}.canvas-toolbar button{font-size:var(--font-size-xs)}.canvas-body{display:flex;flex:1;min-height:0;min-width:0}.canvas-viewport{position:relative;flex:1;overflow:hidden;min-height:200px;min-width:0;touch-action:none;background-color:var(--color-background-secondary);background-image:radial-gradient(var(--color-border-default) 1px,transparent 1px);background-size:20px 20px;user-select:none}.canvas-world{position:absolute;left:0;top:0;transform-origin:0 0;pointer-events:none}.canvas-edges{position:absolute;overflow:visible;pointer-events:none}.canvas-edge{pointer-events:stroke;cursor:pointer}.canvas-edge text{fill:var(--color-text-primary);font-size:14px;paint-order:stroke;stroke:var(--color-background-secondary);stroke-width:5px;pointer-events:auto}.canvas-node{position:absolute;box-sizing:border-box;border:2px solid var(--color-border-default);border-radius:var(--radius-md);background:var(--color-surface-primary);box-shadow:var(--shadow-sm);display:flex;flex-direction:column;overflow:hidden;pointer-events:auto;cursor:grab}.canvas-node.selected{outline:3px solid var(--color-border-focus);outline-offset:2px}.canvas-node.group{background:color-mix(in srgb,var(--color-accent-soft) 25%,transparent);box-shadow:none}.canvas-node header{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--color-border-subtle);font-size:12px;color:var(--color-text-secondary);min-height:20px}.canvas-node-content{flex:1;min-height:0;padding:12px;white-space:pre-wrap;overflow:hidden;overflow-wrap:anywhere;font-size:14px;line-height:1.5}.canvas-node-content p{font-size:12px;color:var(--color-text-secondary);margin-block:8px}.canvas-node-content img{width:100%;height:calc(100% - 24px);object-fit:contain}.node-open{cursor:pointer;border:1px solid var(--color-border-default);border-radius:var(--radius-sm);width:24px;height:24px;background:var(--color-background-primary);color:var(--color-text-primary)}.node-resize{position:absolute;bottom:0;right:0;width:24px;height:24px;cursor:nwse-resize;background:var(--color-accent-soft);color:var(--color-text-primary)}.canvas-selection-box{position:absolute;border:1px solid var(--color-border-focus);background:var(--color-accent-soft);opacity:.65;pointer-events:none}.canvas-inspector{width:270px;flex-shrink:0;overflow:auto;padding:var(--space-md);border-left:1px solid var(--color-border-default);background:var(--color-surface-primary)}.canvas-inspector h3{font-size:var(--font-size-sm);margin-block:var(--space-lg) var(--space-sm)}.canvas-inspector .subtle{font-size:var(--font-size-xs)}.node-properties{display:grid;gap:var(--space-sm);margin-bottom:var(--space-lg)}label{display:grid;gap:var(--space-xs);font-size:var(--font-size-xs)}.node-dimensions{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-sm)}.canvas-inspector input,.canvas-inspector textarea,.canvas-inspector select{width:100%;min-width:0;box-sizing:border-box}.canvas-node-list{display:grid;gap:var(--space-xs);margin-top:var(--space-sm)}.canvas-node-list button{text-align:left;overflow-wrap:anywhere;white-space:normal;font-size:var(--font-size-xs)}.canvas-status{display:flex;justify-content:space-between;align-items:center;gap:var(--space-sm);padding:var(--space-sm);border-top:1px solid var(--color-border-default);font-size:var(--font-size-xs);flex-wrap:wrap}.canvas-empty{position:absolute;top:40%;left:15%;right:15%;text-align:center;color:var(--color-text-secondary);pointer-events:none}.canvas-create{display:grid;gap:var(--space-lg);width:min(520px,100%)}.canvas-error{margin:0;padding:var(--space-sm)}@media(max-width:640px){.canvas-inspector{width:180px;padding:var(--space-sm)}.canvas-body{flex-direction:column}.canvas-inspector{width:auto;max-height:35%;border-left:0;border-top:1px solid var(--color-border-default)}.canvas-viewport{min-height:180px}.canvas-toolbar{max-height:100px;overflow:auto}.canvas-node-list{grid-template-columns:repeat(2,minmax(0,1fr))}} 

.canvas-toolbar { position: relative; z-index: 12; align-items: center; gap: 10px; padding: 7px 12px; background: var(--color-surface-primary); }
.canvas-toolbar .button-secondary { height: 30px; padding: 4px 10px; }
.canvas-tool-group, .canvas-view-tools { display: flex; align-items: center; gap: 4px; }
.canvas-history-tools { border-left: 1px solid var(--color-border-default); padding-left: 10px; }
.canvas-view-tools { margin-left: auto; gap: 8px; }
.canvas-icon-button { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0 !important; }
.canvas-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.inspector-toggle { display: inline-flex; align-items: center; gap: 7px; }
.inspector-toggle[aria-expanded="true"] { color: var(--color-accent-primary); background: var(--color-accent-soft); }
.mirrored { transform: scaleX(-1); }
.canvas-tools-menu { position: relative; }
.canvas-tools-popover { position: absolute; right: 0; top: calc(100% + 8px); width: max-content; min-width: 190px; padding: 6px; display: grid; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-elevated); box-shadow: var(--shadow-lg); }
.canvas-tools-popover :deep(button) { width: 100%; text-align: left; padding: 8px 12px; font-size: var(--font-size-sm); border-radius: var(--radius-sm); white-space: nowrap; }
.canvas-tools-popover :deep(button:hover:not(:disabled)), .canvas-tools-popover :deep(button:focus-visible) { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.canvas-menu-divider { height: 1px; background: var(--color-border-subtle); margin: 5px; }
.canvas-inspector { box-sizing: border-box; width: 244px; padding: 0 12px 12px; }
.canvas-inspector-heading { position: sticky; top: 0; z-index: 1; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 0; background: var(--color-surface-primary); font-size: var(--font-size-sm); }
.canvas-inspector .subtle { line-height: 1.6; margin: 0 0 14px; }
.canvas-inspector h3 { margin-block: 18px 8px; }
.canvas-node-list button { display: flex; align-items: flex-start; gap: 4px; height: auto; min-height: 36px; padding: 8px 10px; line-height: 1.4; }
.canvas-node-list button[aria-pressed="true"] { border-color: var(--color-border-focus); color: var(--color-accent-primary); background: var(--color-accent-soft); }
.node-kind { flex-shrink: 0; color: var(--color-text-tertiary); font-size: 10px; line-height: 1.7; }
.node-label { min-width: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.canvas-node header > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.canvas-node-content > strong { display: block; font-size: 14px; }
.canvas-status { min-height: 40px; padding: 4px 12px; background: var(--color-surface-primary); }
.canvas-status button { min-width: 28px; height: 28px; padding: 3px 8px; }
@media (max-width: 640px) { .canvas-toolbar { max-height: none; overflow: visible; gap: 6px; } .canvas-inspector { width: auto; max-height: 40%; } .canvas-node-list { grid-template-columns: 1fr; } .inspector-toggle > span { display: none; } .canvas-view-tools { gap: 4px; } }
</style>
