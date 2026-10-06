<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import apiClient from '@/services/apiClient'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'

const props = defineProps<{ slot: string; kind: 'mcp' | 'model'; sourceRevision: string }>()
const emit = defineEmits<{ close: []; applied: [] }>()
const workspace = useWorkspaceStore()
interface Review {review_id:string;fingerprint:string;slot:string;kind:string;target:string;before:Record<string,unknown>|null;after:Record<string,unknown>;after_sha256:string;package_name:string;package_version:string;model_plan?:Record<string,unknown>}
interface Receipt {operation_id:string;fingerprint:string;state:string;target:string;after_sha256:string}
const options = ref<Array<{id:string;label:string}>>([]), target = ref(''), review = ref<Review|null>(null)
const busy = ref(false), applying = ref(false), error = ref('')
let generation = 0, controller: AbortController|undefined
const base = '/api/community/configurations'
function invalidate() {++generation;controller?.abort();controller=undefined;review.value=null;busy.value=false;applying.value=false}
function message(reason:unknown) {
  const code = (reason as {code?:string})?.code ?? (reason instanceof Error ? reason.message : String(reason))
  const messages:Record<string,string> = {
    CATALOG_TARGET_CHANGED:t('目标配置已修改，请重新预览。','The target changed. Preview again.'),
    CATALOG_REVIEW_CHANGED:t('来源、包或审核已改变，请重新核对。','The source, package or review changed. Check it again.'),
    CATALOG_REVIEW_EXPIRED:t('审核已过期，请重新预览。','The review expired. Preview again.'),
    CATALOG_MODEL_PIN_MISMATCH:t('此方案与应用内已核对的模型版本不匹配。','This plan does not match a pinned model version.'),
    CATALOG_MODEL_TARGET_UNSUPPORTED:t('此模型方案未声明受支持的运行设置。','This model plan does not declare supported runtime settings.'),
    EXTENSION_KEY_REVOKED:t('签名密钥已撤销，请核对来源。','The signing key was revoked. Check the source.'),
    REQUEST_CANCELLED:t('已取消，应用结果未确认；请重新核对目标。','Cancelled; the result is unconfirmed. Check the target again.'),
    CATALOG_APPLICATION_UNCONFIRMED:t('应用结果未确认，请核对目标后重新预览。','Application is unconfirmed. Check the target and preview again.'),
    CATALOG_PLAINTEXT_SECRET:t('配置含明文密钥字段，请先在 MCP 设置中拆分并加密保存。','The configuration has plain secret fields. Separate and encrypt them in MCP settings first.'),
  }
  return messages[code] ?? (reason instanceof Error ? reason.message : code)
}
function checked(value:Review):Review {
  const digest = (item:unknown)=>typeof item==='string'&&/^[0-9a-f]{64}$/.test(item)
  const uuid = (item:unknown)=>typeof item==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item)
  if(!value||!uuid(value.review_id)||!digest(value.fingerprint)||!digest(value.after_sha256)||value.slot!==props.slot||value.kind!==props.kind||typeof value.target!=='string'||(target.value!=='mcp:new'&&value.target!==target.value)||!value.after||typeof value.after!=='object'||Array.isArray(value.after)||(value.before!==null&&(!value.before||typeof value.before!=='object'||Array.isArray(value.before)))||typeof value.package_name!=='string'||typeof value.package_version!=='string')throw new Error(t('配置预览响应无效，请重试。','Invalid configuration preview. Retry.'))
  if(props.kind==='mcp'&&(value.after.enabled!==false||value.after.approved_digest!==null||value.after.tested_digest!==null||!/^mcp:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value.target)))throw new Error(t('MCP 预览状态无效，请重试。','Invalid MCP preview state. Retry.'))
  if(props.kind==='model'&&value.target!=='model:local_runtime')throw new Error(t('模型应用目标无效，请重试。','Invalid model target. Retry.'))
  if(props.kind==='model'&&(!value.model_plan||!['source','revision','license','model_key'].every(key=>typeof value.model_plan?.[key]==='string')))throw new Error(t('模型方案信息不完整，请重试。','Incomplete model plan. Retry.'))
  if(!Number.isSafeInteger(value.after.version)||Number(value.after.version)!==Number(value.before?.version??0)+1)throw new Error(t('配置修订响应无效，请重试。','Invalid configuration revision. Retry.'))
  return value
}
async function loadTargets() {
  invalidate();target.value='';options.value=[];error.value=''
  const vault=workspace.vaultId,current=++generation
  if(!vault)return
  busy.value=true;const request=new AbortController();controller=request
  try {
    const data=await apiClient.get<{kind:string;items:Array<{id:string;label:string}>}>(`${base}/targets`,{params:{slot:props.slot},signal:request.signal})
    if(current!==generation||workspace.vaultId!==vault)return
    if(data.kind!==props.kind||!Array.isArray(data.items)||data.items.length>257||data.items.some(item=>typeof item.id!=='string'||typeof item.label!=='string'||(props.kind==='model'?item.id!=='model:local_runtime':!/^mcp:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(item.id)))||new Set(data.items.map(item=>item.id)).size!==data.items.length)throw new Error(t('配置目标响应无效，请重试。','Invalid target response. Retry.'))
    options.value=data.items.map(item=>({...item,label:item.id==='mcp:new'?t('新建 MCP 配置','New MCP configuration'):item.id==='model:local_runtime'?t('本地模型运行设置','Local model runtime'):item.label}))
  }catch(reason){if(current===generation)error.value=message(reason)}
  finally{if(current===generation)busy.value=false}
}
async function inspect() {
  invalidate();error.value=''
  const vault=workspace.vaultId,current=++generation
  if(!vault||!options.value.some(option=>option.id===target.value))return
  busy.value=true;const request=new AbortController();controller=request
  try {
    const data=await apiClient.post<Review>(`${base}/preview`,{slot:props.slot,target:target.value},{signal:request.signal})
    if(current===generation&&workspace.vaultId===vault)review.value=checked(data)
  }catch(reason){if(current===generation)error.value=message(reason)}
  finally{if(current===generation)busy.value=false}
}
function matches(value:Receipt|null,operation:string,proposal:Review) {
  return value?.state==='applied'&&value.operation_id===operation&&value.fingerprint===proposal.fingerprint&&value.target===proposal.target&&value.after_sha256===proposal.after_sha256
}
async function apply() {
  const proposal=review.value,vault=workspace.vaultId,current=++generation
  if(!proposal||!vault)return
  const operation=crypto.randomUUID(),request=new AbortController();controller=request
  applying.value=true;busy.value=true;error.value=''
  try {
    const value=await apiClient.post<Receipt>(`${base}/apply`,{review_id:proposal.review_id,fingerprint:proposal.fingerprint,operation_id:operation},{signal:request.signal,timeoutMs:90000})
    if(!matches(value,operation,proposal))throw new Error('CATALOG_APPLICATION_UNCONFIRMED')
    if(current===generation&&workspace.vaultId===vault)emit('applied')
  }catch(reason){
    if(current===generation&&workspace.vaultId===vault){
      const receipt=await apiClient.get<Receipt|null>(`${base}/operations/${operation}`,{params:{fingerprint:proposal.fingerprint}}).catch(()=>null)
      if(current===generation&&workspace.vaultId===vault){
        if(matches(receipt,operation,proposal))emit('applied')
        else{review.value=null;error.value=message(reason)}
      }
    }
  }finally{if(current===generation){busy.value=false;applying.value=false}}
}
watch(target,()=>{invalidate();error.value=''},{flush:'sync'})
watch(()=>[workspace.vaultId,props.slot,props.kind,props.sourceRevision],()=>void loadTargets(),{immediate:true,flush:'sync'})
onBeforeUnmount(invalidate)
</script>

