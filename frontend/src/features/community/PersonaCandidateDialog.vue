<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import { hostInvoke } from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'
const props=defineProps<{slot:string;sourceRevision:string}>()
const emit=defineEmits<{close:[];applied:[]}>()
const workspace=useWorkspaceStore()
interface Persona {version:number;name:string;system_prompt:string;dialogue_pairs:Array<{user:string;assistant:string}>}
interface Preview {fingerprint:string;slot:string;target:string;path:string;expected:string;target_version:number;before:Persona|null;after:Persona;after_sha256:string;package_name:string;package_version:string}
const target=ref(''),preview=ref<Preview|null>(null),busy=ref(false),applying=ref(false),error=ref('')
let generation=0,activeRequest:string|undefined,cancelledGeneration:number|undefined
function cancelRequest(){cancelledGeneration=generation;if(activeRequest)void hostInvoke('extension_stage_cancel',{requestId:activeRequest}).catch(()=>undefined)}
function invalidate(){++generation;cancelRequest();preview.value=null;busy.value=false;applying.value=false}
function message(reason:unknown){
  const code=reason instanceof Error?reason.message:String(reason)
  const messages:Record<string,string>={
    REVISION_CONFLICT:t('当前人设已被修改，请重新预览差异。','The current persona changed. Preview the differences again.'),
    EXTENSION_CANDIDATE_CHANGED:t('包、来源或审核内容已改变，请重新预览。','The package, source or review changed. Preview it again.'),
    EXTENSION_KEY_REVOKED:t('签名密钥已撤销，请核对社区来源。','The signing key was revoked. Review the catalog source.'),
    EXTENSION_RELEASE_WITHDRAWN:t('此版本已撤回，请选择其他版本。','This release was withdrawn. Choose another version.'),
    EXTENSION_TRUST_UNAVAILABLE:t('暂时无法核对社区来源，请稍后重试。','The catalog source could not be checked. Retry later.'),
    REQUEST_CANCELLED:t('已取消应用，请核对当前人设。','Application was cancelled. Check the current persona.'),
    REQUEST_TIMEOUT:t('核对已超时，请重新预览。','The check timed out. Preview again.'),
    VAULT_CHANGED:t('知识库已切换，请重新选择目标。','The vault changed. Choose the target again.'),
    EXTENSION_CANDIDATE_INVALID:t('此包的人设字段不符合设置要求，请核对发行说明。','This package has unsupported persona fields. Review its release information.'),
    RECORD_SCHEMA_INVALID:t('此包的人设内容超出设置限制，请核对发行说明。','This persona exceeds the setting limits. Review its release information.'),
    EXTENSION_SOURCE_UNTRUSTED:t('来源的信任设置已改变，请重新核对签名密钥。','Source trust changed. Review the signing key again.'),
  }
  return messages[code]??code
}
function checked(value:Preview):Preview{
  const digest=(value:unknown)=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value)
  const persona=(value:Persona)=>value&&typeof value.name==='string'&&typeof value.system_prompt==='string'&&Array.isArray(value.dialogue_pairs)&&value.dialogue_pairs.every(pair=>typeof pair.user==='string'&&typeof pair.assistant==='string')
  if(!value||value.slot!==props.slot||value.target!=='workspace_persona'||value.path!=='opennexus-records/v1/persona/default.json'||!digest(value.fingerprint)||!digest(value.after_sha256)||(value.expected!==''&&!digest(value.expected))||!Number.isSafeInteger(value.target_version)||value.target_version<0||!persona(value.after)||value.after.version!==value.target_version+1||(value.before!==null&&!persona(value.before))||Boolean(value.expected)!==Boolean(value.before))throw new Error(t('人设预览响应无效，请重试。','Invalid persona preview. Retry.'))
  return value
}
async function inspect(){
  invalidate();error.value=''
  const vault=workspace.vaultId,current=++generation,slot=props.slot
  if(!vault||target.value!=='workspace_persona')return
  busy.value=true
  try{const value=await hostInvoke<Preview>('extension_persona_preview',{request:{slot,vault_id:vault}});if(current===generation&&workspace.vaultId===vault)preview.value=checked(value)}
  catch(reason){if(current===generation)error.value=message(reason)}
  finally{if(current===generation)busy.value=false}
}
async function apply(){
  const review=preview.value,vault=workspace.vaultId,current=++generation
  if(!review||!vault||target.value!==review.target)return
  let requestId:string|undefined,dispatched=false
  const operationId=crypto.randomUUID();cancelledGeneration=undefined;busy.value=true;applying.value=true;error.value=''
  try{
    requestId=await hostInvoke<string>('extension_stage_prepare')
    if(current!==generation||workspace.vaultId!==vault||cancelledGeneration===current){await hostInvoke('extension_stage_cancel',{requestId});if(cancelledGeneration===current)throw new Error('REQUEST_CANCELLED');return}
    activeRequest=requestId;dispatched=true
    const receipt=await hostInvoke<{operation_id:string;state:string;hash:string;path:string}>('extension_persona_apply',{request:{request_id:requestId,application:{vault_id:vault,slot:review.slot,target:review.target,expected:review.expected,target_version:review.target_version,fingerprint:review.fingerprint,operation_id:operationId}}})
    if(receipt?.operation_id!==operationId||receipt.state!=='applied'||receipt.hash!==review.after_sha256||receipt.path!==review.path)throw new Error(t('应用结果尚未确认，请核对当前人设。','Application is unconfirmed. Check the current persona.'))
    if(current===generation&&workspace.vaultId===vault)emit('applied')
  }catch(reason){
    if(current===generation&&workspace.vaultId===vault){
      let recovered=false
      if(dispatched){
        const state=await hostInvoke<{operation_id:string;state:string;result:{path:string;hash:string}|null}|null>('workspace_operation',{vaultId:vault,operationId}).catch(()=>null)
        recovered=!!state&&state.operation_id===operationId&&state.state==='committed'&&state.result?.path===review.path&&state.result?.hash===review.after_sha256
      }
      if(current===generation&&workspace.vaultId===vault){if(recovered)emit('applied');else{preview.value=null;error.value=message(reason)}}
    }
  }finally{
    if(activeRequest===requestId)activeRequest=undefined
    if(requestId)void hostInvoke('extension_stage_cancel',{requestId}).catch(()=>undefined)
    if(current===generation){busy.value=false;applying.value=false}
  }
}
watch(target,()=>{invalidate();error.value=''})
watch(()=>[workspace.vaultId,props.slot,props.sourceRevision],()=>{invalidate();target.value='';error.value=t('知识库或来源已改变，请重新选择目标并预览。','The vault or source changed. Choose a target and preview again.')},{flush:'sync'})
onBeforeUnmount(invalidate)
</script>

