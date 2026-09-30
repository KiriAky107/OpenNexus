<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import type { FileNode } from '@/contracts'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { readFileContent } from '@/services/workspaceService'
import { splitNoteMetadata } from '@/utils/noteMetadata'
import { folderNoteSummary } from './folderSummary'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import { t } from '@/i18n'
const props = defineProps<{path:string}>()
const workspace = useWorkspaceStore(), editor = useEditorStore()
const view = ref<'cards'|'list'>(localStorage.getItem('folder-view') === 'list' ? 'list' : 'cards')
const sort = ref<'title'|'updated'>('title'), query = ref(''), count = ref(40), error = ref(''), busy = ref(false)
const summaries = ref<Record<string,{stamp?:string;title:string;summary:string}>>({}), intro = ref('')
let generation = 0
const find = (nodes:FileNode[]):FileNode|undefined => nodes.find(node=>node.path===props.path) ?? nodes.flatMap(node=>node.children??[]).map(node=>find([node])).find(Boolean)
const children = computed(()=>props.path === '/' ? workspace.fileTree : find(workspace.fileTree)?.children??[])
const introduction = computed(()=>children.value.find(node=>node.type==='file' && node.name.toLowerCase()==='index.md'))
const entries = computed(()=>children.value.filter(node=>node.type==='folder'||/\.(md|canvas)$/i.test(node.path)).filter(node=>node.name.toLowerCase().includes(query.value.trim().toLowerCase())).sort((a,b)=>a.type !== b.type ? a.type === 'folder' ? -1 : 1 : sort.value==='updated' ? (b.updated_at??'').localeCompare(a.updated_at??'') || a.name.localeCompare(b.name) : a.name.localeCompare(b.name)))
const shown = computed(()=>entries.value.slice(0,count.value))
watch(view,value=>{try{localStorage.setItem('folder-view',value)}catch{/* session state */}})
watch(()=>[props.path,workspace.vaultId],()=>{generation++;summaries.value={};intro.value='';count.value=40;query.value='';error.value=''}, {flush:'sync'})
watch(()=>[sort.value,query.value],()=>{count.value=40})
watch(()=>[props.path,workspace.vaultId,JSON.stringify(shown.value.map(node=>[node.path,node.content_hash])),introduction.value?.content_hash],async()=>{
  const version=++generation,vault=workspace.vaultId
  busy.value=true
  const items=[...shown.value.filter(node=>node.type==='file'&&/\.md$/i.test(node.path))]
  if(introduction.value&&!items.some(item=>item.path===introduction.value!.path))items.push(introduction.value)
  for(let offset=0;offset<items.length;offset+=8){
    await Promise.all(items.slice(offset,offset+8).map(async node=>{
      try{
        if(node.content_hash&&summaries.value[node.path]?.stamp===node.content_hash)return
        const content=await readFileContent(node.path)
        if(version!==generation||vault!==workspace.vaultId)return
        summaries.value[node.path]={stamp:node.content_hash,...folderNoteSummary(node.path,content)}
        if(node.path===introduction.value?.path)intro.value=content
      }catch(cause){if(version===generation)error.value=`${node.path}: ${String(cause)}`}
    }))
    if(version!==generation)return
  }
  if(version===generation)busy.value=false
},{immediate:true})
onBeforeUnmount(()=>generation++)
async function open(node:FileNode){
  if(node.type==='folder'){workspace.selectFolder(node.path);return}
  try{await editor.loadFile(node.path);workspace.openFile(node.path)}catch(cause){error.value=String(cause)}
}
const introductionSource = computed(()=>splitNoteMetadata(intro.value)?.body??intro.value)
</script>
<template>
  <section class="folder-contents">
    <header><div><button v-if="path !== '/'" class="button-secondary" @click="workspace.selectFolder(path.slice(0,path.lastIndexOf('/'))||'/')">{{ t('上一级','Up one level') }}</button><h1>{{ path === '/' ? workspace.vaultName : path.split('/').at(-1) }}</h1><p class="subtle">{{ path }} · {{ entries.length }} {{ t('个子项','items') }}</p></div><div class="inline-actions"><button class="button-secondary" :aria-pressed="view==='cards'" @click="view='cards'">{{ t('卡片','Cards') }}</button><button class="button-secondary" :aria-pressed="view==='list'" @click="view='list'">{{ t('列表','List') }}</button><button class="button-secondary" @click="workspace.refreshFileTree()">{{ t('刷新','Refresh') }}</button></div></header>
    <div v-if="introduction && intro" class="folder-introduction"><div class="inline-actions"><strong>{{ t('目录导言','Folder introduction') }}</strong><button class="button-secondary" @click="open(introduction)">{{ t('编辑 index.md','Edit index.md') }}</button></div><MarkdownContent :source="introductionSource" :source-path="introduction.path" /></div>
    <div class="folder-controls inline-actions"><input v-model="query" class="input" :aria-label="t('筛选文件名','Filter filenames')" :placeholder="t('筛选文件名','Filter filenames')"/><select v-model="sort" class="select" :aria-label="t('子项排序','Sort items')"><option value="title">{{ t('名称','Name') }}</option><option value="updated">{{ t('最近更新','Recently updated') }}</option></select></div>
    <p v-if="error" role="alert" class="error-banner">{{ error }}</p><p v-if="busy" role="status">{{ t('读取笔记摘要…','Loading note summaries…') }}</p>
    <div :class="['folder-items',view]"><button v-for="node in shown" :key="node.path" class="folder-item" @click="open(node)"><strong>{{ node.type==='folder'?'▸ ':'' }}{{ summaries[node.path]?.title??node.name.replace(/\.(md|canvas)$/i,'') }}</strong><span class="folder-kind">{{ node.type==='folder'?t('文件夹','Folder'):/\.canvas$/i.test(node.path)?t('画布','Canvas'):t('笔记','Note') }}</span><p>{{ node.type==='folder'?t(`${node.children?.length??0} 个直接子项`,`${node.children?.length??0} direct items`):summaries[node.path]?.summary??'' }}</p><small>{{ node.updated_at?new Date(node.updated_at).toLocaleString():t('更新时间暂不可用','Update time unavailable') }}</small></button></div>
    <p v-if="!entries.length" class="empty-state">{{ query?t('没有匹配的子项','No matching items'):t('文件夹中还没有笔记或子文件夹','No notes or subfolders yet') }}</p><button v-if="count<entries.length" class="button-secondary" @click="count+=40">{{ t('更多子项','More items') }}</button>
  </section>
