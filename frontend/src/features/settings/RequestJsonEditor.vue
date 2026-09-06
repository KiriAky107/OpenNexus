<script setup lang="ts">
import { ref, watch } from 'vue'
import { apiClient } from '@/services/apiClient'
import type { RequestOverride } from '@/contracts'
import { t } from '@/i18n'
import FilePicker from '@/components/common/FilePicker.vue'
const props = defineProps<{modelValue: RequestOverride[]}>()
const emit = defineEmits<{ 'update:modelValue': [value:RequestOverride[]]; valid:[value:boolean] }>()
const transferError = ref('')
let published = JSON.stringify(props.modelValue)
let generation = 0
const rules = ref(props.modelValue.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''})))
const protectedFields = new Set(['model','messages','input','system','instructions','tools','tool_choice','parallel_tool_calls','functions','function_call','file','audio','reference_file','stream','previous_response_id','conversation','background','store'])
function publish() {
  generation++
  let valid = true
  const result: RequestOverride[] = []
  for (const rule of rules.value) {
    try {
      const body = JSON.parse(rule.draft)
      if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error(t('顶层必须为 JSON 对象', 'The top level must be a JSON object'))
      const conflicts = Object.keys(body).filter(key => protectedFields.has(key))
      if (conflicts.length) throw new Error(`${t('运行请求管理字段不可覆盖：', 'Runtime-managed fields cannot be overridden: ')}${conflicts.join(', ')}`)
      rule.error = ''
      result.push({capability:rule.capability,model:rule.model || null,stream:rule.stream ?? null,body})
    } catch(e) { rule.error = (e as Error).message; valid = false }
  }
  emit('valid', valid)
  if(valid) { published = JSON.stringify(result); emit('update:modelValue', result) }
}
function add() { rules.value.push({capability:'chat',model:null,stream:null,body:{},draft:'{}',error:''}); publish() }
function format(index:number) { try { rules.value[index].draft = JSON.stringify(JSON.parse(rules.value[index].draft), null, 2); publish() } catch { publish() } }
watch(() => props.modelValue, value => {
  if (JSON.stringify(value) !== published) {
    generation++
    rules.value = value.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''}))
    published = JSON.stringify(value)
    emit('valid', true)
  }
}, {deep: true})
function reset() { rules.value = []; transferError.value = ''; publish() }
async function importRules(file: File | null) {
  if (!file) return
  const current = ++generation
  transferError.value = ''
  try {
    if (file.size > 1024 * 1024) throw new Error(t('配置文件不得超过 1 MiB', 'The configuration file must not exceed 1 MiB'))
    const parsed = JSON.parse(await file.text())
    const validated = await apiClient.post<{request_overrides: RequestOverride[]}>('/api/providers/request-rules/validate', parsed)
    if (current !== generation) return
    rules.value = validated.request_overrides.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''}))
    publish()
  } catch(e) { if (current === generation) transferError.value = (e as Error).message }
}
async function exportRules() {
  transferError.value = ''
  try {
    publish()
    if (rules.value.some(rule => rule.error)) throw new Error(t('请先修正 JSON', 'Fix the JSON first'))
    const validated = await apiClient.post('/api/providers/request-rules/validate', {version:1, request_overrides:JSON.parse(published)})
    const url = URL.createObjectURL(new Blob([JSON.stringify(validated, null, 2)], {type:'application/json'}))
    const link = document.createElement('a'); link.href = url; link.download = 'model-request-rules.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch(e) { transferError.value = (e as Error).message }
}

</script>
<template>
  <details class="request-json ui-disclosure"><summary>{{ t('高级：自定义请求 JSON', 'Advanced: Custom request JSON') }}</summary>
    <p class="subtle">{{ t('提供商通用规则先应用，再应用模型规则。对象递归合并，数组整体替换，null 作为实际值；删除键后恢复继承。密钥继续使用独立 API Key 配置。', 'Provider-wide rules are applied before model rules. Objects merge recursively, arrays replace whole values, and null is kept as a value. Delete a key to inherit it again. API keys remain in the separate credential setting.') }}</p>
    <div v-for="(rule,index) in rules" :key="index" class="rule">
      <div class="rule-selectors"><label>{{ t('能力', 'Capability') }}<select v-model="rule.capability" class="select" @change="publish"><option value="chat">{{ t('聊天', 'Chat') }}</option><option value="embedding">Embedding</option><option value="transcription">{{ t('音频转写', 'Transcription') }}</option><option value="speaker_matching">{{ t('声纹比对', 'Speaker matching') }}</option></select></label>
      <label>{{ t('模型', 'Model') }}<input v-model="rule.model" class="input" :placeholder="t('留空：全部模型', 'Blank: all models')" @input="publish" /></label>
      <label>{{ t('请求模式', 'Request mode') }}<select v-model="rule.stream" class="select" @change="publish"><option :value="null">{{ t('全部', 'All') }}</option><option :value="true">{{ t('仅流式', 'Streaming only') }}</option><option :value="false">{{ t('仅非流式', 'Non-streaming only') }}</option></select></label></div>
      <textarea v-model="rule.draft" class="textarea json-body" rows="6" :aria-label="t('自定义请求 JSON', 'Custom request JSON')" spellcheck="false" placeholder='{"stream_options":{"include_usage":true}}' @input="publish" />
      <p v-if="rule.error" class="error-text" role="alert">{{ rule.error }}</p>
      <div class="inline-actions"><button type="button" class="button-secondary" @click="format(index)">{{ t('格式化', 'Format') }}</button><button type="button" class="button-danger" @click="rules.splice(index,1); publish()">{{ t('删除规则', 'Delete rule') }}</button></div>
    </div>
    <button type="button" class="button-secondary" @click="add">{{ t('添加请求规则', 'Add request rule') }}</button>
    <div class="transfer-actions"><div class="inline-actions"><button type="button" class="button-secondary" @click="reset">{{ t('恢复默认请求', 'Restore default request') }}</button><button type="button" class="button-secondary" @click="exportRules">{{ t('导出请求配置', 'Export request settings') }}</button></div><FilePicker :file="null" :label="t('导入请求配置', 'Import request settings')" :empty-label="t('选择 JSON 文件', 'Choose a JSON file')" accept=".json,application/json" @select="importRules" /></div>
    <p v-if="transferError" class="error-text" role="alert">{{ transferError }}</p>
    <p class="subtle">{{ t('导入替换当前请求规则，保存提供商后生效。导出仅包含请求规则，不包含凭据引用和 API Key。', 'Importing replaces the current request rules and takes effect after saving the provider. Exports contain rules only, without credential references or API keys.') }}</p>
  </details>
</template>
<style scoped>.request-json{display:grid;gap:12px}.rule{padding:12px;border:1px solid var(--color-border-default);border-radius:8px;margin:12px 0}.rule-selectors{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.rule-selectors label{display:grid;gap:5px}.json-body{font-family:var(--font-ui-mono);width:100%}.transfer-actions{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-sm);justify-content:space-between}</style>