<template>
  <AppDialog :label="t('应用社区配置','Apply a catalog configuration')" :dismissible="!applying" @close="emit('close')"><section class="modal-card configuration-candidate">
    <h2>{{ kind==='mcp'?t('应用 MCP 配置','Apply MCP configuration'):t('应用模型运行方案','Apply a model runtime plan') }}</h2>
    <p>{{ kind==='mcp'?t('确认后保存连接配置并停用目标服务器。保留已有加密密钥；启动前仍需在 MCP 页面审核命令、测试连接并启用。','Confirmation saves connection settings and disables the target. Existing encrypted secrets are retained. Review its command, test the connection and enable it on the MCP page before use.'):t('确认后更新本机模型运行设置。模型权重和运行组件需要在设置页面另行安装；修改 Embedding 后需重建索引。','Confirmation updates local runtime settings. Install model weights and runtime components separately in Settings. Rebuild the index after changing the embedding model.') }}</p>
    <label>{{ t('应用目标','Application target') }}<select v-model="target" class="input" :disabled="busy"><option value="">{{ t('请选择目标','Choose a target') }}</option><option v-for="option in options" :key="option.id" :value="option.id">{{ option.label }}</option></select></label>
    <button class="button-secondary" :disabled="busy||!target" @click="inspect">{{ t('预览配置差异','Preview configuration differences') }}</button>
    <p v-if="busy" role="status">{{ t('正在核对…','Checking…') }}</p><p v-if="error" role="alert">{{ error }}</p>
    <template v-if="review"><h3>{{ review.package_name }} · {{ review.package_version }}</h3><details v-if="review.model_plan"><summary>{{ t('模型来源、版本、许可与资源声明','Model source, revision, license and resource declarations') }}</summary><pre>{{ JSON.stringify(review.model_plan,null,2) }}</pre></details><div class="differences"><article v-for="side in (['before','after'] as const)" :key="side"><h4>{{ side==='before'?t('当前配置','Current configuration'):t('应用后的配置','Configuration after application') }}</h4><pre v-if="review[side]!==null">{{ JSON.stringify(review[side],null,2) }}</pre><p v-else>{{ t('尚未配置','No configuration yet') }}</p></article></div><button class="button-primary" :disabled="busy" @click="apply">{{ t('确认应用配置','Confirm configuration application') }}</button></template>
    <button v-if="applying" class="button-secondary" @click="controller?.abort()">{{ t('取消并核对结果','Cancel and check the result') }}</button><button v-else class="button-secondary" @click="emit('close')">{{ t('关闭','Close') }}</button>
  </section></AppDialog>
</template>

<style scoped>
.configuration-candidate{width:min(920px,calc(100vw - 32px));max-height:85vh;overflow:auto;padding:var(--space-xl);background:var(--color-surface-primary);color:var(--color-text-primary);border:1px solid var(--color-border-default);border-radius:var(--radius-lg)}
label{display:grid;gap:var(--space-sm);margin-block:var(--space-md)}.differences{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-md)}article{min-width:0;border:1px solid var(--color-border-subtle);padding:var(--space-md);border-radius:var(--radius-md);background:var(--color-background-secondary)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto}h3,p{overflow-wrap:anywhere}button{margin-top:var(--space-md);margin-right:var(--space-sm)}
@media(max-width:700px){.differences{grid-template-columns:1fr}}
</style>