<template>
  <AppDialog :label="t('应用社区人设','Apply a catalog persona')" :dismissible="!applying" @close="emit('close')"><section class="modal-card candidate-persona">
    <h2>{{ t('应用人设','Apply persona') }}</h2>
    <p>{{ t('选择目标并核对差异。当前知识库的对话与智能体将使用应用后的人设。','Choose the target and review its differences. Chats and agents in this vault will use the applied persona.') }}</p>
    <label class="field">{{ t('应用目标','Application target') }}<select v-model="target" class="input" :disabled="busy"><option value="">{{ t('请选择目标','Choose a target') }}</option><option value="workspace_persona">{{ t('当前知识库人设','Current vault persona') }}</option></select></label>
    <button class="button-secondary" :disabled="busy||!target" @click="inspect">{{ t('预览人设差异','Preview persona differences') }}</button>
    <p v-if="busy" role="status">{{ t('正在核对…','Checking…') }}</p><p v-if="error" role="alert" class="error-banner">{{ error }}</p>
    <template v-if="preview"><h3>{{ preview.package_name }} · {{ preview.package_version }}</h3><div class="persona-differences">
      <article v-for="side in (['before','after'] as const)" :key="side"><h4>{{ side==='before'?t('当前人设','Current persona'):t('应用后的人设','Persona after application') }}</h4><template v-if="preview[side]"><strong>{{ preview[side]!.name }}</strong><pre>{{ preview[side]!.system_prompt }}</pre><section v-for="(pair,index) in preview[side]!.dialogue_pairs" :key="index"><h5>{{ t('对话示例','Example dialogue') }} {{ index+1 }}</h5><pre>{{ t('用户：','User: ') }}{{ pair.user }}</pre><pre>AI: {{ pair.assistant }}</pre></section></template><p v-else>{{ t('尚未设置人设','No persona is configured') }}</p></article>
    </div><button class="button-primary" :disabled="busy" @click="apply">{{ t('确认应用人设','Confirm persona application') }}</button></template>
    <button v-if="applying" class="button-secondary" @click="cancelRequest">{{ t('取消并核对结果','Cancel and check the result') }}</button><button v-else class="button-secondary" @click="emit('close')">{{ t('关闭','Close') }}</button>
  </section></AppDialog>
</template>

<style scoped>
.candidate-persona{width:min(900px,calc(100vw - 32px));max-height:85vh;overflow:auto;padding:var(--space-xl);background:var(--color-surface-primary);color:var(--color-text-primary);border:1px solid var(--color-border-default);border-radius:var(--radius-lg)}
.candidate-persona h2,.candidate-persona p{margin-block:var(--space-sm)}.field{display:grid;gap:var(--space-xs);margin-block:var(--space-md)}
.persona-differences{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-md);margin-block:var(--space-md)}article{min-width:0;border:1px solid var(--color-border-subtle);padding:var(--space-md);border-radius:var(--radius-md);background:var(--color-background-secondary)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto}.candidate-persona h3{overflow-wrap:anywhere}
@media(max-width:700px){.persona-differences{grid-template-columns:1fr}}
</style>
