<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import HandwritingPanel from '../../src/features/editor/HandwritingPanel.vue'
import type { HandwritingProposal } from '../../src/features/editor/handwritingActions'
import type { Handwriting } from '../../src/features/editor/handwriting'
import { aiError, analyzeHandwriting, mobileAi, type AiProfile, type AiState } from './handwritingAi'
import type { HandwritingAnalysis } from '../../src/features/editor/HandwritingPanel.vue'
const props=defineProps<{ink?:Handwriting;text?:string;language:string;readOnly?:boolean}>()
const emit=defineEmits<{close:[];apply:[proposal:HandwritingProposal]}>()
const panel=ref<InstanceType<typeof HandwritingPanel>>(),saved=ref<AiState>(),secret=ref(''),error=ref(''),busy=ref(false),importText=ref('')
const profile=ref<AiProfile>({endpoint:'',model:'',protocol:'openai_chat',vision:true})
const t=(zh:string,en:string)=>props.language==='en'?en:zh
async function analyze(request:HandwritingAnalysis,signal:AbortSignal){try{return await analyzeHandwriting(request,signal)}catch(cause){throw Error(aiError(cause,props.language))}}
let disposed=false
async function current(){try{const state=await mobileAi<AiState>({action:'current'});if(!disposed){saved.value=state;if(state.profile)profile.value={...state.profile}}}catch(cause){if(!disposed)error.value=aiError(cause,props.language)}}
async function configure(){
  if(props.readOnly||busy.value)return
  busy.value=true;error.value=''
  const key=secret.value;secret.value=''
  try{const state=await mobileAi<AiState>({action:'configure',profile:profile.value,secret:key||null});if(!disposed)saved.value=state}
  catch(cause){if(!disposed)error.value=aiError(cause,props.language)}finally{if(!disposed)busy.value=false}
}
async function remove(){if(busy.value)return;busy.value=true;try{const state=await mobileAi<AiState>({action:'remove'});if(!disposed)saved.value=state}catch(cause){if(!disposed)error.value=aiError(cause,props.language)}finally{if(!disposed)busy.value=false}}
function importProfile(){
  try{
    const value=JSON.parse(importText.value)
    if(!value||value.schema!==1||!value.profile||typeof value.profile.endpoint!=='string'||typeof value.profile.model!=='string'||typeof value.profile.protocol!=='string'||typeof value.profile.vision!=='boolean')throw Error('AI_PROFILE_INVALID')
    profile.value={endpoint:value.profile.endpoint,model:value.profile.model,protocol:value.profile.protocol,vision:value.profile.vision};importText.value='';error.value=''
  }catch{error.value=t('连接配置无效；请使用 PC 导出的无密钥配置。','Invalid connection settings. Use the key-free profile exported on PC.')}
}
onMounted(current)
onBeforeUnmount(()=>{disposed=true;secret.value=''})
defineExpose({close:()=>panel.value?.close()})
</script>
<template>
<HandwritingPanel ref="panel" v-bind="props" :analyze="analyze" @close="emit('close')" @apply="emit('apply',$event)">
  <template #model="{busy:taskBusy,invalidate}"><details @input="invalidate()" @change="invalidate()"><summary>{{ saved?.profile?t('原生 AI 连接已配置','Native AI connection configured'):t('配置原生 AI 连接','Configure native AI connection') }}</summary><fieldset class="native-ai-fields" :disabled="taskBusy||busy||readOnly">
    <p>{{ t('可使用与 PC 相同的服务和模型。密钥在本设备单独填写，保存在 Keystore 加密槽，不进入画布或同步文件。','Use the same service and model as PC. Enter the key separately on this device; it is kept in Keystore-backed storage, outside canvas and sync files.') }}</p>
    <label>{{ t('从 PC 导入连接（不含密钥）','Import PC connection (no key)') }}<textarea v-model="importText" rows="2" maxlength="4096"/></label><button :disabled="busy||readOnly" @click="importProfile">{{ t('导入连接信息','Import profile') }}</button>
    <label>{{ t('HTTPS API 根地址','HTTPS API base URL') }}<input v-model="profile.endpoint" type="url" placeholder="https://example.com/v1" maxlength="1024"/></label>
    <label>{{ t('协议','Protocol') }}<select v-model="profile.protocol"><option value="openai_chat">OpenAI compatible</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option><option value="ollama">Ollama (HTTPS)</option></select></label>
    <label>{{ t('模型 ID','Model ID') }}<input v-model="profile.model" maxlength="256"/></label><label><input v-model="profile.vision" type="checkbox"/>{{ t('此模型支持视觉输入','This model supports images') }}</label>
    <label>API Key<input v-model="secret" type="password" autocomplete="off" maxlength="8192" :placeholder="saved?.credential_configured?t('留空保留当前地址的密钥','Leave empty to keep the key for this endpoint'):''"/></label>
    <button :disabled="busy||readOnly" @click="configure">{{ t('保存到本设备','Save on this device') }}</button><button :disabled="busy||!saved?.profile||readOnly" @click="remove">{{ t('删除本设备连接','Remove device connection') }}</button>
  </fieldset></details><p v-if="error" role="alert">{{ error }}</p></template>
</HandwritingPanel>
</template>

<style scoped>.native-ai-fields{display:grid;gap:8px;border:0;padding:12px 0}.native-ai-fields label{display:grid;gap:4px}</style>
