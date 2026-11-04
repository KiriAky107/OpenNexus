<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import HandwritingPanel, { type HandwritingAnalysis } from './HandwritingPanel.vue'
import { type Handwriting } from './handwriting'
import type { HandwritingProposal } from './handwritingActions'
import { useProviderStore } from '@/stores/provider'
import { useChatStore } from '@/stores/chat'
import api from '@/services/apiClient'
const props=defineProps<{ink?:Handwriting;text?:string;language?:string;readOnly?:boolean}>()
const emit=defineEmits<{close:[];apply:[proposal:HandwritingProposal]}>()
const providers=useProviderStore(),chat=useChatStore()
const provider=ref(chat.selectedProviderId),model=ref(chat.selectedModel)
const exportProfile=ref('')
function exportConnection(){
  const p=providers.providers.find(p=>p.provider_id===provider.value);if(!p)return
  const protocol=p.provider_type==='openai_compatible'?'openai_chat':p.provider_type
  try{const endpoint=new URL(p.base_url??'');if(endpoint.protocol!=='https:'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash)throw Error();exportProfile.value=JSON.stringify({schema:1,profile:{endpoint:endpoint.href,model:model.value,protocol,vision:p.capabilities.vision===true}},null,2)}
  catch{exportProfile.value=t('Android 连接需要不含账号、口令和查询参数的 HTTPS API 地址。','Android requires an HTTPS API base URL without account, password or query parameters.')}
}
const models=computed(()=>providers.modelsByProvider[provider.value]??[])
const t=(zh:string,en:string)=>props.language==='en'?en:zh
async function choose(){model.value=providers.providers.find(p=>p.provider_id===provider.value)?.default_model??'';try{await providers.loadModels(provider.value)}catch{/* manual model ID remains available */}}
onMounted(async()=>{await providers.loadProviders();if(!provider.value)provider.value=providers.defaultProviderId;if(!model.value)await choose();else if(provider.value)try{await providers.loadModels(provider.value)}catch{/* manual model ID */}})
async function analyze(request:HandwritingAnalysis,signal:AbortSignal){
  if(!provider.value||!model.value.trim())throw Error(t('请选择已有 Provider 和模型。','Choose a configured provider and model.'))
  const result=await api.post<{text:string}>('/api/handwriting/analyze',{...request,provider_id:provider.value,model:model.value},{signal,timeoutMs:95000})
  return result.text
}
</script>
<template>
<HandwritingPanel v-bind="props" :analyze="analyze" @close="emit('close')" @apply="emit('apply',$event)">
  <template #model="{busy,invalidate}"><label>{{ t('现有模型服务','Configured provider') }}<select v-model="provider" :disabled="busy" @change="invalidate();choose()"><option value="" disabled>{{ t('选择 Provider','Choose provider') }}</option><option v-for="item in providers.enabledProviders" :key="item.provider_id" :value="item.provider_id">{{ item.name }}</option></select></label><label>{{ t('模型（笔迹识别需要视觉能力）','Model (vision is required for ink)') }}<input v-model="model" :disabled="busy" list="handwriting-models" maxlength="256" @input="invalidate()"/><datalist id="handwriting-models"><option v-for="item in models" :key="item.model_id" :value="item.model_id"/></datalist></label><button type="button" @click="exportConnection">{{ t('导出 Android 连接（不含密钥）','Export Android connection (no key)') }}</button><textarea v-if="exportProfile" :value="exportProfile" readonly rows="5" :aria-label="t('无密钥连接配置','Key-free connection profile')"/></template>
</HandwritingPanel>
</template>