</template>
<style scoped>
.folder-contents{flex:1;overflow:auto;padding:var(--space-xl);min-width:0;color:var(--color-text-primary)}header{display:flex;justify-content:space-between;gap:var(--space-md);flex-wrap:wrap}h1{font-size:var(--font-size-xl);margin-block:var(--space-sm)}.folder-introduction{border:1px solid var(--color-border-default);padding:var(--space-lg);border-radius:var(--radius-lg);margin-block:var(--space-lg);max-height:320px;overflow:auto}.folder-controls{margin-block:var(--space-lg);flex-wrap:wrap}.folder-controls input{flex:1;min-width:120px}.folder-items{display:grid;gap:var(--space-md)}.cards{grid-template-columns:repeat(auto-fill,minmax(min(240px,100%),1fr))}.folder-item{display:grid;gap:var(--space-sm);padding:var(--space-lg);border:1px solid var(--color-border-default);border-radius:var(--radius-lg);background:var(--color-surface-primary);text-align:left;overflow-wrap:anywhere;min-width:0;color:inherit}.folder-item:hover{border-color:var(--color-accent-primary)}.folder-kind,small{font-size:var(--font-size-xs);color:var(--color-text-secondary)}.folder-item p{margin:0;color:var(--color-text-secondary);line-height:1.6}.list .folder-item{grid-template-columns:minmax(0,1fr) auto;gap:var(--space-xs) var(--space-md)}.list .folder-item p{grid-column:1/-1}@media(max-width:640px){.folder-contents{padding:var(--space-sm)}.list .folder-item{display:flex;flex-direction:column}}
</style>
